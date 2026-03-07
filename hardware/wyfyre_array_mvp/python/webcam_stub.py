from __future__ import annotations

from pathlib import Path
from typing import Optional

try:
    import cv2
except ImportError:
    cv2 = None


class WebcamCapture:
    def __init__(self, device_index: int = 0) -> None:
        self.device_index = device_index
        self._cap = None

    def start(self) -> bool:
        if cv2 is None:
            return False
        self._cap = cv2.VideoCapture(self.device_index)
        return bool(self._cap.isOpened())

    def stop(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def capture_frame_to_path(self, output_dir: Path, timestamp_ms: int) -> Optional[str]:
        if self._cap is None or cv2 is None:
            return None

        ok, frame = self._cap.read()
        if not ok:
            return None

        output_dir.mkdir(parents=True, exist_ok=True)
        file_path = output_dir / f"cam_{timestamp_ms}.jpg"
        cv2.imwrite(str(file_path), frame)
        return str(file_path)
