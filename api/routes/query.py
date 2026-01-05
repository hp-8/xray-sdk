"""
Query API Endpoints

Endpoints for querying and analyzing X-Ray data.
"""

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.db.database import get_db
from api.db.models import Run, Step, Decision, Evidence


router = APIRouter(prefix="/v1", tags=["query"])


class RunListResponse(BaseModel):
    """Response model for listing runs."""
    runs: list[dict]
    total: int
    page: int
    page_size: int


class StepQueryRequest(BaseModel):
    """Request model for querying steps across runs."""
    pipeline_type: str | None = None
    step_name: str | None = None
    min_rejection_rate: float | None = None
    max_rejection_rate: float | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    limit: int = 100
    offset: int = 0


class DecisionQueryRequest(BaseModel):
    """Request model for querying decisions."""
    candidate_id: str | None = None
    decision_type: str | None = None
    reason: str | None = None
    step_name: str | None = None
    limit: int = 100
    offset: int = 0


@router.get("/runs")
async def list_runs(
    pipeline_type: str | None = None,
    status: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
) -> RunListResponse:
    """
    List runs with optional filters.
    """
    query = select(Run)
    
    # Apply filters
    conditions = []
    if pipeline_type:
        conditions.append(Run.pipeline_type == pipeline_type)
    if status:
        conditions.append(Run.status == status)
    if date_from:
        conditions.append(Run.started_at >= date_from)
    if date_to:
        conditions.append(Run.started_at <= date_to)
    
    if conditions:
        query = query.where(and_(*conditions))
    
    # Order by most recent first
    query = query.order_by(Run.started_at.desc())
    
    # Get total count
    count_query = select(Run.id)
    if conditions:
        count_query = count_query.where(and_(*conditions))
    count_result = await db.execute(count_query)
    total = len(count_result.all())
    
    # Apply pagination
    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)
    
    result = await db.execute(query)
    runs = result.scalars().all()
    
    return RunListResponse(
        runs=[
            {
                "id": run.id,
                "pipeline_type": run.pipeline_type,
                "name": run.name,
                "status": run.status,
                "started_at": run.started_at.isoformat(),
                "completed_at": run.completed_at.isoformat() if run.completed_at else None
            }
            for run in runs
        ],
        total=total,
        page=page,
        page_size=page_size
    )


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    include_decisions: bool = Query(False),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """
    Get a run with all its steps.
    
    Optionally include decisions for each step.
    """
    query = select(Run).where(Run.id == run_id).options(
        selectinload(Run.steps)
    )
    
    result = await db.execute(query)
    run = result.scalar_one_or_none()
    
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} not found"
        )
    
    # Build response
    steps_data = []
    for step in sorted(run.steps, key=lambda s: s.sequence_order):
        step_data = {
            "id": step.id,
            "name": step.step_name,
            "sequence_order": step.sequence_order,
            "input": step.input_data,
            "output": step.output_data,
            "config": step.config,
            "reasoning": step.reasoning,
            "stats": step.stats,
            "started_at": step.started_at.isoformat(),
            "completed_at": step.completed_at.isoformat() if step.completed_at else None
        }
        
        if include_decisions:
            # Load decisions for this step
            decisions_query = select(Decision).where(
                Decision.step_id == step.id
            ).order_by(Decision.sequence_order)
            decisions_result = await db.execute(decisions_query)
            decisions = decisions_result.scalars().all()
            
            step_data["decisions"] = [
                {
                    "id": d.id,
                    "candidate_id": d.candidate_id,
                    "decision_type": d.decision_type,
                    "reason": d.reason,
                    "score": d.score,
                    "metadata": d.meta_data
                }
                for d in decisions
            ]
        
        steps_data.append(step_data)
    
    return {
        "id": run.id,
        "pipeline_type": run.pipeline_type,
        "name": run.name,
        "input": run.input_context,
        "output": run.output_result,
        "status": run.status,
        "started_at": run.started_at.isoformat(),
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "metadata": run.meta_data,
        "steps": steps_data
    }


@router.get("/runs/{run_id}/steps/{step_id}/decisions")
async def get_step_decisions(
    run_id: str,
    step_id: str,
    decision_type: str | None = None,
    reason: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """
    Get decisions for a specific step with pagination.
    """
    # Verify step exists and belongs to run
    step_query = select(Step).where(
        and_(Step.id == step_id, Step.run_id == run_id)
    )
    step_result = await db.execute(step_query)
    step = step_result.scalar_one_or_none()
    
    if not step:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Step {step_id} not found in run {run_id}"
        )
    
    # Query decisions
    query = select(Decision).where(Decision.step_id == step_id)
    
    conditions = []
    if decision_type:
        conditions.append(Decision.decision_type == decision_type)
    if reason:
        conditions.append(Decision.reason == reason)
    
    if conditions:
        query = query.where(and_(*conditions))
    
    query = query.order_by(Decision.sequence_order)
    
    # Get total count
    count_query = select(Decision.id).where(Decision.step_id == step_id)
    if conditions:
        count_query = count_query.where(and_(*conditions))
    count_result = await db.execute(count_query)
    total = len(count_result.all())
    
    # Apply pagination
    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)
    
    result = await db.execute(query)
    decisions = result.scalars().all()
    
    return {
        "step_id": step_id,
        "step_name": step.step_name,
        "decisions": [
            {
                "id": d.id,
                "candidate_id": d.candidate_id,
                "decision_type": d.decision_type,
                "reason": d.reason,
                "score": d.score,
                "sequence_order": d.sequence_order,
                "metadata": d.meta_data,
                "created_at": d.created_at.isoformat()
            }
            for d in decisions
        ],
        "total": total,
        "page": page,
        "page_size": page_size
    }


@router.post("/query/steps")
async def query_steps(
    request: StepQueryRequest,
    db: AsyncSession = Depends(get_db)
) -> dict:
    """
    Query steps across all runs.
    
    Useful for finding patterns like "all filtering steps with >90% rejection rate".
    """
    query = select(Step).join(Run)
    
    conditions = []
    if request.pipeline_type:
        conditions.append(Run.pipeline_type == request.pipeline_type)
    if request.step_name:
        conditions.append(Step.step_name == request.step_name)
    if request.date_from:
        conditions.append(Step.started_at >= request.date_from)
    if request.date_to:
        conditions.append(Step.started_at <= request.date_to)
    
    if conditions:
        query = query.where(and_(*conditions))
    
    query = query.order_by(Step.started_at.desc())
    query = query.offset(request.offset).limit(request.limit)
    
    result = await db.execute(query)
    steps = result.scalars().all()
    
    # Filter by rejection rate if specified (done in Python since stats is JSON)
    filtered_steps = []
    for step in steps:
        stats = step.stats or {}
        rejection_rate = stats.get("rejection_rate", 0)
        
        if request.min_rejection_rate and rejection_rate < request.min_rejection_rate:
            continue
        if request.max_rejection_rate and rejection_rate > request.max_rejection_rate:
            continue
        
        filtered_steps.append({
            "id": step.id,
            "run_id": step.run_id,
            "name": step.step_name,
            "stats": step.stats,
            "reasoning": step.reasoning,
            "started_at": step.started_at.isoformat()
        })
    
    return {
        "steps": filtered_steps,
        "count": len(filtered_steps)
    }


@router.post("/query/decisions")
async def query_decisions(
    request: DecisionQueryRequest,
    db: AsyncSession = Depends(get_db)
) -> dict:
    """
    Query decisions across all steps.
    
    Useful for finding patterns like "all decisions for candidate X across all runs".
    """
    query = select(Decision).join(Step)
    
    conditions = []
    if request.candidate_id:
        conditions.append(Decision.candidate_id == request.candidate_id)
    if request.decision_type:
        conditions.append(Decision.decision_type == request.decision_type)
    if request.reason:
        conditions.append(Decision.reason == request.reason)
    if request.step_name:
        conditions.append(Step.step_name == request.step_name)
    
    if conditions:
        query = query.where(and_(*conditions))
    
    query = query.order_by(Decision.created_at.desc())
    query = query.offset(request.offset).limit(request.limit)
    
    result = await db.execute(query)
    decisions = result.scalars().all()
    
    return {
        "decisions": [
            {
                "id": d.id,
                "step_id": d.step_id,
                "candidate_id": d.candidate_id,
                "decision_type": d.decision_type,
                "reason": d.reason,
                "score": d.score,
                "metadata": d.meta_data,
                "created_at": d.created_at.isoformat()
            }
            for d in decisions
        ],
        "count": len(decisions)
    }

