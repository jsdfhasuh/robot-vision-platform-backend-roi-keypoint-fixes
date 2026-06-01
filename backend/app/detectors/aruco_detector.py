from __future__ import annotations

import cv2
import numpy as np

from app.core.logging import get_logger
from app.detectors.base import BaseDetector, DetectResult

logger = get_logger(__name__)


class ArucoDetector(BaseDetector):
    """ArUco 标记检测器。

    推荐现场快速落地：在机器人末端或关键部位贴 ArUco/AprilTag 类标记，检测中心点位移。
    依赖 cv2.aruco；如果当前 OpenCV 不带 aruco 模块，会返回明确提示。
    """

    detector_type = "aruco"

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self.prev_center: tuple[float, float] | None = None
        self.dictionary_name = self.config.get("dictionary", "DICT_4X4_50")
        self.target_id = self.config.get("target_id")
        self._aruco_ready = hasattr(cv2, "aruco")
        self.dictionary = None
        self.parameters = None

        if self._aruco_ready:
            dictionary_id = getattr(cv2.aruco, self.dictionary_name, cv2.aruco.DICT_4X4_50)
            self.dictionary = cv2.aruco.getPredefinedDictionary(dictionary_id)
            self.parameters = cv2.aruco.DetectorParameters()
            logger.info("aruco detector initialized dictionary=%s target_id=%s", self.dictionary_name, self.target_id)
        else:
            logger.warning("aruco detector unavailable: cv2.aruco missing")

    def detect(self, frame: np.ndarray) -> DetectResult:
        if frame is None or frame.size == 0:
            return DetectResult(False, 0.0, 0.0, "empty frame")
        if not self._aruco_ready:
            return DetectResult(False, 0.0, 0.0, "cv2.aruco unavailable; install opencv-contrib-python-headless")

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        detector = cv2.aruco.ArucoDetector(self.dictionary, self.parameters)
        corners, ids, _ = detector.detectMarkers(gray)

        if ids is None or len(ids) == 0:
            return DetectResult(False, 0.0, 0.0, "aruco marker not found")

        ids_flat = ids.flatten().tolist()
        marker_index = 0
        if self.target_id is not None:
            target_id = int(self.target_id)
            if target_id not in ids_flat:
                return DetectResult(False, 0.0, 0.0, f"target aruco id {target_id} not found")
            marker_index = ids_flat.index(target_id)

        pts = corners[marker_index][0]
        center_x = float(pts[:, 0].mean())
        center_y = float(pts[:, 1].mean())
        center = (center_x, center_y)

        if self.prev_center is None:
            distance = 0.0
        else:
            distance = float(np.linalg.norm(np.array(center) - np.array(self.prev_center)))
        self.prev_center = center

        x1, y1 = pts[:, 0].min(), pts[:, 1].min()
        x2, y2 = pts[:, 0].max(), pts[:, 1].max()
        marker_id = ids_flat[marker_index]

        return DetectResult(
            target_found=True,
            motion_distance=distance,
            confidence=1.0,
            message=f"aruco_id={marker_id}, displacement={distance:.2f}px",
            center=center,
            bbox=(float(x1), float(y1), float(x2), float(y2)),
            metadata={"marker_id": marker_id, "ids": ids_flat},
        )
