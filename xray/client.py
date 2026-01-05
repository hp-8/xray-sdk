import os
import logging
from typing import Any

import httpx

from xray.models import Step, RunInput, RunComplete, Decision
from xray.sampler import DecisionSampler

log = logging.getLogger(__name__)


class XRay:
    """
    SDK for recording debug data from multi-step pipelines.
    
    Basic usage:
        xray = XRay()
        run_id = xray.start_run("competitor_selection", input={"product_id": "123"})
        xray.record_step(run_id, Step(name="filtering", ...))
        xray.complete_run(run_id, result={"winner": "456"})
    """
    
    def __init__(
        self,
        api_url: str | None = None,
        enabled: bool | None = None,
        sample_threshold: int | None = None,
        sample_per_reason: int | None = None,
        timeout: float = 10.0
    ):
        self.api_url = api_url or os.getenv("XRAY_API_URL", "http://localhost:8000")
        self.enabled = enabled if enabled is not None else os.getenv("XRAY_ENABLED", "true").lower() == "true"
        self.timeout = timeout
        
        self.sampler = DecisionSampler(
            threshold=sample_threshold or int(os.getenv("XRAY_SAMPLE_THRESHOLD", "500")),
            per_reason=sample_per_reason or int(os.getenv("XRAY_SAMPLE_PER_REASON", "50"))
        )
        self._client = httpx.Client(base_url=self.api_url, timeout=timeout)
    
    def start_run(
        self,
        pipeline_type: str,
        name: str | None = None,
        input: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None
    ) -> str | None:
        if not self.enabled:
            return None
        
        try:
            payload = RunInput(pipeline_type=pipeline_type, name=name, input=input, metadata=metadata)
            resp = self._client.post("/v1/runs", json=payload.model_dump(exclude_none=True))
            resp.raise_for_status()
            return resp.json().get("run_id")
        except Exception as e:
            log.warning(f"start_run failed: {e}")
            return None
    
    def record_step(self, run_id: str | None, step: Step) -> dict | None:
        if not self.enabled or not run_id:
            return None
        
        try:
            data = step.model_dump(exclude_none=True)
            
            if step.decisions:
                sampled, did_sample = self.sampler.sample(step.decisions)
                data["decisions"] = [
                    d.model_dump(exclude_none=True) if isinstance(d, Decision) else d
                    for d in sampled
                ]
                if did_sample:
                    log.debug(f"sampled {len(step.decisions)} -> {len(sampled)} decisions")
            
            resp = self._client.post(f"/v1/runs/{run_id}/steps", json=data)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log.warning(f"record_step failed: {e}")
            return None
    
    def complete_run(
        self,
        run_id: str | None,
        result: dict[str, Any] | None = None,
        status: str = "completed"
    ) -> dict | None:
        if not self.enabled or not run_id:
            return None
        
        try:
            payload = RunComplete(result=result, status=status)
            resp = self._client.patch(f"/v1/runs/{run_id}", json=payload.model_dump(exclude_none=True))
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log.warning(f"complete_run failed: {e}")
            return None
    
    # query methods
    
    def get_run(self, run_id: str, include_decisions: bool = False) -> dict | None:
        try:
            resp = self._client.get(f"/v1/runs/{run_id}", params={"include_decisions": include_decisions})
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log.warning(f"get_run failed: {e}")
            return None
    
    def query_runs(
        self,
        pipeline_type: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20
    ) -> dict | None:
        try:
            params = {"page": page, "page_size": page_size}
            if pipeline_type:
                params["pipeline_type"] = pipeline_type
            if status:
                params["status"] = status
            resp = self._client.get("/v1/runs", params=params)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log.warning(f"query_runs failed: {e}")
            return None
    
    def get_step(self, run_id: str, step_name: str) -> dict | None:
        run = self.get_run(run_id)
        if not run:
            return None
        for s in run.get("steps", []):
            if s.get("name") == step_name:
                return s
        return None
    
    def get_decisions(
        self,
        run_id: str,
        step_id: str,
        decision_type: str | None = None,
        page: int = 1,
        page_size: int = 50
    ) -> dict | None:
        try:
            params = {"page": page, "page_size": page_size}
            if decision_type:
                params["decision_type"] = decision_type
            resp = self._client.get(f"/v1/runs/{run_id}/steps/{step_id}/decisions", params=params)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log.warning(f"get_decisions failed: {e}")
            return None
    
    def query_decisions(
        self,
        candidate_id: str | None = None,
        decision_type: str | None = None,
        reason: str | None = None,
        step_name: str | None = None,
        limit: int = 100
    ) -> dict | None:
        try:
            data = {k: v for k, v in {
                "candidate_id": candidate_id,
                "decision_type": decision_type,
                "reason": reason,
                "step_name": step_name,
                "limit": limit
            }.items() if v is not None}
            resp = self._client.post("/v1/query/decisions", json=data)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log.warning(f"query_decisions failed: {e}")
            return None
    
    def close(self):
        self._client.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, *args):
        self.close()
