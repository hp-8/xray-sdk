"""
Ingest API Endpoints

Endpoints for recording runs, steps, and decisions.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.database import get_db
from api.db.models import Run, Step, Decision, Evidence
from xray.models import (
    RunInput, RunComplete, Step as StepInput,
    RunResponse, StepResponse, StepStats
)

router = APIRouter(prefix="/v1", tags=["ingest"])


@router.post("/runs", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_run(
    run_input: RunInput,
    db: AsyncSession = Depends(get_db)
) -> dict:
    """
    Create a new pipeline run.
    
    Returns the run_id to use for subsequent step recordings.
    """
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
    
    return {"run_id": run.id}


@router.post("/runs/{run_id}/steps", response_model=dict, status_code=status.HTTP_201_CREATED)
async def record_step(
    run_id: str,
    step_input: StepInput,
    db: AsyncSession = Depends(get_db)
) -> dict:
    """
    Record a step in a pipeline run.
    
    Includes decisions and evidence if provided.
    """
    # Verify run exists
    result = await db.execute(select(Run).where(Run.id == run_id))
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} not found"
        )
    
    # Get next sequence order
    count_result = await db.execute(
        select(func.count(Step.id)).where(Step.run_id == run_id)
    )
    sequence_order = count_result.scalar() or 0
    
    # Compute stats from decisions
    stats = compute_stats(step_input.decisions) if step_input.decisions else None
    
    # Create step
    step = Step(
        run_id=run_id,
        step_name=step_input.name,
        sequence_order=sequence_order,
        input_data=step_input.input,
        output_data=step_input.output,
        config=step_input.config,
        reasoning=step_input.reasoning,
        stats=stats,
        started_at=datetime.utcnow(),
        completed_at=datetime.utcnow()
    )
    
    db.add(step)
    await db.flush()  # Get step ID before adding decisions
    
    # Add decisions
    if step_input.decisions:
        for idx, decision_input in enumerate(step_input.decisions):
            decision = Decision(
                step_id=step.id,
                candidate_id=decision_input.candidate_id,
                decision_type=decision_input.decision_type,
                reason=decision_input.reason,
                score=decision_input.score,
                sequence_order=idx,
                meta_data=decision_input.metadata,
                created_at=decision_input.timestamp or datetime.utcnow()
            )
            db.add(decision)
    
    # Add evidence
    if step_input.evidence:
        # Evidence is attached at step level, we'll create decisions for them
        # For now, store step-level evidence as metadata
        pass
    
    await db.commit()
    await db.refresh(step)
    
    return {
        "step_id": step.id,
        "stats": stats
    }


@router.patch("/runs/{run_id}", response_model=dict)
async def complete_run(
    run_id: str,
    run_complete: RunComplete,
    db: AsyncSession = Depends(get_db)
) -> dict:
    """
    Complete a pipeline run with final result.
    """
    result = await db.execute(select(Run).where(Run.id == run_id))
    run = result.scalar_one_or_none()
    
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} not found"
        )
    
    run.output_result = run_complete.result
    run.status = run_complete.status
    run.completed_at = datetime.utcnow()
    
    await db.commit()
    
    return {
        "run_id": run_id,
        "status": run.status,
        "completed_at": run.completed_at.isoformat()
    }


def compute_stats(decisions: list) -> dict[str, Any]:
    """
    Compute statistics from a list of decisions.
    
    Returns:
        - input_count: Total decisions
        - output_count: Accepted decisions
        - rejection_rate: Percentage rejected
        - rejection_reasons: Count per rejection reason
    """
    if not decisions:
        return {
            "input_count": 0,
            "output_count": 0,
            "rejection_rate": 0.0,
            "rejection_reasons": {}
        }
    
    total = len(decisions)
    accepted = sum(1 for d in decisions if d.decision_type == "accepted")
    rejected = sum(1 for d in decisions if d.decision_type == "rejected")
    
    # Count rejection reasons
    rejection_reasons = {}
    for d in decisions:
        if d.decision_type == "rejected" and d.reason:
            rejection_reasons[d.reason] = rejection_reasons.get(d.reason, 0) + 1
    
    return {
        "input_count": total,
        "output_count": accepted,
        "rejection_rate": rejected / total if total > 0 else 0.0,
        "rejection_reasons": rejection_reasons
    }

