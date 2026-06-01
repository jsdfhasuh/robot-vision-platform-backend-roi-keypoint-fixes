from __future__ import annotations

from collections import deque
from datetime import datetime
import time

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.config import settings
from app.database import SessionLocal
from app.detectors.base import DetectResult
from app.models.camera import Camera
from app.services import event_service, snapshot_service, status_service, video_stream_service, roi_service
from app.utils.annotator import draw_detection
from app.services.runtime_service import RuntimeContext
from app.workers.frame_reader import FrameReader

logger = get_logger(__name__)


class CameraWorker:
    """单摄像头检测 Worker。

    Worker 只负责主循环编排，同时维护运行健康状态：
    - RTSP 是否连接
    - 最近读帧/检测时间
    - 实际 FPS
    - 错误次数和最近错误
    - 配置版本和运行时重置次数
    """

    def __init__(self, camera_id: int):
        self.camera_id = camera_id
        self.running = False
        self.runtime = RuntimeContext(camera_id)
        self.last_result: DetectResult | None = None
        self.last_tracker_stats = None
        self.last_annotated_path: str | None = None
        self.last_status: str | None = None
        self.open_event_ids: dict[str, int] = {}

        self.started_at: datetime | None = None
        self.stopped_at: datetime | None = None
        self.last_frame_time: datetime | None = None
        self.last_detect_time: datetime | None = None
        self.last_error_time: datetime | None = None
        self.last_error: str | None = None
        self.loop_count = 0
        self.frames_read = 0
        self.detect_count = 0
        self.error_count = 0
        self.reconnect_count = 0
        self.consecutive_read_failures = 0
        self.rtsp_connected = False
        self._frame_ticks: deque[float] = deque(maxlen=60)
        self._detect_ticks: deque[float] = deque(maxlen=60)

    def stop(self) -> None:
        logger.info("camera worker stop signal camera_id=%s", self.camera_id)
        self.running = False

    def _actual_rate(self, ticks: deque[float]) -> float:
        if len(ticks) < 2:
            return 0.0
        span = ticks[-1] - ticks[0]
        if span <= 0:
            return 0.0
        return round((len(ticks) - 1) / span, 3)

    def _note_frame(self, frame=None) -> None:
        self.frames_read += 1
        video_stream_service.frame_cache.publish_raw(self.camera_id, frame)
        self.last_frame_time = datetime.utcnow()
        self._frame_ticks.append(time.time())
        self.rtsp_connected = True
        self.consecutive_read_failures = 0

    def _note_detect(self) -> None:
        self.detect_count += 1
        self.last_detect_time = datetime.utcnow()
        self._detect_ticks.append(time.time())

    def _note_reconnect(self) -> None:
        self.reconnect_count += 1
        self.rtsp_connected = False

    def _note_error(self, message: str, *, exc: Exception | None = None) -> None:
        self.error_count += 1
        self.last_error = f"{message}: {exc}" if exc else message
        self.last_error_time = datetime.utcnow()
        logger.warning("worker error camera_id=%s error=%s", self.camera_id, self.last_error)

    def get_debug_state(self) -> dict:
        runtime_health = self.runtime.health()
        return {
            "camera_id": self.camera_id,
            "running": self.running,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "uptime_seconds": round((datetime.utcnow() - self.started_at).total_seconds(), 3) if self.started_at else 0,
            "rtsp_connected": self.rtsp_connected,
            "last_frame_time": self.last_frame_time,
            "last_detect_time": self.last_detect_time,
            "fps_actual": self._actual_rate(self._frame_ticks),
            "detect_fps_actual": self._actual_rate(self._detect_ticks),
            "loop_count": self.loop_count,
            "frames_read": self.frames_read,
            "detect_count": self.detect_count,
            "error_count": self.error_count,
            "reconnect_count": self.reconnect_count,
            "consecutive_read_failures": self.consecutive_read_failures,
            "last_error": self.last_error,
            "last_error_time": self.last_error_time,
            "last_status": self.last_status,
            "last_annotated_path": self.last_annotated_path,
            "last_annotated_url": snapshot_service.storage_url(self.last_annotated_path),
            "detector_signature": self.runtime.detector_signature,
            "last_config_version": self.runtime.last_config_version,
            "runtime": runtime_health,
            "open_event_ids": self.open_event_ids,
            "rule": self.runtime.rule_detail,
            "tracker": self.last_tracker_stats.to_dict() if self.last_tracker_stats else None,
            "last_result": self._result_to_dict(self.last_result) if self.last_result else None,
        }

    def _result_to_dict(self, result: DetectResult) -> dict:
        metadata = dict(result.metadata or {})
        if "keypoints" in metadata and isinstance(metadata["keypoints"], list):
            metadata["keypoints_count"] = len(metadata["keypoints"])
        return {
            "target_found": result.target_found,
            "motion_distance": result.motion_distance,
            "confidence": result.confidence,
            "message": result.message,
            "center": result.center,
            "bbox": result.bbox,
            "metadata": metadata,
        }

    def _get_camera(self, db: Session) -> Camera | None:
        return db.query(Camera).filter(Camera.id == self.camera_id).first()

    def _detect_safely(self, roi_frame) -> DetectResult:
        try:
            return self.runtime.run_detector(roi_frame)
        except Exception as exc:
            logger.exception("detector error camera_id=%s", self.camera_id)
            self._note_error("detector error", exc=exc)
            return DetectResult(False, 0.0, 0.0, f"detector error: {exc}")

    def _save_status_annotated(self, roi_frame, result: DetectResult, status: str, camera: Camera) -> None:
        path = snapshot_service.save_annotated_image(
            roi_frame,
            result,
            status,
            camera=camera,
            rule_detail=self.runtime.rule_detail,
            suffix="status_annotated",
        )
        if path:
            self.last_annotated_path = path

    def _process_frame(self, db: Session, camera: Camera, frame) -> None:
        roi_frame, roi_ctx = roi_service.crop_and_mask(frame, camera)
        raw_result = self._detect_safely(roi_frame)
        raw_result = roi_service.apply_result_filter(raw_result, camera, roi_ctx)
        result, stats = self.runtime.update_tracker(raw_result)
        self.last_tracker_stats = stats

        status, last_motion_time, message = self.runtime.update_rule(result)
        self._note_detect()
        result.metadata = dict(result.metadata or {})
        result.metadata["rule_detail"] = self.runtime.rule_detail
        self.last_result = result

        final_message = f"{camera.detector_type}: {message}"
        status_service.update_status(
            db,
            self.camera_id,
            status,
            last_motion_time=last_motion_time,
            confidence=result.confidence,
            message=final_message,
            previous_status=self.last_status,
        )

        try:
            # 给前端视频预览提供实时标注帧：ROI 内画检测结果，再贴回原图。
            x1, y1, x2, y2 = roi_ctx.bounds
            annotated_roi = draw_detection(
                roi_frame,
                result,
                status,
                rule_detail=self.runtime.rule_detail,
                camera_name=camera.name,
                detector_type=camera.detector_type,
                roi=[x1, y1, x2, y2],
            )
            annotated_full = snapshot_service.draw_roi_on_full_frame(frame, annotated_roi, (x1, y1, x2, y2))
            video_stream_service.frame_cache.publish_annotated(self.camera_id, annotated_full)
        except Exception as exc:
            self._note_error("publish annotated frame failed", exc=exc)

        if status in {"STOPPED", "UNKNOWN"} or status != self.last_status:
            self._save_status_annotated(roi_frame, result, status, camera)

        event_service.handle_status_change(
            db,
            camera=camera,
            status=status,
            open_event_ids=self.open_event_ids,
            full_frame=frame,
            roi_frame=roi_frame,
            result=result,
            message=final_message,
            rule_detail=self.runtime.rule_detail,
        )
        try:
            event_service.sample_open_event_frames(
                db,
                camera=camera,
                open_event_ids=self.open_event_ids,
                full_frame=frame,
                roi_frame=roi_frame,
                result=result,
                status=status,
                message=final_message,
                rule_detail=self.runtime.rule_detail,
                interval_seconds=settings.event_frame_sample_seconds,
                max_frames_per_event=settings.event_frame_max_per_event,
            )
        except Exception as exc:
            self._note_error("event sample frame failed", exc=exc)
        self.last_status = status

    def _mark_offline(self, db: Session, camera: Camera) -> None:
        self.consecutive_read_failures += 1
        self.rtsp_connected = False
        last_motion_time = self.runtime.rule.last_motion_time if self.runtime.rule else None
        status_service.update_status(
            db,
            self.camera_id,
            "OFFLINE",
            last_motion_time=last_motion_time,
            confidence=0.0,
            message="rtsp read failed",
            previous_status=self.last_status,
        )
        event_service.handle_status_change(
            db,
            camera=camera,
            status="OFFLINE",
            open_event_ids=self.open_event_ids,
            full_frame=None,
            roi_frame=None,
            result=None,
            message="rtsp read failed",
            rule_detail={"final_status": "OFFLINE", "reason": "RTSP 视频流读取失败，判定离线。"},
        )
        self.last_status = "OFFLINE"

    def run(self) -> None:
        self.running = True
        self.started_at = datetime.utcnow()
        self.stopped_at = None
        logger.info("camera worker run started camera_id=%s", self.camera_id)
        db = SessionLocal()
        reader: FrameReader | None = None
        try:
            camera = self._get_camera(db)
            if not camera:
                logger.error("camera not found, worker exit camera_id=%s", self.camera_id)
                return

            self.runtime.ensure(camera)
            logger.info(
                "opening rtsp camera_id=%s name=%s detector=%s fps=%s config_version=%s",
                camera.id,
                camera.name,
                camera.detector_type,
                camera.fps_limit,
                camera.config_version,
            )
            reader = FrameReader(camera.rtsp_url, self.camera_id)
            reader.open()

            while self.running:
                self.loop_count += 1
                camera = self._get_camera(db)
                if not camera or not camera.enabled:
                    logger.info("camera disabled or deleted, worker exit camera_id=%s", self.camera_id)
                    break

                try:
                    previous_version = self.runtime.last_config_version
                    self.runtime.ensure(camera)
                    if previous_version is not None and previous_version != self.runtime.last_config_version:
                        # 配置变化后清理上一轮检测结果，避免前端误读旧结果。
                        self.last_result = None
                        self.last_tracker_stats = None
                        self.last_annotated_path = None

                    sleep_interval = 1.0 / max(camera.fps_limit, 1)

                    if reader.url != camera.rtsp_url:
                        logger.info("rtsp url changed camera_id=%s, reconnect", self.camera_id)
                        self._note_reconnect()
                        reader.reconnect(camera.rtsp_url)

                    ok, frame = reader.read() if reader else (False, None)
                    if not ok or frame is None:
                        self._mark_offline(db, camera)
                        self._note_error("rtsp read failed")
                        logger.warning("rtsp read failed camera_id=%s, reconnecting", self.camera_id)
                        time.sleep(2)
                        self._note_reconnect()
                        reader.reconnect(camera.rtsp_url)
                        continue

                    self._note_frame(frame)
                    self._process_frame(db, camera, frame)
                    time.sleep(sleep_interval)
                except Exception as exc:
                    logger.exception("worker loop error camera_id=%s", self.camera_id)
                    self._note_error("worker loop error", exc=exc)
                    time.sleep(2)

        finally:
            self.running = False
            self.stopped_at = datetime.utcnow()
            self.rtsp_connected = False
            if reader:
                reader.close()
            db.close()
            logger.info("camera worker run stopped camera_id=%s", self.camera_id)
