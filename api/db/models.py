import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, String, Float, Integer, DateTime, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    pipeline_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    input_context: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    output_result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running", index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    meta_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, name="metadata")

    steps: Mapped[list["Step"]] = relationship("Step", back_populates="run", cascade="all, delete-orphan")


class Step(Base):
    __tablename__ = "steps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True)
    step_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    sequence_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    output_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    config: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    reasoning: Mapped[str | None] = mapped_column(Text)
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)

    run: Mapped["Run"] = relationship("Run", back_populates="steps")
    decisions: Mapped[list["Decision"]] = relationship("Decision", back_populates="step", cascade="all, delete-orphan")


class Decision(Base):
    """One candidate can have multiple decisions across steps (e.g. rejected then accepted)."""
    __tablename__ = "decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    step_id: Mapped[str] = mapped_column(String(36), ForeignKey("steps.id", ondelete="CASCADE"), nullable=False, index=True)
    candidate_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    decision_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    reason: Mapped[str | None] = mapped_column(String(255), index=True)
    score: Mapped[float | None] = mapped_column(Float)
    sequence_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    meta_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, name="metadata")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    step: Mapped["Step"] = relationship("Step", back_populates="decisions")
    evidence: Mapped[list["Evidence"]] = relationship("Evidence", back_populates="decision", cascade="all, delete-orphan")


class Evidence(Base):
    """Heavy context (LLM responses, etc) - kept separate from decisions."""
    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    decision_id: Mapped[str] = mapped_column(String(36), ForeignKey("decisions.id", ondelete="CASCADE"), nullable=False, index=True)
    evidence_type: Mapped[str] = mapped_column(String(100), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    decision: Mapped["Decision"] = relationship("Decision", back_populates="evidence")
