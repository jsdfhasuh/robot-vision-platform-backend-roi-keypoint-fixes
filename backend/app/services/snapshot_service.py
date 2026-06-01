from __future__ import annotations

import os
import time
from typing import Any

import cv2
import numpy as np

from app.core.config import settings
from app.core.logging import get_logger
from app.detectors.base import DetectResult
from app.models.camera import Camera
from app.utils.annotator import draw_detection
from app.utils.paths import storage_path_to_url

logger = get_logger(__name__)


def snapshots_dir() -> str:
    path = os.path.join(settings.storage_dir, "snapshots")
    os.makedirs(path, exist_ok=True)
    return path


def save_image(frame, *, camera_id: int, suffix: str = "snapshot") -> str | None:
    if frame is None:
        return None
    try:
        path = os.path.join(snapshots_dir(), f"camera_{camera_id}_{int(time.time() * 1000)}_{suffix}.jpg")
        ok = cv2.imwrite(path, frame)
        if ok:
            logger.info("image saved camera_id=%s suffix=%s path=%s", camera_id, suffix, path)
            return path
        logger.warning("image save failed camera_id=%s path=%s", camera_id, path)
    except Exception:
        logger.exception("image save exception camera_id=%s suffix=%s", camera_id, suffix)
    return None


def save_annotated_image(
    frame,
    result: DetectResult,
    status: str,
    *,
    camera: Camera | None = None,
    rule_detail: dict[str, Any] | None = None,
    event_type: str | None = None,
    suffix: str = "annotated",
) -> str | None:
    try:
        annotated = draw_detection(
            frame,
            result,
            status,
            rule_detail=rule_detail,
            camera_name=(camera.name if camera else None),
            detector_type=(camera.detector_type if camera else None),
            roi=(camera.roi if camera else None),
            event_type=event_type,
        )
        return save_image(annotated, camera_id=(camera.id if camera else 0), suffix=suffix)
    except Exception:
        logger.exception("annotated snapshot save exception camera_id=%s", getattr(camera, "id", None))
        return None


def storage_url(path: str | None) -> str | None:
    return storage_path_to_url(path)


def crop_roi(frame, roi):
    if not roi or len(roi) != 4:
        return frame
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = [int(v) for v in roi]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 <= x1 or y2 <= y1:
        return frame
    return frame[y1:y2, x1:x2]


def roi_bounds(frame, roi) -> tuple[int, int, int, int]:
    h, w = frame.shape[:2]
    if not roi or len(roi) != 4:
        return 0, 0, w, h
    x1, y1, x2, y2 = [int(v) for v in roi]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 <= x1 or y2 <= y1:
        return 0, 0, w, h
    return x1, y1, x2, y2


def decode_upload_image(file_bytes: bytes):
    arr = np.frombuffer(file_bytes, np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def draw_roi_on_full_frame(full_frame, annotated_roi, roi: tuple[int, int, int, int]):
    x1, y1, x2, y2 = roi
    annotated_full = full_frame.copy()
    annotated_full[y1:y2, x1:x2] = annotated_roi
    cv2.rectangle(annotated_full, (x1, y1), (x2, y2), (0, 255, 255), 2)
    cv2.putText(
        annotated_full,
        "ROI",
        (x1 + 8, max(24, y1 + 24)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.75,
        (0, 255, 255),
        2,
    )
    return annotated_full
