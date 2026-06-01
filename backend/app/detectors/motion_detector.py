from __future__ import annotations

import cv2
import numpy as np

from app.detectors.base import BaseDetector, DetectResult
from app.core.logging import get_logger

logger = get_logger(__name__)


class MotionDetector(BaseDetector):
    """ROI 内运动检测器。

    适合 MVP：不需要训练模型，先跑通平台闭环。
    为了降低光照变化影响，这里做了灰度、模糊、形态学开运算和最小变化面积过滤。
    """

    detector_type = "motion"

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self.prev_gray: np.ndarray | None = None
        self.diff_threshold = int(self.config.get("diff_threshold", 25))
        self.min_area = int(self.config.get("min_area", 80))
        self.blur_size = int(self.config.get("blur_size", 5))
        if self.blur_size % 2 == 0:
            self.blur_size += 1
        logger.info("motion detector initialized diff_threshold=%s min_area=%s blur_size=%s", self.diff_threshold, self.min_area, self.blur_size)

    def detect(self, frame: np.ndarray) -> DetectResult:
        if frame is None or frame.size == 0:
            return DetectResult(False, 0.0, 0.0, "empty frame")

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (self.blur_size, self.blur_size), 0)

        if self.prev_gray is None:
            self.prev_gray = gray
            return DetectResult(True, 0.0, 0.1, "motion warmup")

        diff = cv2.absdiff(self.prev_gray, gray)
        self.prev_gray = gray

        _, thresh = cv2.threshold(diff, self.diff_threshold, 255, cv2.THRESH_BINARY)
        kernel = np.ones((3, 3), np.uint8)
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)
        thresh = cv2.dilate(thresh, kernel, iterations=1)

        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        valid_areas = [cv2.contourArea(c) for c in contours if cv2.contourArea(c) >= self.min_area]
        changed_area = float(sum(valid_areas))
        total_pixels = float(thresh.shape[0] * thresh.shape[1])

        # 放大成更好配置的分数。默认 motion_threshold=5，大概对应 0.5% ROI 面积变化。
        motion_score = changed_area / max(total_pixels, 1.0) * 1000.0
        confidence = min(1.0, motion_score / 20.0)

        return DetectResult(
            target_found=True,
            motion_distance=motion_score,
            confidence=confidence,
            message=f"motion_score={motion_score:.2f}, areas={len(valid_areas)}",
            metadata={"changed_area": changed_area, "contours": len(valid_areas)},
        )
