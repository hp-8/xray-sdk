from datetime import datetime
from typing import Any, Literal
from uuid import UUID
from pydantic import BaseModel, Field


class Decision(BaseModel):
    candidate_id: str
    decision_type: Literal["accepted", "rejected", "pending"]
    reason: str | None = None
    score: float | None = None
    metadata: dict[str, Any] | None = None
    timestamp: datetime | None = None


class Evidence(BaseModel):
    evidence_type: str
    data: dict[str, Any]
    timestamp: datetime | None = None


class Step(BaseModel):
    name: str
    input: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    config: dict[str, Any] | None = None
    stats: dict[str, Any] | None = None
    decisions: list[Decision] | None = None
    evidence: list[Evidence] | None = None
    reasoning: str | None = None


class RunInput(BaseModel):
    pipeline_type: str
    name: str | None = None
    input: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


class RunComplete(BaseModel):
    result: dict[str, Any] | None = None
    status: Literal["completed", "failed", "cancelled"] = "completed"


# response models

class RunResponse(BaseModel):
    id: UUID
    pipeline_type: str
    name: str | None
    input_context: dict[str, Any] | None
    output_result: dict[str, Any] | None
    status: str
    started_at: datetime
    completed_at: datetime | None
    metadata: dict[str, Any] | None


class StepResponse(BaseModel):
    id: UUID
    run_id: UUID
    step_name: str
    sequence_order: int
    input_data: dict[str, Any] | None
    output_data: dict[str, Any] | None
    config: dict[str, Any] | None
    reasoning: str | None
    stats: dict[str, Any] | None
    started_at: datetime
    completed_at: datetime | None


class DecisionResponse(BaseModel):
    id: UUID
    step_id: UUID
    candidate_id: str
    decision_type: str
    reason: str | None
    score: float | None
    sequence_order: int
    metadata: dict[str, Any] | None
    created_at: datetime


class StepStats(BaseModel):
    input_count: int = 0
    output_count: int = 0
    rejection_rate: float = 0.0
    rejection_reasons: dict[str, int] = Field(default_factory=dict)
