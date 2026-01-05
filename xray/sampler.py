"""
Decision Event Sampler

Samples decisions to reduce data volume while preserving debugging value.

Strategy:
1. Keep ALL accepted decisions (we care about what passed)
2. Keep N random rejected decisions PER REASON (preserve reasoning diversity)
3. Maintain time-ordering (sequence_order)
"""

import random
from collections import defaultdict
from typing import Any

from xray.models import Decision


class DecisionSampler:
    """
    Samples decisions to reduce data volume while preserving debugging value.
    
    The sampling strategy prioritizes:
    1. All accepted decisions (preserve positive outcomes)
    2. N rejected decisions per reason (preserve reasoning diversity)
    3. Time ordering (sequence information)
    """
    
    def __init__(
        self,
        threshold: int = 500,
        per_reason: int = 50
    ):
        """
        Initialize the sampler.
        
        Args:
            threshold: Max decisions before sampling kicks in
            per_reason: Number of rejected decisions to keep per rejection reason
        """
        self.threshold = threshold
        self.per_reason = per_reason
    
    def sample(
        self,
        decisions: list[Decision]
    ) -> tuple[list[Decision], bool]:
        """
        Sample decisions if they exceed the threshold.
        
        Args:
            decisions: List of Decision objects
            
        Returns:
            Tuple of (sampled_decisions, was_sampled)
        """
        if len(decisions) <= self.threshold:
            return decisions, False
        
        # Separate by decision type
        accepted = []
        rejected_by_reason: dict[str, list[Decision]] = defaultdict(list)
        pending = []
        
        for d in decisions:
            if d.decision_type == "accepted":
                accepted.append(d)
            elif d.decision_type == "rejected":
                reason = d.reason or "unknown"
                rejected_by_reason[reason].append(d)
            else:
                pending.append(d)
        
        # Sample rejected decisions: N per reason
        sampled_rejected = []
        for reason, reason_decisions in rejected_by_reason.items():
            if len(reason_decisions) <= self.per_reason:
                sampled_rejected.extend(reason_decisions)
            else:
                # Random sample, but try to preserve some diversity
                sampled = random.sample(reason_decisions, self.per_reason)
                sampled_rejected.extend(sampled)
        
        # Combine: all accepted + sampled rejected + all pending
        sampled = accepted + sampled_rejected + pending
        
        # Sort by sequence order if available
        sampled.sort(key=lambda d: self._get_sequence(d))
        
        return sampled, True
    
    def _get_sequence(self, decision: Decision) -> int:
        """Get sequence order from decision, with fallback."""
        if decision.metadata and "sequence" in decision.metadata:
            return decision.metadata["sequence"]
        return 0
    
    def compute_stats(self, decisions: list[Decision]) -> dict[str, Any]:
        """
        Compute statistics from decisions.
        
        This is computed BEFORE sampling, so stats reflect the full picture.
        
        Returns:
            Dict with:
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
            if d.decision_type == "rejected":
                reason = d.reason or "unknown"
                rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
        
        return {
            "input_count": total,
            "output_count": accepted,
            "rejection_rate": rejected / total if total > 0 else 0.0,
            "rejection_reasons": rejection_reasons
        }


def sample_decisions(
    decisions: list[Decision],
    threshold: int = 500,
    per_reason: int = 50
) -> tuple[list[Decision], dict[str, Any]]:
    """
    Convenience function to sample decisions and compute stats.
    
    Args:
        decisions: List of Decision objects
        threshold: Max decisions before sampling kicks in
        per_reason: Number of rejected decisions to keep per reason
        
    Returns:
        Tuple of (sampled_decisions, stats)
    """
    sampler = DecisionSampler(threshold=threshold, per_reason=per_reason)
    
    # Compute stats from FULL list (before sampling)
    stats = sampler.compute_stats(decisions)
    
    # Sample decisions
    sampled, was_sampled = sampler.sample(decisions)
    
    # Add sampling info to stats
    stats["sampled"] = was_sampled
    stats["sampled_count"] = len(sampled)
    
    return sampled, stats

