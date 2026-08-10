from __future__ import annotations

import json
import re
from typing import Any

from langgraph.graph import END, START, StateGraph

from pk_agent.domain.research import Claim, Report
from pk_agent.graph.state import ResearchState
from pk_agent.harness.bus import EventBus
from pk_agent.harness.prompt import PromptAssembler, has_prompt_injection_signal
from pk_agent.harness.registry import ToolRegistry, ToolRisk
from pk_agent.harness.router import ModelRouter
from pk_agent.literature.models import LiteratureRecord
from pk_agent.providers.base import KnowledgeProvider
from pk_agent.providers.contract import KnowledgeHit
from pk_agent.providers.errors import KnowledgeTimeout, KnowledgeUnavailable

CLAIM_TEXT_MAX_CHARS = 300
QUERY_MAX_CHARS = 1000
EXTERNAL_MIN_HITS = 3
EXTERNAL_MAX_SOURCES = 8
INGEST_MAX_RECORDS = 2

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _parse_model_output(raw: str) -> tuple[str, list[Claim]]:
    """Defensively parse the model's JSON answer; fall back to plain text."""
    text = _FENCE_RE.sub("", raw.strip()).strip()
    try:
        body = json.loads(text)
    except ValueError:
        return raw.strip(), []
    if not isinstance(body, dict):
        return raw.strip(), []
    answer = str(body.get("answer") or "").strip() or raw.strip()
    claims: list[Claim] = []
    for item in body.get("claims") or []:
        if not isinstance(item, dict):
            continue
        claim_text = str(item.get("text") or "")[:CLAIM_TEXT_MAX_CHARS].strip()
        if not claim_text:
            continue
        chunk_ids = [str(cid) for cid in item.get("chunk_ids") or [] if cid]
        claims.append(Claim(text=claim_text, chunk_ids=chunk_ids))
    return answer, claims


def _dedupe_external(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dedupe by DOI, then normalized URL, then source_id; cap the total."""
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for record in records:
        doi = (record.get("doi") or "").strip().lower()
        url = (record.get("url") or "").strip().lower().rstrip("/")
        key = doi or url or f"{record.get('source_type')}:{record.get('external_id')}"
        if key in seen:
            continue
        seen.add(key)
        merged.append(record)
        if len(merged) >= EXTERNAL_MAX_SOURCES:
            break
    return merged


def build_research_graph(
    checkpointer: Any,
    model_router: ModelRouter,
    event_bus: EventBus,
    max_budget_calls: int,
    knowledge_provider: KnowledgeProvider,
    tool_registry: ToolRegistry | None = None,
):
    prompt_assembler = PromptAssembler()

    async def publish(task_id: str, event: dict[str, Any]) -> None:
        await event_bus.publish(task_id, event)

    async def normalize_node(state: ResearchState) -> ResearchState:
        task_id = state["task_id"]
        query = " ".join(state["input"].split())[:QUERY_MAX_CHARS]
        await publish(task_id, {"type": "started", "task_id": task_id})
        return {
            "status": "running",
            "budget_calls": 0,
            "retrieval_rounds": 0,
            "normalized_query": query,
            "messages": [{"role": "system", "content": "research graph initialized"}],
        }

    async def retrieve_internal_node(state: ResearchState) -> ResearchState:
        task_id = state["task_id"]
        try:
            hits = await knowledge_provider.search(
                state["normalized_query"], tenant_id=state["tenant_id"], limit=5
            )
        except (KnowledgeUnavailable, KnowledgeTimeout) as exc:
            # ADR 004: retrieval outage degrades to empty context; auth errors
            # are misconfiguration and propagate to fail the task.
            await publish(task_id, {"type": "knowledge_degraded", "reason": str(exc)})
            return {"knowledge_hits": [], "knowledge_error": str(exc)}

        await publish(task_id, {"type": "knowledge_retrieved", "hits": len(hits)})
        return {"knowledge_hits": [hit.model_dump() for hit in hits]}

    def route_after_internal(state: ResearchState) -> str:
        if not state.get("allow_external") or tool_registry is None:
            return "generate_grounded_answer"
        if len(state.get("knowledge_hits", [])) >= EXTERNAL_MIN_HITS:
            return "generate_grounded_answer"
        return "retrieve_external"

    async def retrieve_external_node(state: ResearchState) -> ResearchState:
        task_id = state["task_id"]
        assert tool_registry is not None  # guaranteed by route_after_internal
        result = await tool_registry.call(
            "literature_search",
            {"query": state["normalized_query"], "limit": 5},
            max_risk=ToolRisk.L1,
        )
        rounds = state.get("retrieval_rounds", 0) + 1
        if not result.ok:
            await publish(
                task_id, {"type": "external_degraded", "reason": result.error_message}
            )
            return {
                "external_sources": [],
                "external_error": result.error_message or "literature_search failed",
                "retrieval_rounds": rounds,
            }

        data = result.data or {}
        records = _dedupe_external(
            [r for r in data.get("records") or [] if isinstance(r, dict)]
        )
        source_errors = [str(e) for e in data.get("errors") or []]
        if source_errors:
            await publish(
                task_id,
                {"type": "external_degraded", "reason": "; ".join(source_errors)},
            )
        await publish(task_id, {"type": "external_retrieved", "sources": len(records)})
        update: ResearchState = {
            "external_sources": records,
            "retrieval_rounds": rounds,
        }
        if source_errors:
            update["external_error"] = "; ".join(source_errors)
        return update

    async def ingest_external_node(state: ResearchState) -> ResearchState:
        task_id = state["task_id"]
        if not state.get("ingest_external") or tool_registry is None:
            return {}
        sources = state.get("external_sources", [])
        candidates = [s for s in sources if s.get("pdf_url")][:INGEST_MAX_RECORDS]
        if not candidates:
            return {}

        updated = list(sources)
        failures: list[str] = []
        for record in candidates:
            result = await tool_registry.call(
                "literature_ingest",
                {"record": record, "tenant_id": state["tenant_id"]},
                max_risk=ToolRisk.L2,
            )
            if result.ok:
                knowledge_id = (result.data or {}).get("knowledge_id")
                index = sources.index(record)
                updated[index] = {
                    **updated[index],
                    "extra": {**record.get("extra", {}), "knowledge_id": knowledge_id},
                }
                await publish(
                    task_id,
                    {
                        "type": "external_ingested",
                        "source_id": record.get("source_id")
                        or f"{record.get('source_type')}:{record.get('external_id')}",
                        "knowledge_id": knowledge_id,
                    },
                )
            else:
                failures.append(
                    f"{record.get('external_id')}: {result.error_message}"
                )
        update: ResearchState = {"external_sources": updated}
        if failures:
            note = "; ".join(failures)
            await publish(task_id, {"type": "external_degraded", "reason": note})
            update["external_error"] = note
        return update

    async def generate_grounded_answer_node(state: ResearchState) -> ResearchState:
        task_id = state["task_id"]
        budget_calls = state.get("budget_calls", 0)
        if budget_calls >= max_budget_calls:
            await publish(task_id, {"type": "failed", "reason": "model budget exceeded"})
            return {"status": "failed", "result": "model budget exceeded"}

        hits = [KnowledgeHit(**hit) for hit in state.get("knowledge_hits", [])]
        external = [
            LiteratureRecord(**record) for record in state.get("external_sources", [])
        ]
        suspicious_evidence = sum(
            has_prompt_injection_signal(hit.snippet) for hit in hits
        ) + sum(
            has_prompt_injection_signal(f"{record.title}\n{record.abstract}")
            for record in external
        )
        if suspicious_evidence:
            await publish(
                task_id,
                {"type": "prompt_injection_detected", "evidence_items": suspicious_evidence},
            )
        prompt = prompt_assembler.build_answer_prompt(
            state["normalized_query"], hits, external or None
        )
        raw = await model_router.generate(prompt, capability="answer")
        await publish(task_id, {"type": "model_called", "capability": "answer"})

        answer, claims = _parse_model_output(raw)
        report = Report(answer=answer, claims=claims)
        return {
            "budget_calls": budget_calls + 1,
            "report": report.model_dump(),
            "result": answer,
        }

    async def citation_gate_node(state: ResearchState) -> ResearchState:
        task_id = state["task_id"]
        hits = state.get("knowledge_hits", [])
        external = state.get("external_sources", [])
        known_chunk_ids = {hit["chunk_id"] for hit in hits}
        known_source_ids = {
            f"{record.get('source_type')}:{record.get('external_id')}"
            for record in external
        }
        known_ids = known_chunk_ids | known_source_ids
        report = Report(**state["report"])

        dropped = 0
        verified: list[Claim] = []
        for claim in report.claims:
            valid_ids = [cid for cid in claim.chunk_ids if cid in known_ids]
            dropped += len(claim.chunk_ids) - len(valid_ids)
            if valid_ids:
                verified.append(Claim(text=claim.text, chunk_ids=valid_ids))

        notes = list(report.notes)
        if not verified and hits:
            top = hits[0]
            verified = [
                Claim(
                    text=f"Retrieved evidence from "
                    f"{top['source'].get('title') or top['source'].get('document_id')}: "
                    f"{top['snippet'][:CLAIM_TEXT_MAX_CHARS]}",
                    chunk_ids=[top["chunk_id"]],
                )
            ]
            notes.append("model produced no valid claims; synthesized from top hit")
        elif not verified and external:
            top = external[0]
            source_id = f"{top.get('source_type')}:{top.get('external_id')}"
            verified = [
                Claim(
                    text=f"External literature: {top.get('title', '')} "
                    f"({top.get('venue') or top.get('source_type')}, "
                    f"{top.get('published') or 'n.d.'}) — "
                    f"{(top.get('abstract') or '')[:CLAIM_TEXT_MAX_CHARS]}",
                    chunk_ids=[source_id],
                )
            ]
            notes.append(
                "model produced no valid claims; synthesized from top external source"
            )
        if not hits and not external:
            notes.append("no evidence retrieved; answer is ungrounded")

        grounded = bool(verified)
        gated = report.model_copy(
            update={
                "claims": verified,
                "grounded": grounded,
                "dropped_citations": report.dropped_citations + dropped,
                "notes": notes,
            }
        )
        await publish(
            task_id,
            {
                "type": "citation_gate",
                "claims": len(verified),
                "dropped": dropped,
                "grounded": grounded,
            },
        )
        return {"report": gated.model_dump()}

    async def finalize_node(state: ResearchState) -> ResearchState:
        task_id = state["task_id"]
        needs_approval = state.get("require_approval") and not state.get("approved")
        status = "paused" if needs_approval else "completed"
        await publish(task_id, {"type": status, "task_id": task_id})
        return {
            "status": status,
            "messages": [{"role": "assistant", "content": state.get("result", "")}],
        }

    graph = StateGraph(ResearchState)
    graph.add_node("normalize", normalize_node)
    graph.add_node("retrieve_internal", retrieve_internal_node)
    graph.add_node("retrieve_external", retrieve_external_node)
    graph.add_node("ingest_external", ingest_external_node)
    graph.add_node("generate_grounded_answer", generate_grounded_answer_node)
    graph.add_node("citation_gate", citation_gate_node)
    graph.add_node("finalize", finalize_node)
    graph.add_edge(START, "normalize")
    graph.add_edge("normalize", "retrieve_internal")
    graph.add_conditional_edges(
        "retrieve_internal",
        route_after_internal,
        ["retrieve_external", "generate_grounded_answer"],
    )
    graph.add_edge("retrieve_external", "ingest_external")
    graph.add_edge("ingest_external", "generate_grounded_answer")
    graph.add_edge("generate_grounded_answer", "citation_gate")
    graph.add_edge("citation_gate", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer)
