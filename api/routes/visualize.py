"""
Visualization routes for X-Ray data.

Provides HTML visualization of runs, steps, and decisions.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from api.db.database import get_db
from api.db.models import Run, Step, Decision

router = APIRouter(prefix="/visualize", tags=["visualization"])


@router.get("/runs/{run_id}", response_class=HTMLResponse)
async def visualize_run(
    run_id: str,
    format: str = Query("html", regex="^(html|json)$"),
    db: AsyncSession = Depends(get_db)
):
    """
    Visualize a run with all steps and decisions.
    
    Returns HTML visualization or JSON based on format parameter.
    """
    try:
        # Fetch run with steps
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
        
        # Load all decisions for all steps
        steps_data = []
        for step in sorted(run.steps, key=lambda s: s.sequence_order):
            decisions_query = select(Decision).where(
                Decision.step_id == step.id
            ).order_by(Decision.sequence_order)
            decisions_result = await db.execute(decisions_query)
            decisions = decisions_result.scalars().all()
            
            # Group decisions by type
            accepted = [d for d in decisions if d.decision_type == "accepted"]
            rejected = [d for d in decisions if d.decision_type == "rejected"]
            pending = [d for d in decisions if d.decision_type == "pending"]
            
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
                "completed_at": step.completed_at.isoformat() if step.completed_at else None,
                "decisions": {
                    "accepted": len(accepted),
                    "rejected": len(rejected),
                    "pending": len(pending),
                    "total": len(decisions)
                },
                "decisions_list": [
                    {
                        "id": d.id,
                        "candidate_id": d.candidate_id,
                        "decision_type": d.decision_type,
                        "reason": d.reason,
                        "score": d.score,
                        "metadata": d.meta_data,
                        "sequence_order": d.sequence_order
                    }
                    for d in decisions[:100]  # Limit for visualization
                ]
            }
            steps_data.append(step_data)
        
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
            from fastapi.responses import JSONResponse
            return JSONResponse(content=run_data)
        
        # Render HTML visualization
        return render_run_html(run_data)
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error rendering visualization: {str(e)}\n\n{error_details}"
        )


def render_run_html(run_data: dict) -> str:
    """Render HTML visualization of run data."""
    import html as html_escape
    
    # Escape HTML in text fields
    def escape_html(text):
        if text is None:
            return ""
        if isinstance(text, (dict, list)):
            return format_json(text)
        return html_escape.escape(str(text))
    
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>X-Ray: {escape_html(run_data.get('name', 'Unnamed Run'))}</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            background: #f5f5f5;
            color: #333;
            line-height: 1.6;
            padding: 1rem;
        }}
        
        .container {{
            max-width: 1400px;
            margin: 0 auto;
            background: white;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            padding: 2rem;
        }}
        
        .header {{
            border-bottom: 2px solid #e0e0e0;
            padding-bottom: 1rem;
            margin-bottom: 2rem;
        }}
        
        .header h1 {{
            font-size: 1.8rem;
            color: #1a1a1a;
            margin-bottom: 0.5rem;
        }}
        
        .header .meta {{
            display: flex;
            gap: 2rem;
            flex-wrap: wrap;
            font-size: 0.9rem;
            color: #666;
        }}
        
        .badge {{
            display: inline-block;
            padding: 0.25rem 0.75rem;
            border-radius: 12px;
            font-size: 0.85rem;
            font-weight: 500;
        }}
        
        .badge-success {{
            background: #d4edda;
            color: #155724;
        }}
        
        .badge-failed {{
            background: #f8d7da;
            color: #721c24;
        }}
        
        .badge-pending {{
            background: #fff3cd;
            color: #856404;
        }}
        
        .timeline {{
            position: relative;
            padding-left: 2rem;
        }}
        
        .timeline::before {{
            content: '';
            position: absolute;
            left: 0.5rem;
            top: 0;
            bottom: 0;
            width: 2px;
            background: #e0e0e0;
        }}
        
        .step {{
            position: relative;
            margin-bottom: 2rem;
            background: #fafafa;
            border-radius: 8px;
            padding: 1.5rem;
            border-left: 4px solid #4a90e2;
        }}
        
        .step::before {{
            content: '';
            position: absolute;
            left: -2.25rem;
            top: 1.5rem;
            width: 12px;
            height: 12px;
            border-radius: 50%;
            background: #4a90e2;
            border: 2px solid white;
        }}
        
        .step-header {{
            display: flex;
            justify-content: space-between;
            align-items: start;
            margin-bottom: 1rem;
        }}
        
        .step-title {{
            font-size: 1.3rem;
            font-weight: 600;
            color: #1a1a1a;
        }}
        
        .step-order {{
            color: #999;
            font-size: 0.9rem;
        }}
        
        .step-content {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 1rem;
            margin-bottom: 1rem;
        }}
        
        .section {{
            background: white;
            padding: 1rem;
            border-radius: 6px;
            border: 1px solid #e0e0e0;
        }}
        
        .section h3 {{
            font-size: 0.9rem;
            text-transform: uppercase;
            color: #666;
            margin-bottom: 0.5rem;
            letter-spacing: 0.5px;
        }}
        
        .section pre {{
            background: #f8f8f8;
            padding: 0.75rem;
            border-radius: 4px;
            font-size: 0.85rem;
            overflow-x: auto;
            max-height: 200px;
            overflow-y: auto;
        }}
        
        .decisions-summary {{
            display: flex;
            gap: 1rem;
            margin-top: 1rem;
        }}
        
        .decision-count {{
            padding: 0.5rem 1rem;
            border-radius: 6px;
            text-align: center;
        }}
        
        .decision-count.accepted {{
            background: #d4edda;
            color: #155724;
        }}
        
        .decision-count.rejected {{
            background: #f8d7da;
            color: #721c24;
        }}
        
        .decision-count.pending {{
            background: #fff3cd;
            color: #856404;
        }}
        
        .decision-count .number {{
            font-size: 1.5rem;
            font-weight: bold;
            display: block;
        }}
        
        .decision-count .label {{
            font-size: 0.85rem;
            text-transform: uppercase;
        }}
        
        .decisions-list {{
            margin-top: 1rem;
            max-height: 300px;
            overflow-y: auto;
        }}
        
        .decision-item {{
            padding: 0.75rem;
            margin-bottom: 0.5rem;
            border-radius: 4px;
            border-left: 3px solid;
            background: white;
        }}
        
        .decision-item.accepted {{
            border-color: #28a745;
        }}
        
        .decision-item.rejected {{
            border-color: #dc3545;
        }}
        
        .decision-item.pending {{
            border-color: #ffc107;
        }}
        
        .decision-item .candidate-id {{
            font-weight: 600;
            margin-bottom: 0.25rem;
        }}
        
        .decision-item .reason {{
            font-size: 0.9rem;
            color: #666;
        }}
        
        .decision-item .score {{
            float: right;
            font-weight: 600;
            color: #4a90e2;
        }}
        
        .stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 1rem;
            margin-top: 1rem;
        }}
        
        .stat-item {{
            text-align: center;
            padding: 1rem;
            background: white;
            border-radius: 6px;
            border: 1px solid #e0e0e0;
        }}
        
        .stat-item .value {{
            font-size: 1.8rem;
            font-weight: bold;
            color: #4a90e2;
        }}
        
        .stat-item .label {{
            font-size: 0.85rem;
            color: #666;
            text-transform: uppercase;
            margin-top: 0.25rem;
        }}
        
        .toggle-view {{
            position: fixed;
            top: 1rem;
            right: 1rem;
            background: white;
            padding: 0.75rem 1.5rem;
            border-radius: 6px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            border: 1px solid #e0e0e0;
        }}
        
        .toggle-view a {{
            text-decoration: none;
            color: #4a90e2;
            font-weight: 500;
        }}
        
        .reasoning {{
            background: #e3f2fd;
            padding: 1rem;
            border-radius: 6px;
            border-left: 4px solid #2196f3;
            margin-top: 1rem;
            font-style: italic;
        }}
    </style>
</head>
<body>
    <div class="toggle-view">
        <a href="?format=json">View JSON</a>
    </div>
    
    <div class="container">
        <div class="header">
            <h1>{escape_html(run_data.get('name', 'Unnamed Run'))}</h1>
            <div class="meta">
                <span><strong>Pipeline:</strong> {escape_html(run_data.get('pipeline_type', 'N/A'))}</span>
                <span><strong>Status:</strong> <span class="badge badge-{run_data.get('status', 'unknown')}">{escape_html(run_data.get('status', 'unknown'))}</span></span>
                <span><strong>Started:</strong> {escape_html(run_data.get('started_at', '')[:19].replace('T', ' ') if run_data.get('started_at') else 'N/A')}</span>
                {f'<span><strong>Completed:</strong> {escape_html(run_data["completed_at"][:19].replace("T", " ") if run_data.get("completed_at") else "N/A")}</span>' if run_data.get('completed_at') else ''}
            </div>
        </div>
        
        <div class="timeline">
"""
    
    for step in run_data.get('steps', []):
        decisions = step.get('decisions', {})
        stats = step.get('stats', {})
        
        html += f"""
            <div class="step">
                <div class="step-header">
                    <div>
                        <span class="step-order">Step {step.get('sequence_order', 0)}</span>
                        <h2 class="step-title">{escape_html(step.get('name', 'Unnamed Step'))}</h2>
                    </div>
                </div>
                
                <div class="step-content">
                    <div class="section">
                        <h3>Input</h3>
                        <pre>{escape_html(format_json(step.get('input')))}</pre>
                    </div>
                    <div class="section">
                        <h3>Output</h3>
                        <pre>{escape_html(format_json(step.get('output')))}</pre>
                    </div>
                    {f'<div class="section"><h3>Config</h3><pre>{escape_html(format_json(step.get("config")))}</pre></div>' if step.get('config') else ''}
                </div>
                
                {f'<div class="reasoning">{escape_html(step.get("reasoning", ""))}</div>' if step.get('reasoning') else ''}
                
                <div class="decisions-summary">
                    <div class="decision-count accepted">
                        <span class="number">{decisions.get('accepted', 0)}</span>
                        <span class="label">Accepted</span>
                    </div>
                    <div class="decision-count rejected">
                        <span class="number">{decisions.get('rejected', 0)}</span>
                        <span class="label">Rejected</span>
                    </div>
                    <div class="decision-count pending">
                        <span class="number">{decisions.get('pending', 0)}</span>
                        <span class="label">Pending</span>
                    </div>
                    <div class="decision-count">
                        <span class="number">{decisions.get('total', 0)}</span>
                        <span class="label">Total</span>
                    </div>
                </div>
"""
        
        if stats:
            html += """
                <div class="stats">
"""
            for key, value in stats.items():
                if isinstance(value, (int, float)):
                    # Format the value properly
                    formatted_value = f"{value:.2f}" if isinstance(value, float) else str(value)
                    html += f"""
                    <div class="stat-item">
                        <div class="value">{formatted_value}</div>
                        <div class="label">{escape_html(key.replace('_', ' ').title())}</div>
                    </div>
"""
            html += """
                </div>
"""
        
        decisions_list = step.get('decisions_list', [])
        if decisions_list:
            html += """
                <div class="decisions-list">
"""
            for decision in decisions_list[:50]:  # Show first 50
                score_html = f'<span class="score">{decision["score"]:.2f}</span>' if decision.get('score') is not None else ''
                html += f"""
                    <div class="decision-item {escape_html(decision.get('decision_type', 'unknown'))}">
                        <span class="candidate-id">{escape_html(str(decision.get('candidate_id', 'N/A')))}</span>
                        {score_html}
                        <div class="reason">{escape_html(str(decision.get('reason', 'N/A')))}</div>
                    </div>
"""
            if len(decisions_list) > 50:
                html += f"""
                    <div style="text-align: center; padding: 1rem; color: #666;">
                        ... and {len(decisions_list) - 50} more decisions
                    </div>
"""
            html += """
                </div>
"""
        
        html += """
            </div>
"""
    
    html += """
        </div>
    </div>
</body>
</html>
"""
    
    return html


def format_json(data: dict | list | None) -> str:
    """Format data as JSON string."""
    import json
    if data is None:
        return "null"
    try:
        return json.dumps(data, indent=2, default=str)
    except (TypeError, ValueError):
        return str(data)

