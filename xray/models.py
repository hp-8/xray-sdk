"""
X-Ray SDK Pydantic Models

These models define the structure for capturing decision context in multi-step pipelines.
"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class Decision(BaseModel):
    """
    A decision event about a candidate.
    
    Decisions are time-ordered events that capture why a candidate was
    accepted, rejected, or is pending at a particular step.
    """
    candidate_id: str = Field(..., description="Unique identifier for the candidate being evaluated")
    decision_type: Literal["accepted", "rejected", "pending"] = Field(
        ..., description="The type of decision made"
    )
    reason: str | None = Field(None, description="Why this decision was made")
    score: float | None = Field(None, description="Numeric score if applicable (e.g., relevance score)")
    metadata: dict[str, Any] | None = Field(None, description="Additional context for this decision")
    timestamp: datetime | None = Field(None, description="When the decision was made")

    class Config:
        json_schema_extra = {
            "example": {
                "candidate_id": "product-123",
                "decision_type": "rejected",
                "reason": "price_exceeds_threshold",
                "score": None,
                "metadata": {"price": 150, "threshold": 100}
            }
        }


class Evidence(BaseModel):
    """
    Evidence or context attached to a decision or step.
    
    Used to store additional data like LLM outputs, API responses,
    or computed intermediate values.
    """
    evidence_type: str = Field(..., description="Type of evidence (e.g., 'llm_output', 'api_response')")
    data: dict[str, Any] = Field(..., description="The evidence data")
    timestamp: datetime | None = Field(None, description="When the evidence was captured")

    class Config:
        json_schema_extra = {
            "example": {
                "evidence_type": "llm_output",
                "data": {"model": "gpt-4", "tokens": 150, "response": "..."}
            }
        }


class Step(BaseModel):
    """
    A step in a pipeline run.
    
    Each step represents a decision point that captures inputs, outputs,
    configuration, decisions made, and reasoning.
    """
    name: str = Field(..., description="Name of the step (e.g., 'filtering', 'ranking')")
    input: dict[str, Any] | None = Field(None, description="Input data for this step")
    output: dict[str, Any] | None = Field(None, description="Output data from this step")
    config: dict[str, Any] | None = Field(None, description="Configuration/thresholds used")
    decisions: list[Decision] | None = Field(None, description="Decision events (time-ordered)")
    evidence: list[Evidence] | None = Field(None, description="Additional evidence/context")
    reasoning: str | None = Field(None, description="Human-readable explanation of what happened")

    class Config:
        json_schema_extra = {
            "example": {
                "name": "filtering",
                "input": {"candidate_count": 5000},
                "output": {"passed_count": 30},
                "config": {"price_threshold": 100, "min_rating": 3.5},
                "reasoning": "Applied price cap ($100) and minimum rating (3.5) filters"
            }
        }


class RunInput(BaseModel):
    """
    Input for creating a new pipeline run.
    """
    pipeline_type: str = Field(..., description="Type of pipeline (e.g., 'competitor_selection')")
    name: str | None = Field(None, description="Optional name for this run")
    input: dict[str, Any] | None = Field(None, description="Input context for the run")
    metadata: dict[str, Any] | None = Field(None, description="Additional metadata")

    class Config:
        json_schema_extra = {
            "example": {
                "pipeline_type": "competitor_selection",
                "name": "find_competitor_product-123",
                "input": {"product_id": "product-123", "title": "Laptop Stand"}
            }
        }


class RunComplete(BaseModel):
    """
    Data for completing a pipeline run.
    """
    result: dict[str, Any] | None = Field(None, description="Final result of the run")
    status: Literal["completed", "failed", "cancelled"] = Field(
        "completed", description="Final status of the run"
    )


# Response models for API

class RunResponse(BaseModel):
    """Response model for a run."""
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
    """Response model for a step."""
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
    """Response model for a decision."""
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
    """Pre-computed statistics for a step."""
    input_count: int = Field(0, description="Total number of candidates evaluated")
    output_count: int = Field(0, description="Number of accepted decisions")
    rejection_rate: float = Field(0.0, description="Percentage of candidates rejected")
    rejection_reasons: dict[str, int] = Field(
        default_factory=dict, description="Count of rejections per reason"
    )

