from datetime import datetime
from typing import Any, Sequence

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.database import get_db
from api.db.models import Run, Step, Decision, Evidence
from xray.models import RunInput, RunComplete, Step as StepInput, Decision as DecisionInput
from xray.sampler import DecisionSampler
from xray.config import MAX_DECISIONS_PER_STEP, MAX_EVIDENCE_PER_STEP

router = APIRouter(prefix="/v1", tags=["ingest"])

# Implements ADR-001: Server-side sampling with accurate stats
_sampler = DecisionSampler()


class CreateRunResponse(BaseModel):
    run_id: str


class SamplingSummary(BaseModel):
    """Implements ADR-001: Sampling transparency"""
    total: int
    kept: int
    sampled: bool


class RecordStepResponse(BaseModel):
    step_id: str
    stats: dict[str, Any] | None = None
    sampling_summary: SamplingSummary | None = None


class CompleteRunResponse(BaseModel):
    run_id: str
    status: str
    completed_at: datetime


@router.post("/runs", status_code=status.HTTP_201_CREATED, response_model=CreateRunResponse)
async def create_run(run_input: RunInput, db: AsyncSession = Depends(get_db)) -> CreateRunResponse:
    run = Run(
        pipeline_type=run_input.pipeline_type,
        name=run_input.name,
        input_context=run_input.input,
        meta_data=run_input.metadata,
        status="running",
        started_at=datetime.utcnow()
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return CreateRunResponse(run_id=run.id)


@router.post("/runs/{run_id}/steps", status_code=status.HTTP_201_CREATED, response_model=RecordStepResponse)
async def record_step(run_id: str, step_input: StepInput, db: AsyncSession = Depends(get_db)) -> RecordStepResponse:
    """
    Record a pipeline step with decisions and evidence.
    
    Implements PRD Section 11: Input validation for sizes and evidence
    """
    # Input validation - PRD Section 11 (Storage guardrails)
    if step_input.decisions and len(step_input.decisions) > MAX_DECISIONS_PER_STEP:
        raise HTTPException(
            status_code=413,
            detail=f"Too many decisions: {len(step_input.decisions)} exceeds maximum of {MAX_DECISIONS_PER_STEP}"
        )
    
    if step_input.evidence and len(step_input.evidence) > MAX_EVIDENCE_PER_STEP:
        raise HTTPException(
            status_code=413,
            detail=f"Too many evidence items: {len(step_input.evidence)} exceeds maximum of {MAX_EVIDENCE_PER_STEP}"
        )
    
    result = await db.execute(select(Run).where(Run.id == run_id))
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    
    count_result = await db.execute(select(func.count(Step.id)).where(Step.run_id == run_id))
    seq = count_result.scalar() or 0
    
    # Implements ADR-001: Compute stats and sampling on server-side using canonical sampler
    stats: dict[str, Any] | None = None
    sdk_decisions: Sequence[DecisionInput] | None = step_input.decisions
    if sdk_decisions:
        from xray.models import Decision as SDKDecision
        normalized: list[SDKDecision] = []
        for idx, d in enumerate(sdk_decisions):
            md = dict(d.metadata or {})
            md.setdefault("sequence", idx)
            normalized.append(
                SDKDecision(
                    candidate_id=d.candidate_id,
                    decision_type=d.decision_type,
                    reason=d.reason,
                    score=d.score,
                    metadata=md,
                    timestamp=d.timestamp
                )
            )
        computed_stats = _sampler.compute_stats(normalized)
        stats = dict(computed_stats)
        sampled_sdk_decisions, was_sampled = _sampler.sample(normalized)
    else:
        sampled_sdk_decisions, was_sampled = [], False
    
    step = Step(
        run_id=run_id,
        step_name=step_input.name,
        sequence_order=seq,
        input_data=step_input.input,
        output_data=step_input.output,
        config=step_input.config,
        reasoning=step_input.reasoning,
        stats=stats,
        started_at=datetime.utcnow(),
        completed_at=datetime.utcnow()
    )
    db.add(step)
    await db.flush()
    
    # Implements ADR-001: Server-side sampling with sampling_summary
    created_decisions: list[Decision] = []
    sampling_summary: SamplingSummary | None = None
    
    if sdk_decisions:
        original_count = len(sdk_decisions)
        decisions_to_store = [
            DecisionInput(
                candidate_id=d.candidate_id,
                decision_type=d.decision_type,
                reason=d.reason,
                score=d.score,
                metadata=d.metadata,
                timestamp=d.timestamp
            )
            for d in sampled_sdk_decisions
        ] if sampled_sdk_decisions else []
        
        sampling_summary = SamplingSummary(
            total=original_count,
            kept=len(decisions_to_store),
            sampled=was_sampled
        )
        
        for idx, d in enumerate(decisions_to_store):
            decision = Decision(
                step_id=step.id,
                candidate_id=d.candidate_id,
                decision_type=d.decision_type,
                reason=d.reason,
                score=d.score,
                sequence_order=idx,
                meta_data=d.metadata,
                created_at=d.timestamp or datetime.utcnow()
            )
            db.add(decision)
            created_decisions.append(decision)

    if step_input.evidence:
        if not created_decisions:
            raise HTTPException(
                status_code=400,
                detail="Evidence provided but no decisions to attach to"
            )
        if len(step_input.evidence) > len(created_decisions):
            raise HTTPException(
                status_code=400,
                detail="Evidence count exceeds decisions; provide one evidence per decision"
            )
        for ev, decision in zip(step_input.evidence, created_decisions):
            db.add(Evidence(
                decision_id=decision.id,
                evidence_type=ev.evidence_type,
                data=ev.data,
                created_at=ev.timestamp or datetime.utcnow()
            ))
    
    await db.commit()
    await db.refresh(step)
    return RecordStepResponse(step_id=step.id, stats=stats, sampling_summary=sampling_summary)


@router.patch("/runs/{run_id}", response_model=CompleteRunResponse)
async def complete_run(run_id: str, run_complete: RunComplete, db: AsyncSession = Depends(get_db)) -> CompleteRunResponse:
    result = await db.execute(select(Run).where(Run.id == run_id))
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    
    run.output_result = run_complete.result
    run.status = run_complete.status
    run.completed_at = datetime.utcnow()
    await db.commit()
    
    return CompleteRunResponse(run_id=run_id, status=run.status, completed_at=run.completed_at)


# Removed _compute_stats - now using xray.sampler.DecisionSampler.compute_stats()
# This eliminates code duplication and maintains single source of truth (ADR-001)
