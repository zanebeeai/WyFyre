from __future__ import annotations

from typing import Any


class CvFeedbackAdapter:
    """Placeholder extension point for future CV->radar feedback."""

    def adjust_target_confidence(self, target_confidence: float, target_payload: dict[str, Any]) -> float:
        _ = target_payload
        return max(0.0, min(1.0, target_confidence))
