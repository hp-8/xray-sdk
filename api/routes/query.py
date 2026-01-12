from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.db.database import get_db
from api.db.models import Run, Step, Decision

router = APIRouter(prefix="/v1", tags=["query"])


class RunSummary(BaseModel):
    id: str
    pipeline_type: str
    name: str | None
    status: str
    started_at: datetime
    completed_at: datetime | None


class RunListResponse(BaseModel):
    runs: list[RunSummary]
    total: int
    page: int
    page_size: int


class StepQueryRequest(BaseModel):
    pipeline_type: str | None = None
    step_name: str | None = None
    min_rejection_rate: float | None = None
    max_rejection_rate: float | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    limit: int = 100
    offset: int = 0


class DecisionQueryRequest(BaseModel):
    candidate_id: str | None = None
    decision_type: str | None = None
    reason: str | None = None
    step_name: str | None = None
    limit: int = 100
    offset: int = 0


class DecisionOut(BaseModel):
    id: str
    candidate_id: str
    decision_type: str
    reason: str | None = None
    score: float | None = None
    sequence_order: int
    metadata: dict[str, Any] | None = None
    created_at: datetime | None = None


class StepOut(BaseModel):
    id: str
    name: str
    sequence_order: int
    input: dict[str, Any] | None
    output: dict[str, Any] | None
    config: dict[str, Any] | None
    reasoning: str | None
    stats: dict[str, Any] | None
    started_at: datetime
    completed_at: datetime | None
    decisions: list[DecisionOut] | None = None


class RunDetailResponse(BaseModel):
    id: str
    pipeline_type: str
    name: str | None
    input: dict[str, Any] | None
    output: dict[str, Any] | None
    status: str
    started_at: datetime
    completed_at: datetime | None
    metadata: dict[str, Any] | None
    steps: list[StepOut]


class StepDecisionListResponse(BaseModel):
    step_id: str
    step_name: str
    decisions: list[DecisionOut]
    total: int
    page: int
    page_size: int


class StepQueryItem(BaseModel):
    id: str
    run_id: str
    name: str
    stats: dict[str, Any] | None
    reasoning: str | None
    started_at: datetime


class StepQueryResponse(BaseModel):
    steps: list[StepQueryItem]
    count: int


class DecisionQueryItem(BaseModel):
    id: str
    step_id: str
    candidate_id: str
    decision_type: str
    reason: str | None
    score: float | None
    metadata: dict[str, Any] | None
    created_at: datetime


class DecisionQueryResponse(BaseModel):
    decisions: list[DecisionQueryItem]
    count: int


@router.get("/runs", response_model=RunListResponse)
async def list_runs(
    pipeline_type: str | None = None,
    status: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
) -> RunListResponse:
    query = select(Run)
    
    conds = []
    if pipeline_type:
        conds.append(Run.pipeline_type == pipeline_type)
    if status:
        conds.append(Run.status == status)
    if date_from:
        conds.append(Run.started_at >= date_from)
    if date_to:
        conds.append(Run.started_at <= date_to)
    
    if conds:
        query = query.where(and_(*conds))
    
    query = query.order_by(Run.started_at.desc())
    
    # count total (per PRD-xray-lite-v1: use DB counts, no in-memory len(all()))
    count_query = select(func.count()).select_from(Run)
    if conds:
        count_query = count_query.where(and_(*conds))
    total = await db.scalar(count_query) or 0
    
    # paginate
    offset = (page - 1) * page_size
    result = await db.execute(query.offset(offset).limit(page_size))
    runs = result.scalars().all()
    
    run_summaries = [
        RunSummary(
            id=r.id,
            pipeline_type=r.pipeline_type,
            name=r.name,
            status=r.status,
            started_at=r.started_at,
            completed_at=r.completed_at
        )
        for r in runs
    ]

    return RunListResponse(runs=run_summaries, total=total, page=page, page_size=page_size)


@router.get("/runs/{run_id}", response_model=RunDetailResponse)
async def get_run(
    run_id: str,
    include_decisions: bool = Query(False),
    db: AsyncSession = Depends(get_db)
) -> RunDetailResponse:
    query = select(Run).where(Run.id == run_id).options(selectinload(Run.steps))
    result = await db.execute(query)
    run = result.scalar_one_or_none()
    
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    
    steps_sorted = sorted(run.steps, key=lambda s: s.sequence_order)
    decisions_by_step: dict[str, list[Decision]] = {}
    if include_decisions and steps_sorted:
        step_ids = [s.id for s in steps_sorted]
        dec_result = await db.execute(
            select(Decision)
            .where(Decision.step_id.in_(step_ids))
            .order_by(Decision.sequence_order)
        )
        for d in dec_result.scalars().all():
            decisions_by_step.setdefault(d.step_id, []).append(d)

    steps_data: list[StepOut] = []
    for step in steps_sorted:
        decisions_payload: list[DecisionOut] | None = None
        if include_decisions:
            decisions_payload = [
                DecisionOut(
                    id=d.id,
                    candidate_id=d.candidate_id,
                    decision_type=d.decision_type,
                    reason=d.reason,
                    score=d.score,
                    sequence_order=d.sequence_order,
                    metadata=d.meta_data,
                    created_at=d.created_at,
                )
                for d in decisions_by_step.get(step.id, [])
            ]

        steps_data.append(
            StepOut(
                id=step.id,
                name=step.step_name,
                sequence_order=step.sequence_order,
                input=step.input_data,
                output=step.output_data,
                config=step.config,
                reasoning=step.reasoning,
                stats=step.stats,
                started_at=step.started_at,
                completed_at=step.completed_at,
                decisions=decisions_payload,
            )
        )
    
    return RunDetailResponse(
        id=run.id,
        pipeline_type=run.pipeline_type,
        name=run.name,
        input=run.input_context,
        output=run.output_result,
        status=run.status,
        started_at=run.started_at,
        completed_at=run.completed_at,
        metadata=run.meta_data,
        steps=steps_data,
    )


@router.get("/runs/{run_id}/steps/{step_id}/decisions", response_model=StepDecisionListResponse)
async def get_step_decisions(
    run_id: str,
    step_id: str,
    decision_type: str | None = None,
    reason: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db)
) -> StepDecisionListResponse:
    step_result = await db.execute(select(Step).where(and_(Step.id == step_id, Step.run_id == run_id)))
    step = step_result.scalar_one_or_none()
    if not step:
        raise HTTPException(status_code=404, detail=f"Step {step_id} not found in run {run_id}")
    
    query = select(Decision).where(Decision.step_id == step_id)
    
    conds = []
    if decision_type:
        conds.append(Decision.decision_type == decision_type)
    if reason:
        conds.append(Decision.reason == reason)
    if conds:
        query = query.where(and_(*conds))
    
    query = query.order_by(Decision.sequence_order)
    
    # count
    count_query = select(func.count()).select_from(Decision).where(Decision.step_id == step_id)
    if conds:
        count_query = count_query.where(and_(*conds))
    total = await db.scalar(count_query) or 0
    
    offset = (page - 1) * page_size
    result = await db.execute(query.offset(offset).limit(page_size))
    decisions = result.scalars().all()
    
    return StepDecisionListResponse(
        step_id=step_id,
        step_name=step.step_name,
        decisions=[
            DecisionOut(
                id=d.id,
                candidate_id=d.candidate_id,
                decision_type=d.decision_type,
                reason=d.reason,
                score=d.score,
                sequence_order=d.sequence_order,
                metadata=d.meta_data,
                created_at=d.created_at,
            )
            for d in decisions
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/query/steps", response_model=StepQueryResponse)
async def query_steps(request: StepQueryRequest, db: AsyncSession = Depends(get_db)) -> StepQueryResponse:
    """Find steps across runs - useful for patterns like high rejection rates."""
    query = select(Step).join(Run)
    
    conds = []
    if request.pipeline_type:
        conds.append(Run.pipeline_type == request.pipeline_type)
    if request.step_name:
        conds.append(Step.step_name == request.step_name)
    if request.date_from:
        conds.append(Step.started_at >= request.date_from)
    if request.date_to:
        conds.append(Step.started_at <= request.date_to)
    
    if conds:
        query = query.where(and_(*conds))
    
    query = query.order_by(Step.started_at.desc()).offset(request.offset).limit(request.limit)
    steps = (await db.execute(query)).scalars().all()
    
    # TODO: Implement SQL-based filtering for rejection_rate (see refactor-3)
    # Current implementation filters in Python due to JSON field limitations in SQLite
    # For production, use PostgreSQL with JSONB and create expression index:
    # CREATE INDEX idx_step_rejection_rate ON steps ((stats->>'rejection_rate')::float);
    # PRD Reference: Section 8 (Performance Requirements)
    
    filtered: list[StepQueryItem] = []
    for step in steps:
        stats = step.stats or {}
        rate = stats.get("rejection_rate", 0)
        if request.min_rejection_rate and rate < request.min_rejection_rate:
            continue
        if request.max_rejection_rate and rate > request.max_rejection_rate:
            continue
        filtered.append(
            StepQueryItem(
                id=step.id,
                run_id=step.run_id,
                name=step.step_name,
                stats=step.stats,
                reasoning=step.reasoning,
                started_at=step.started_at,
            )
        )
    
    return StepQueryResponse(steps=filtered, count=len(filtered))


@router.post("/query/decisions", response_model=DecisionQueryResponse)
async def query_decisions(request: DecisionQueryRequest, db: AsyncSession = Depends(get_db)) -> DecisionQueryResponse:
    """Find decisions across steps - useful for tracking a candidate across runs."""
    query = select(Decision).join(Step)
    
    conds = []
    if request.candidate_id:
        conds.append(Decision.candidate_id == request.candidate_id)
    if request.decision_type:
        conds.append(Decision.decision_type == request.decision_type)
    if request.reason:
        conds.append(Decision.reason == request.reason)
    if request.step_name:
        conds.append(Step.step_name == request.step_name)
    
    if conds:
        query = query.where(and_(*conds))
    
    query = query.order_by(Decision.created_at.desc()).offset(request.offset).limit(request.limit)
    decisions = (await db.execute(query)).scalars().all()
    
    return DecisionQueryResponse(
        decisions=[
            DecisionQueryItem(
                id=d.id,
                step_id=d.step_id,
                candidate_id=d.candidate_id,
                decision_type=d.decision_type,
                reason=d.reason,
                score=d.score,
                metadata=d.meta_data,
                created_at=d.created_at,
            )
            for d in decisions
        ],
        count=len(decisions),
    )
