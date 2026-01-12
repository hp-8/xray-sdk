"""
X-Ray Configuration Module

Centralizes all configuration values to eliminate magic numbers
and provide single source of truth for system parameters.

Implements: Code Quality Best Practice (Single Responsibility)
PRD Reference: Section 6 (Sampling Configuration)
"""

import os
from typing import Final


# API Configuration
DEFAULT_API_URL: Final[str] = "http://localhost:8000"
DEFAULT_API_TIMEOUT: Final[float] = 10.0

# Sampling Configuration - ADR-001
DEFAULT_SAMPLE_THRESHOLD: Final[int] = 500
DEFAULT_SAMPLE_PER_REASON: Final[int] = 50

# Storage Limits - PRD Section 11
MAX_DECISIONS_PER_STEP: Final[int] = 100_000
MAX_EVIDENCE_PER_STEP: Final[int] = 1000
MAX_EVIDENCE_SIZE_BYTES: Final[int] = 10 * 1024 * 1024  # 10MB

# SDK Configuration
DEFAULT_ENABLED: Final[bool] = True


class XRayConfig:
    """
    X-Ray SDK Configuration.
    
    Usage:
        config = XRayConfig()
        xray = XRay(api_url=config.api_url, sample_threshold=config.sample_threshold)
    """
    
    def __init__(self) -> None:
        self.api_url: str = os.getenv("XRAY_API_URL", DEFAULT_API_URL)
        self.enabled: bool = os.getenv("XRAY_ENABLED", "true").lower() == "true"
        self.timeout: float = float(os.getenv("XRAY_API_TIMEOUT", str(DEFAULT_API_TIMEOUT)))
        
        # Sampling config
        self.sample_threshold: int = int(os.getenv("XRAY_SAMPLE_THRESHOLD", str(DEFAULT_SAMPLE_THRESHOLD)))
        self.sample_per_reason: int = int(os.getenv("XRAY_SAMPLE_PER_REASON", str(DEFAULT_SAMPLE_PER_REASON)))
    
    @staticmethod
    def from_env() -> "XRayConfig":
        """Create configuration from environment variables."""
        return XRayConfig()
