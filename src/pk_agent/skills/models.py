from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SkillStatus = Literal["candidate", "testing", "approved", "deprecated"]

# Legal status transitions. Note: nothing transitions INTO "approved" except
# the approve endpoint (human action); generation code can only ever create
# "candidate" records.
ALLOWED_TRANSITIONS: dict[SkillStatus, frozenset[SkillStatus]] = {
    "candidate": frozenset({"testing", "deprecated"}),
    "testing": frozenset({"approved", "deprecated"}),
    "approved": frozenset({"deprecated"}),
    "deprecated": frozenset(),
}

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
_SKILL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class TriggerCondition(BaseModel):
    """Deterministic matcher — no LLM involvement at runtime."""

    model_config = ConfigDict(extra="forbid")

    keywords: list[str] = Field(default_factory=list, max_length=20)
    regex_patterns: list[str] = Field(default_factory=list, max_length=10)
    priority: int = 0

    @field_validator("regex_patterns")
    @classmethod
    def _patterns_compile(cls, value: list[str]) -> list[str]:
        for pattern in value:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"invalid regex pattern {pattern!r}: {exc}") from exc
        return value


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1, max_length=100)
    query: str = Field(min_length=1, max_length=1000)
    # Deterministic assertions (no LLM judge required):
    expected_answer_contains: list[str] = Field(default_factory=list, max_length=10)
    expect_skill_matched: str | None = None
    expect_model_blocks: list[str] = Field(default_factory=list, max_length=5)


class SkillRecord(BaseModel):
    """One version of a reusable, verified procedure (docx §14 fields)."""

    model_config = ConfigDict(extra="forbid")

    skill_id: str
    version: str
    status: SkillStatus = "candidate"
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=1000)
    trigger_conditions: list[TriggerCondition] = Field(default_factory=list)
    procedure: str = Field(default="", max_length=8000)
    required_tools: list[str] = Field(default_factory=list, max_length=20)
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    source_task_ids: list[str] = Field(default_factory=list, max_length=50)
    # Model-suggested cases from distillation, persisted so the evaluate
    # endpoint/UI can run them later without re-distilling.
    suggested_eval_cases: list[EvalCase] = Field(default_factory=list, max_length=5)
    eval_pass_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    approved_by: str | None = None
    approved_at: datetime | None = None
    parent_version: str | None = None

    @field_validator("skill_id")
    @classmethod
    def _skill_id_shape(cls, value: str) -> str:
        if not _SKILL_ID_RE.match(value):
            raise ValueError(
                "skill_id must be lowercase alphanumerics and dashes (<=64 chars)"
            )
        return value

    @field_validator("version")
    @classmethod
    def _version_shape(cls, value: str) -> str:
        if not _SEMVER_RE.match(value):
            raise ValueError("version must be semver (e.g. 1.0.0)")
        return value

    @field_validator("procedure")
    @classmethod
    def _procedure_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("procedure must not be empty")
        return value


class EvalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    total_cases: int = Field(ge=0)
    passed_cases: int = Field(ge=0)
    eval_pass_rate: float = Field(ge=0.0, le=1.0)
    details: list[dict[str, Any]] = Field(default_factory=list)
    compared_to_version: str | None = None
    regression_detected: bool = False
    judge_notes: list[str] = Field(default_factory=list)


class SkillCandidate(BaseModel):
    """A model-distilled proposal. Never approved by generation code."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    derived_from_task_id: str
    proposed_record: SkillRecord
    eval_cases: list[EvalCase] = Field(default_factory=list)
    eval_result: EvalResult | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    created_by: str | None = None
