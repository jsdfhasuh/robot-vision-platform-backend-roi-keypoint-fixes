from __future__ import annotations

import cv2

from app.core.logging import get_logger

logger = get_logger(__name__)


class FrameReader:
    """RTSP 拉流封装。

    Worker 只管调用 read/reconnect/close，便于后续替换为 FFmpeg/GStreamer/MediaMTX 订阅。
    """

    def __init__(self, url: str, camera_id: int):
        self.url = url
        self.camera_id = camera_id
        self.cap = None

    def open(self):
        self.close()
        logger.info("opening rtsp camera_id=%s url=%s", self.camera_id, self.url)
        self.cap = cv2.VideoCapture(self.url)

    def read(self):
        if self.cap is None:
            self.open()
        return self.cap.read() if self.cap else (False, None)

    def reconnect(self, url: str | None = None):
        if url and url != self.url:
            self.url = url
        logger.info("rtsp reconnect attempted camera_id=%s", self.camera_id)
        self.open()

    def close(self):
        if self.cap is not None:
            self.cap.release()
            self.cap = None
