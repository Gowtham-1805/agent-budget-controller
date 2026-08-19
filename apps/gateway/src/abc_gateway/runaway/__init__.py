"""Runaway detection and human review.

A deterministic circuit breaker: more than 20% of an agent's monthly budget
inside a rolling 60-minute window pauses it and requires a human to resume it.
"""

from .detector import (
    COUNTED_KINDS,
    DetectionResult,
    RunawayDetector,
    bucket_key,
    window_buckets,
)
from .review import AuditRecord, ReviewError, ReviewService

__all__ = [
    "COUNTED_KINDS",
    "AuditRecord",
    "DetectionResult",
    "ReviewError",
    "ReviewService",
    "RunawayDetector",
    "bucket_key",
    "window_buckets",
]
