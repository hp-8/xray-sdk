"""
Decision Sampler - Implements ADR-001

Samples decisions to reduce volume while preserving debug value.
Strategy: keep all accepted, N rejected per reason, compute stats before sampling.

See: docs/adr/ADR-001-xray-data-capture-and-sampling.md
PRD Reference: PRD-xray-lite-v1.md Section 6 (Server-side sampling)
"""

import random
from collections import defaultdict
from typing import Any, TypedDict

from xray.models import Decision


class StatsDict(TypedDict):
    input_count: int
    output_count: int
    rejection_rate: float
    rejection_reasons: dict[str, int]


class SampleStats(StatsDict):
    sampled: bool
    sampled_count: int


class DecisionSampler:
    """
    Implements ADR-001: Stratified sampling with stats preservation.
    
    Keeps all accepted decisions and samples rejected decisions per reason
    to maintain diversity while reducing storage volume.
    """
    def __init__(self, threshold: int = 500, per_reason: int = 50):
        self.threshold = threshold
        self.per_reason = per_reason
    
    def sample(self, decisions: list[Decision]) -> tuple[list[Decision], bool]:
        """Returns (sampled_list, was_sampled)"""
        if len(decisions) <= self.threshold:
            return decisions, False
        
        accepted = []
        by_reason: dict[str, list[Decision]] = defaultdict(list)
        pending = []
        
        for d in decisions:
            if d.decision_type == "accepted":
                accepted.append(d)
            elif d.decision_type == "rejected":
                by_reason[d.reason or "unknown"].append(d)
            else:
                pending.append(d)
        
        sampled_rejected = []
        for reason, items in by_reason.items():
            if len(items) <= self.per_reason:
                sampled_rejected.extend(items)
            else:
                sampled_rejected.extend(random.sample(items, self.per_reason))
        
        result = accepted + sampled_rejected + pending
        result.sort(key=lambda d: d.metadata.get("sequence", 0) if d.metadata else 0)
        return result, True
    
    def compute_stats(self, decisions: list[Decision]) -> StatsDict:
        """
        Compute decision statistics on full decision set before sampling.
        Implements ADR-001: Stats must be computed on complete data.
        """
        if not decisions:
            return {"input_count": 0, "output_count": 0, "rejection_rate": 0.0, "rejection_reasons": {}}
        
        total = len(decisions)
        accepted = sum(1 for d in decisions if d.decision_type == "accepted")
        rejected = sum(1 for d in decisions if d.decision_type == "rejected")
        
        reasons: dict[str, int] = {}
        for d in decisions:
            if d.decision_type == "rejected":
                r = d.reason or "unknown"
                reasons[r] = reasons.get(r, 0) + 1
        
        return {
            "input_count": total,
            "output_count": accepted,
            "rejection_rate": rejected / total if total else 0.0,
            "rejection_reasons": reasons
        }


# Removed sample_decisions() - Dead code identified in audit
# Use DecisionSampler class methods directly instead
