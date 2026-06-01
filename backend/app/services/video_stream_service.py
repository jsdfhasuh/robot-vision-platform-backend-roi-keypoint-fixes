from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any, Generator

import cv2
import numpy as np

from app.core.logging import get_logger

logger = get_logger(__name__)


class FrameCache:
    """线程安全的摄像头最新帧缓存。

    设计目标：
    - Worker 负责持续拉 RTSP 和检测；
    - 前端视频流接口不重复拉摄像头，而是复用 Worker 最新帧；
    - 没有前端观看时也不影响检测主流程。
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._raw_frames: dict[int, np.ndarray] = {}
        self._annotated_frames: dict[int, np.ndarray] = {}
        self._raw_times: dict[int, datetime] = {}
        self._annotated_times: dict[int, datetime] = {}

    def publish_raw(self, camera_id: int, frame: np.ndarray | None) -> None:
        if frame is None:
            return
        with self._lock:
            self._raw_frames[camera_id] = frame.copy()
            self._raw_times[camera_id] = datetime.utcnow()

    def publish_annotated(self, camera_id: int, frame: np.ndarray | None) -> None:
        if frame is None:
            return
        with self._lock:
            self._annotated_frames[camera_id] = frame.copy()
            self._annotated_times[camera_id] = datetime.utcnow()

    def get_frame(self, camera_id: int, *, annotated: bool = False) -> tuple[np.ndarray | None, datetime | None]:
        with self._lock:
            if annotated:
                frame = self._annotated_frames.get(camera_id)
                ts = self._annotated_times.get(camera_id)
                if frame is not None:
                    return frame.copy(), ts
            frame = self._raw_frames.get(camera_id)
            ts = self._raw_times.get(camera_id)
            return (frame.copy(), ts) if frame is not None else (None, None)

    def info(self, camera_id: int | None = None) -> dict[str, Any] | list[dict[str, Any]]:
        with self._lock:
            camera_ids = sorted(set(self._raw_frames.keys()) | set(self._annotated_frames.keys()))
            if camera_id is not None:
                camera_ids = [camera_id]
            rows = []
            now = datetime.utcnow()
            for cid in camera_ids:
                raw = self._raw_frames.get(cid)
                ann = self._annotated_frames.get(cid)
                raw_ts = self._raw_times.get(cid)
                ann_ts = self._annotated_times.get(cid)
                rows.append(
                    {
                        "camera_id": cid,
                        "has_raw_frame": raw is not None,
                        "has_annotated_frame": ann is not None,
                        "raw_shape": list(raw.shape) if raw is not None else None,
                        "annotated_shape": list(ann.shape) if ann is not None else None,
                        "raw_updated_at": raw_ts,
                        "annotated_updated_at": ann_ts,
                        "raw_age_seconds": round((now - raw_ts).total_seconds(), 3) if raw_ts else None,
                        "annotated_age_seconds": round((now - ann_ts).total_seconds(), 3) if ann_ts else None,
                    }
                )
            return rows[0] if camera_id is not None and rows else {
                "camera_id": camera_id,
                "has_raw_frame": False,
                "has_annotated_frame": False,
                "raw_shape": None,
                "annotated_shape": None,
                "raw_updated_at": None,
                "annotated_updated_at": None,
                "raw_age_seconds": None,
                "annotated_age_seconds": None,
            } if camera_id is not None else rows


frame_cache = FrameCache()


def _resize_keep_ratio(frame: np.ndarray, max_width: int | None = None) -> np.ndarray:
    if not max_width or max_width <= 0:
        return frame
    h, w = frame.shape[:2]
    if w <= max_width:
        return frame
    new_h = int(h * (max_width / w))
    return cv2.resize(frame, (max_width, new_h), interpolation=cv2.INTER_AREA)


def encode_jpeg(frame: np.ndarray, *, quality: int = 80, max_width: int | None = None) -> bytes | None:
    if frame is None:
        return None
    quality = max(10, min(int(quality), 100))
    try:
        out = _resize_keep_ratio(frame, max_width=max_width)
        ok, buf = cv2.imencode(".jpg", out, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        if not ok:
            return None
        return buf.tobytes()
    except Exception:
        logger.exception("jpeg encode failed")
        return None


def placeholder_frame(camera_id: int, message: str = "No video frame") -> np.ndarray:
    img = np.zeros((360, 640, 3), dtype=np.uint8)
    lines = [
        f"Camera {camera_id}",
        message,
        "Start worker or wait for RTSP frames.",
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    ]
    y = 90
    for line in lines:
        cv2.putText(img, line, (40, y), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2)
        y += 42
    return img


def get_latest_jpeg(
    camera_id: int,
    *,
    annotated: bool = False,
    quality: int = 80,
    max_width: int | None = None,
    placeholder: bool = True,
) -> tuple[bytes | None, datetime | None, bool]:
    frame, ts = frame_cache.get_frame(camera_id, annotated=annotated)
    is_placeholder = False
    if frame is None and placeholder:
        frame = placeholder_frame(camera_id)
        is_placeholder = True
    if frame is None:
        return None, ts, is_placeholder
    return encode_jpeg(frame, quality=quality, max_width=max_width), ts, is_placeholder


def mjpeg_generator(
    camera_id: int,
    *,
    annotated: bool = False,
    fps: float = 8.0,
    quality: int = 80,
    max_width: int | None = None,
) -> Generator[bytes, None, None]:
    fps = max(0.2, min(float(fps or 8.0), 25.0))
    interval = 1.0 / fps
    logger.info(
        "mjpeg stream opened camera_id=%s annotated=%s fps=%s quality=%s max_width=%s",
        camera_id,
        annotated,
        fps,
        quality,
        max_width,
    )
    try:
        while True:
            jpg, _, _ = get_latest_jpeg(
                camera_id,
                annotated=annotated,
                quality=quality,
                max_width=max_width,
                placeholder=True,
            )
            if jpg:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Cache-Control: no-cache\r\n\r\n" + jpg + b"\r\n"
                )
            time.sleep(interval)
    except GeneratorExit:
        logger.info("mjpeg stream closed camera_id=%s annotated=%s", camera_id, annotated)
        return
