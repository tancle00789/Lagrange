from __future__ import annotations

import json
import re
from typing import Any

from pk_agent.harness.parse import load_json_object, strip_model_noise
from pk_agent.harness.prompt import sanitize_untrusted_text
from pk_agent.harness.router import ModelRouter
from pk_agent.skills.models import EvalCase, SkillRecord, TriggerCondition
from pk_agent.skills.prompts import build_distill_prompt


class DistillError(Exception):
    """Distillation could not produce a valid candidate."""


def normalize_skill_id(raw: str) -> str:
    """Coerce model-proposed ids into the lowercase-dash shape.

    Local models reliably answer ``kv_cache_simulation`` (underscores, after
    the block names in the task record) no matter how the format instruction
    is phrased; normalizing is cheaper than rejecting.
    """
    return re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")


def summarize_task_record(record: dict[str, Any], *, max_chars: int = 4000) -> str:
    """Compact, untrusted-text-sanitized view of a completed task."""
    summary = {
        "input": sanitize_untrusted_text(str(record.get("input", "")), max_chars=1000),
        "result": sanitize_untrusted_text(str(record.get("result", "")), max_chars=1500),
        "model_runs": [
            {
                "block_name": str(run.get("block_name", "")),
                "status": str(run.get("status", "")),
                "metrics": run.get("metrics") or {},
            }
            for run in (record.get("model_runs") or [])[:5]
        ],
        "external_source_count": len(record.get("external_sources") or []),
        "knowledge_hit_count": len(record.get("knowledge_hits") or []),
    }
    return json.dumps(summary, ensure_ascii=False)[:max_chars]


def next_version(existing_versions: list[str]) -> str:
    """Bump the minor version of the highest existing semver (1.0.0 if none)."""
    best = (0, 0, 0)
    for version in existing_versions:
        match = re.match(r"^(\d+)\.(\d+)\.(\d+)$", version)
        if match:
            candidate = (int(match[1]), int(match[2]), int(match[3]))
            best = max(best, candidate)
    major, minor, patch = best
    return f"{major}.{minor + 1}.{patch}" if best != (0, 0, 0) else "1.0.0"


async def distill_candidate_record(
    task_record: dict[str, Any],
    model_router: ModelRouter,
    *,
    available_tools: list[str],
    existing_skill_ids: list[str],
    existing_versions: list[str],
    source_task_id: str,
) -> tuple[SkillRecord, list[EvalCase]] | None:
    """Distill a candidate skill from a completed task.

    Returns None when the model judges the task not generalizable. The
    returned record is ALWAYS status="candidate" — this code path has no
    way to produce an approved skill. The accompanying eval cases are
    model-suggested starting points for the offline evaluator; every case
    is pinned to this skill (expect_skill_matched) regardless of what the
    model claimed.
    """
    prompt = build_distill_prompt(
        summarize_task_record(task_record), available_tools, existing_skill_ids
    )
    raw = await model_router.generate(prompt, capability="distill")
    body = load_json_object(strip_model_noise(raw))
    if body is None:
        raise DistillError("distill output is not a JSON object")
    if body.get("worth_saving") is not True:
        return None

    proposed_tools = body.get("required_tools") or []
    unknown = [name for name in proposed_tools if name not in available_tools]
    if unknown:
        raise DistillError(f"distill proposed unknown tools: {unknown}")

    try:
        conditions = [
            TriggerCondition(**item)
            for item in (body.get("trigger_conditions") or [])
            if isinstance(item, dict)
        ]
        record = SkillRecord(
            skill_id=normalize_skill_id(str(body.get("skill_id") or "")),
            version=next_version(existing_versions),
            status="candidate",
            name=str(body.get("name") or "")[:200],
            description=str(body.get("description") or "")[:1000],
            trigger_conditions=conditions,
            procedure=str(body.get("procedure") or "")[:8000],
            required_tools=[str(name) for name in proposed_tools][:20],
            source_task_ids=[source_task_id],
        )
    except ValueError as exc:
        raise DistillError(f"distill output failed schema validation: {exc}") from exc

    eval_cases: list[EvalCase] = []
    for index, item in enumerate(body.get("eval_cases") or []):
        if not isinstance(item, dict):
            continue
        try:
            eval_cases.append(
                EvalCase(
                    case_id=str(item.get("case_id") or f"case-{index + 1}")[:100],
                    query=str(item.get("query") or ""),
                    expected_answer_contains=[
                        str(needle)
                        for needle in (item.get("expected_answer_contains") or [])
                    ][:10],
                    expect_skill_matched=record.skill_id,
                    expect_model_blocks=[
                        str(name) for name in (item.get("expect_model_blocks") or [])
                    ][:5],
                )
            )
        except ValueError:
            continue  # drop malformed cases; the record stands on its own
    return record, eval_cases[:5]
