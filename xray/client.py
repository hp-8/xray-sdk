"""
X-Ray SDK Client

The main client for recording pipeline runs, steps, and decisions.
"""

import os
import logging
from typing import Any
from datetime import datetime

import httpx

from xray.models import Step, RunInput, RunComplete, Decision
from xray.sampler import DecisionSampler

logger = logging.getLogger(__name__)


class XRay:
    """
    X-Ray SDK client for debugging multi-step pipelines.
    
    Usage:
        xray = XRay(api_url="http://localhost:8000")
        
        run_id = xray.start_run(
            pipeline_type="competitor_selection",
            input={"product_id": "123"}
        )
        
        xray.record_step(run_id, Step(
            name="filtering",
            input={"count": 5000},
            output={"count": 30},
            reasoning="Applied price filter"
        ))
        
        xray.complete_run(run_id, result={"winner": "product-456"})
    """
    
    def __init__(
        self,
        api_url: str | None = None,
        enabled: bool | None = None,
        sample_threshold: int | None = None,
        sample_per_reason: int | None = None,
        timeout: float = 10.0
    ):
        """
        Initialize the X-Ray client.
        
        Args:
            api_url: Base URL of the X-Ray API server
            enabled: Whether to actually send data (can disable for testing)
            sample_threshold: Max decisions before sampling kicks in
            sample_per_reason: Number of rejected decisions to keep per reason
            timeout: HTTP request timeout in seconds
        """
        self.api_url = api_url or os.getenv("XRAY_API_URL", "http://localhost:8000")
        self.enabled = enabled if enabled is not None else os.getenv("XRAY_ENABLED", "true").lower() == "true"
        self.timeout = timeout
        
        # Initialize sampler
        self.sampler = DecisionSampler(
            threshold=sample_threshold or int(os.getenv("XRAY_SAMPLE_THRESHOLD", "500")),
            per_reason=sample_per_reason or int(os.getenv("XRAY_SAMPLE_PER_REASON", "50"))
        )
        
        # HTTP client
        self._client = httpx.Client(
            base_url=self.api_url,
            timeout=timeout
        )
    
    def start_run(
        self,
        pipeline_type: str,
        name: str | None = None,
        input: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None
    ) -> str | None:
        """
        Start a new pipeline run.
        
        Args:
            pipeline_type: Type of pipeline (e.g., "competitor_selection")
            name: Optional name for this run
            input: Input context for the run
            metadata: Additional metadata
            
        Returns:
            run_id: The ID of the created run, or None if disabled/failed
        """
        if not self.enabled:
            return None
        
        try:
            run_input = RunInput(
                pipeline_type=pipeline_type,
                name=name,
                input=input,
                metadata=metadata
            )
            
            response = self._client.post(
                "/v1/runs",
                json=run_input.model_dump(exclude_none=True)
            )
            response.raise_for_status()
            
            data = response.json()
            return data.get("run_id")
            
        except Exception as e:
            logger.warning(f"Failed to start X-Ray run: {e}")
            return None
    
    def record_step(
        self,
        run_id: str | None,
        step: Step
    ) -> dict | None:
        """
        Record a step in a pipeline run.
        
        Args:
            run_id: The run ID (from start_run)
            step: Step data including decisions and reasoning
            
        Returns:
            Response with step_id and stats, or None if disabled/failed
        """
        if not self.enabled or run_id is None:
            return None
        
        try:
            # Sample decisions if too many
            step_data = step.model_dump(exclude_none=True)
            if step.decisions:
                sampled_decisions, was_sampled = self.sampler.sample(step.decisions)
                step_data["decisions"] = [
                    d.model_dump(exclude_none=True) if isinstance(d, Decision) else d
                    for d in sampled_decisions
                ]
                
                if was_sampled:
                    logger.debug(
                        f"Sampled {len(step.decisions)} decisions down to {len(sampled_decisions)}"
                    )
            
            response = self._client.post(
                f"/v1/runs/{run_id}/steps",
                json=step_data
            )
            response.raise_for_status()
            
            return response.json()
            
        except Exception as e:
            logger.warning(f"Failed to record X-Ray step: {e}")
            return None
    
    def complete_run(
        self,
        run_id: str | None,
        result: dict[str, Any] | None = None,
        status: str = "completed"
    ) -> dict | None:
        """
        Complete a pipeline run.
        
        Args:
            run_id: The run ID (from start_run)
            result: Final result of the run
            status: Final status (completed, failed, cancelled)
            
        Returns:
            Response data, or None if disabled/failed
        """
        if not self.enabled or run_id is None:
            return None
        
        try:
            run_complete = RunComplete(
                result=result,
                status=status
            )
            
            response = self._client.patch(
                f"/v1/runs/{run_id}",
                json=run_complete.model_dump(exclude_none=True)
            )
            response.raise_for_status()
            
            return response.json()
            
        except Exception as e:
            logger.warning(f"Failed to complete X-Ray run: {e}")
            return None
    
    # Convenience methods for querying
    
    def get_run(self, run_id: str, include_decisions: bool = False) -> dict | None:
        """Get a run by ID."""
        try:
            response = self._client.get(
                f"/v1/runs/{run_id}",
                params={"include_decisions": include_decisions}
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.warning(f"Failed to get X-Ray run: {e}")
            return None
    
    def query_runs(
        self,
        pipeline_type: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20
    ) -> dict | None:
        """Query runs with filters."""
        try:
            params = {"page": page, "page_size": page_size}
            if pipeline_type:
                params["pipeline_type"] = pipeline_type
            if status:
                params["status"] = status
            
            response = self._client.get("/v1/runs", params=params)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.warning(f"Failed to query X-Ray runs: {e}")
            return None
    
    def get_step(self, run_id: str, step_name: str) -> dict | None:
        """Get a specific step from a run by name."""
        run = self.get_run(run_id)
        if not run:
            return None
        
        for step in run.get("steps", []):
            if step.get("name") == step_name:
                return step
        return None
    
    def get_decisions(
        self,
        run_id: str,
        step_id: str,
        decision_type: str | None = None,
        page: int = 1,
        page_size: int = 50
    ) -> dict | None:
        """Get decisions for a step."""
        try:
            params = {"page": page, "page_size": page_size}
            if decision_type:
                params["decision_type"] = decision_type
            
            response = self._client.get(
                f"/v1/runs/{run_id}/steps/{step_id}/decisions",
                params=params
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.warning(f"Failed to get X-Ray decisions: {e}")
            return None
    
    def query_decisions(
        self,
        candidate_id: str | None = None,
        decision_type: str | None = None,
        reason: str | None = None,
        step_name: str | None = None,
        limit: int = 100
    ) -> dict | None:
        """Query decisions across all runs."""
        try:
            request_data = {
                "candidate_id": candidate_id,
                "decision_type": decision_type,
                "reason": reason,
                "step_name": step_name,
                "limit": limit
            }
            # Remove None values
            request_data = {k: v for k, v in request_data.items() if v is not None}
            
            response = self._client.post(
                "/v1/query/decisions",
                json=request_data
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.warning(f"Failed to query X-Ray decisions: {e}")
            return None
    
    def close(self):
        """Close the HTTP client."""
        self._client.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, *args):
        self.close()

