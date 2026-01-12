from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.db.database import get_db
from api.db.models import Run, Step, Decision
from api.templates import render_run_html

router = APIRouter(prefix="/visualize", tags=["visualization"])


@router.get("/runs/{run_id}", response_class=HTMLResponse, response_model=None)
async def visualize_run(
    run_id: str,
    format: str = Query("html", pattern="^(html|json)$"),
    db: AsyncSession = Depends(get_db)
) -> HTMLResponse | JSONResponse:
    """
    Visualize a run with its steps and decisions.
    
    Returns HTML by default, JSON if ?format=json.
    Note: response_model=None because we return different response types (HTMLResponse | JSONResponse)
    """
    query = select(Run).where(Run.id == run_id).options(selectinload(Run.steps))
    result = await db.execute(query)
    run = result.scalar_one_or_none()
    
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    
    steps_sorted = sorted(run.steps, key=lambda s: s.sequence_order)
    step_ids = [s.id for s in steps_sorted]
    decisions_by_step: dict[str, list[Decision]] = {sid: [] for sid in step_ids}

    if step_ids:
        dec_result = await db.execute(
            select(Decision)
            .where(Decision.step_id.in_(step_ids))
            .order_by(Decision.sequence_order)
        )
        for d in dec_result.scalars().all():
            decisions_by_step[d.step_id].append(d)

    steps_data = []
    for step in steps_sorted:
        decisions = decisions_by_step.get(step.id, [])

        accepted = [d for d in decisions if d.decision_type == "accepted"]
        rejected = [d for d in decisions if d.decision_type == "rejected"]
        pending = [d for d in decisions if d.decision_type == "pending"]
        
        steps_data.append({
            "id": step.id,
            "name": step.step_name,
            "sequence_order": step.sequence_order,
            "input": step.input_data,
            "output": step.output_data,
            "config": step.config,
            "reasoning": step.reasoning,
            "stats": step.stats,
            "started_at": step.started_at.isoformat(),
            "completed_at": step.completed_at.isoformat() if step.completed_at else None,
            "decisions": {
                "accepted": len(accepted),
                "rejected": len(rejected),
                "pending": len(pending),
                "total": len(decisions)
            },
            "decisions_list": [{
                "id": d.id,
                "candidate_id": d.candidate_id,
                "decision_type": d.decision_type,
                "reason": d.reason,
                "score": d.score,
                "metadata": d.meta_data,
                "sequence_order": d.sequence_order
            } for d in decisions[:100]]
        })
    
    run_data = {
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
    
    if format == "json":
        return JSONResponse(content=run_data)
    
    # Use extracted template renderer for better separation of concerns
    html_content = render_run_html(run_data)
    return HTMLResponse(content=html_content)
