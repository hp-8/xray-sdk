"""
SQLAlchemy ORM Models for X-Ray API.

Data Model:
    Run → Step → Decision → Evidence

Decisions are the primary event type, capturing time-ordered
decision events about candidates at each step.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, String, Float, Integer, DateTime, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from api.db.database import Base


def generate_uuid() -> str:
    """Generate a UUID string."""
    return str(uuid.uuid4())


class Run(Base):
    """
    A pipeline run represents a complete execution of a multi-step process.
    
    Example: One competitor selection run for a single product.
    """
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=generate_uuid
    )
    pipeline_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    input_context: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    output_result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="running", index=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    meta_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, name="metadata", nullable=True)  # Column name is "metadata" in DB

    # Relationships
    steps: Mapped[list["Step"]] = relationship(
        "Step", back_populates="run", cascade="all, delete-orphan"
    )


class Step(Base):
    """
    A step in a pipeline run.
    
    Each step is a decision point that captures inputs, outputs,
    configuration, and generates decision events.
    """
    __tablename__ = "steps"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=generate_uuid
    )
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    step_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    sequence_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    output_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    config: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    run: Mapped["Run"] = relationship("Run", back_populates="steps")
    decisions: Mapped[list["Decision"]] = relationship(
        "Decision", back_populates="step", cascade="all, delete-orphan"
    )


class Decision(Base):
    """
    A decision event about a candidate.
    
    Decisions are time-ordered events. One candidate can have multiple
    decisions across different steps (e.g., rejected in step 1, 
    reconsidered and accepted in step 2).
    """
    __tablename__ = "decisions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=generate_uuid
    )
    step_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("steps.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    decision_type: Mapped[str] = mapped_column(
        String(20), nullable=False, index=True
    )  # accepted, rejected, pending
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    sequence_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    meta_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, name="metadata", nullable=True)  # Column name is "metadata" in DB
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )

    # Relationships
    step: Mapped["Step"] = relationship("Step", back_populates="decisions")
    evidence: Mapped[list["Evidence"]] = relationship(
        "Evidence", back_populates="decision", cascade="all, delete-orphan"
    )


class Evidence(Base):
    """
    Evidence or context attached to a decision.
    
    Used to store additional data like LLM outputs, API responses,
    or computed intermediate values without bloating the decision record.
    """
    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=generate_uuid
    )
    decision_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("decisions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    evidence_type: Mapped[str] = mapped_column(String(100), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=datetime.utcnow
    )

    # Relationships
    decision: Mapped["Decision"] = relationship("Decision", back_populates="evidence")

