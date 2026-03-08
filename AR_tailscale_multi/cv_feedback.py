from __future__ import annotations

from typing import Any


class CvFeedbackAdapter:
    def adjust_target_confidence(self, confidence: float, _target_context: dict[str, Any]) -> float:
        return confidence
