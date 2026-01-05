"""
Samples decisions to reduce volume while preserving debug value.

Strategy: keep all accepted, N rejected per reason, compute stats before sampling.
"""

import random
from collections import defaultdict
from typing import Any

from xray.models import Decision


class DecisionSampler:
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
    
    def compute_stats(self, decisions: list[Decision]) -> dict[str, Any]:
        if not decisions:
            return {"input_count": 0, "output_count": 0, "rejection_rate": 0.0, "rejection_reasons": {}}
        
        total = len(decisions)
        accepted = sum(1 for d in decisions if d.decision_type == "accepted")
        rejected = sum(1 for d in decisions if d.decision_type == "rejected")
        
        reasons = {}
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


def sample_decisions(
    decisions: list[Decision],
    threshold: int = 500,
    per_reason: int = 50
) -> tuple[list[Decision], dict[str, Any]]:
    """Convenience - samples and computes stats."""
    sampler = DecisionSampler(threshold=threshold, per_reason=per_reason)
    stats = sampler.compute_stats(decisions)
    sampled, was_sampled = sampler.sample(decisions)
    stats["sampled"] = was_sampled
    stats["sampled_count"] = len(sampled)
    return sampled, stats
