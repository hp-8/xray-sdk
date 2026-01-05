"""
X-Ray SDK - Debug non-deterministic, multi-step algorithmic systems.

This SDK provides transparency into multi-step decision processes by capturing
inputs, candidates, filters, outcomes, and reasoning at each step.
"""

from xray.models import Decision, Step, Evidence, RunInput
from xray.client import XRay

__all__ = ["XRay", "Decision", "Step", "Evidence", "RunInput"]
__version__ = "0.1.0"

