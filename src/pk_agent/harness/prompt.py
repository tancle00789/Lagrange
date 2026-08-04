from __future__ import annotations

from pk_agent.literature.models import LiteratureRecord
from pk_agent.providers.contract import KnowledgeHit

ANSWER_FORMAT_INSTRUCTION = """\
You are a grounded research assistant. Answer the question using ONLY the
evidence below. Every factual statement must be traceable to an evidence item
via its chunk_id (internal snippets) or source_id (external literature
metadata). If the evidence is insufficient, say so explicitly in the answer
and return no claims.

Respond with STRICT JSON only, no markdown fences, no commentary:
{"answer": "<concise answer>",
 "claims": [{"text": "<single factual claim>",
             "chunk_ids": ["<chunk_id or source_id>", ...]}, ...]}
"""


class PromptAssembler:
    def build_answer_prompt(
        self,
        query: str,
        hits: list[KnowledgeHit],
        external: list[LiteratureRecord] | None = None,
    ) -> str:
        if hits:
            internal = "\n\n".join(
                f"[chunk_id={hit.chunk_id}] (score={hit.score:.3f}, "
                f"source={hit.source.title or hit.source.document_id})\n{hit.snippet}"
                for hit in hits
            )
        else:
            internal = "(no internal evidence retrieved)"

        sections = [f"Internal evidence:\n{internal}"]
        if external:
            external_block = "\n\n".join(
                f"[source_id={record.source_id}] "
                f"({record.venue or record.source_type}"
                f"{', ' + record.published if record.published else ''})\n"
                f"{record.title} — {record.abstract}"
                for record in external
            )
            sections.append(f"External literature (metadata only):\n{external_block}")

        return (
            f"{ANSWER_FORMAT_INSTRUCTION}\n\n"
            f"Question: {query}\n\n"
            + "\n\n".join(sections)
            + "\n"
        )
