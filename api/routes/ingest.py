from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.database import get_db
from api.db.models import Run, Step, Decision
from xray.models import RunInput, RunComplete, Step as StepInput

router = APIRouter(prefix="/v1", tags=["ingest"])


@router.post("/runs", status_code=status.HTTP_201_CREATED)
async def create_run(run_input: RunInput, db: AsyncSession = Depends(get_db)) -> dict:
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


@router.post("/runs/{run_id}/steps", status_code=status.HTTP_201_CREATED)
async def record_step(run_id: str, step_input: StepInput, db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(select(Run).where(Run.id == run_id))
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    
    count_result = await db.execute(select(func.count(Step.id)).where(Step.run_id == run_id))
    seq = count_result.scalar() or 0
    
    stats = _compute_stats(step_input.decisions) if step_input.decisions else None
    
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
    
    if step_input.decisions:
        for idx, d in enumerate(step_input.decisions):
            db.add(Decision(
                step_id=step.id,
                candidate_id=d.candidate_id,
                decision_type=d.decision_type,
                reason=d.reason,
                score=d.score,
                sequence_order=idx,
                meta_data=d.metadata,
                created_at=d.timestamp or datetime.utcnow()
            ))
    
    await db.commit()
    await db.refresh(step)
    return {"step_id": step.id, "stats": stats}


@router.patch("/runs/{run_id}")
async def complete_run(run_id: str, run_complete: RunComplete, db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(select(Run).where(Run.id == run_id))
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    
    run.output_result = run_complete.result
    run.status = run_complete.status
    run.completed_at = datetime.utcnow()
    await db.commit()
    
    return {"run_id": run_id, "status": run.status, "completed_at": run.completed_at.isoformat()}


def _compute_stats(decisions: list) -> dict[str, Any]:
    if not decisions:
        return {"input_count": 0, "output_count": 0, "rejection_rate": 0.0, "rejection_reasons": {}}
    
    total = len(decisions)
    accepted = sum(1 for d in decisions if d.decision_type == "accepted")
    rejected = sum(1 for d in decisions if d.decision_type == "rejected")
    
    reasons = {}
    for d in decisions:
        if d.decision_type == "rejected" and d.reason:
            reasons[d.reason] = reasons.get(d.reason, 0) + 1
    
    return {
        "input_count": total,
        "output_count": accepted,
        "rejection_rate": rejected / total if total else 0.0,
        "rejection_reasons": reasons
    }
