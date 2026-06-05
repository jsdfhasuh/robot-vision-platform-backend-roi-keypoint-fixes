from __future__ import annotations

from collections import deque
from datetime import datetime
from types import SimpleNamespace
from typing import Callable
import time

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.database import SessionLocal
from app.detectors.base import DetectResult
from app.models.camera import Camera
from app.models.detection_task import DetectionRule, DetectionTask
from app.services import event_service, roi_service, snapshot_service, status_service, video_stream_service
from app.services.runtime_service import RuntimeContext
from app.utils.annotator import draw_detection
from app.workers.frame_reader import FrameReader

logger = get_logger(__name__)


def result_to_dict(result: DetectResult | None) -> dict | None:
    if result is None:
        return None
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


class TaskRuntime:
    """Runtime state for one detection task on a shared camera stream."""

    def __init__(self, task_id: int, camera_id: int):
        self.task_id = task_id
        self.camera_id = camera_id
        self.running = True
        self.runtime = RuntimeContext(camera_id)
        self.last_result: DetectResult | None = None
        self.last_tracker_stats = None
        self.last_annotated_path: str | None = None
        self.last_status: str | None = None
        self.open_event_ids: dict[str, int] = {}

        self.started_at = datetime.utcnow()
        self.stopped_at: datetime | None = None
        self.last_frame_time: datetime | None = None
        self.last_detect_time: datetime | None = None
        self.last_error_time: datetime | None = None
        self.last_error: str | None = None
        self.detect_count = 0
        self.error_count = 0
        self._detect_ticks: deque[float] = deque(maxlen=60)
        self._next_detect_at = 0.0

    def stop(self) -> None:
        self.running = False
        self.stopped_at = datetime.utcnow()

    def _actual_rate(self, ticks: deque[float]) -> float:
        if len(ticks) < 2:
            return 0.0
        span = ticks[-1] - ticks[0]
        if span <= 0:
            return 0.0
        return round((len(ticks) - 1) / span, 3)

    def _note_error(self, message: str, *, exc: Exception | None = None) -> None:
        self.error_count += 1
        self.last_error = f"{message}: {exc}" if exc else message
        self.last_error_time = datetime.utcnow()
        logger.warning("task runtime error task_id=%s camera_id=%s error=%s", self.task_id, self.camera_id, self.last_error)

    def should_process(self, task: DetectionTask) -> bool:
        now = time.time()
        if now < self._next_detect_at:
            return False
        self._next_detect_at = now + (1.0 / max(int(task.fps_limit or 1), 1))
        return True

    def _task_camera_view(self, camera: Camera, task: DetectionTask):
        return SimpleNamespace(
            id=camera.id,
            name=camera.name,
            detector_type=task.detector_type,
            detector_config=task.detector_config,
            roi=task.roi,
        )

    def _detect_safely(self, task: DetectionTask, roi_frame) -> DetectResult:
        try:
            return self.runtime.run_detector(roi_frame)
        except Exception as exc:
            logger.exception("detector error task_id=%s camera_id=%s", task.id, task.camera_id)
            self._note_error("detector error", exc=exc)
            return DetectResult(False, 0.0, 0.0, f"detector error: {exc}")

    def _save_status_annotated(self, roi_frame, result: DetectResult, status: str, camera: Camera, task: DetectionTask) -> None:
        path = snapshot_service.save_annotated_image(
            roi_frame,
            result,
            status,
            camera=self._task_camera_view(camera, task),
            rule_detail=self.runtime.rule_detail,
            suffix=f"task_{task.id}_status_annotated",
        )
        if path:
            self.last_annotated_path = path

    def process_frame(self, db: Session, camera: Camera, task: DetectionTask, rule: DetectionRule, frame) -> None:
        if not self.running:
            return
        task_view = self._task_camera_view(camera, task)
        roi_frame, roi_ctx = roi_service.crop_and_mask(frame, task_view)
        self.runtime.ensure_task(task, rule)
        raw_result = self._detect_safely(task, roi_frame)
        raw_result = roi_service.apply_result_filter(raw_result, task_view, roi_ctx)
        result, stats = self.runtime.update_tracker(raw_result)
        self.last_tracker_stats = stats

        status, last_motion_time, message = self.runtime.update_rule(result)
        self.detect_count += 1
        now = datetime.utcnow()
        self.last_frame_time = now
        self.last_detect_time = now
        self._detect_ticks.append(time.time())
        result.metadata = dict(result.metadata or {})
        result.metadata["rule_detail"] = self.runtime.rule_detail
        result.metadata["task_id"] = task.id
        result.metadata["rule_id"] = rule.id
        result.metadata["rule_version"] = rule.version
        self.last_result = result

        final_message = f"{task.detector_type}: {message}"
        detail = self.runtime.rule_detail or {}
        status_service.update_task_status(
            db,
            task.id,
            camera.id,
            status,
            last_frame_time=now,
            last_motion_time=last_motion_time,
            last_detect_time=now,
            confidence=result.confidence,
            message=final_message,
            reason_code=str(detail.get("reason_code") or ""),
            detail=detail,
            result=result_to_dict(result),
            rule_version=rule.version,
            previous_status=self.last_status,
        )

        try:
            x1, y1, x2, y2 = roi_ctx.bounds
            annotated_roi = draw_detection(
                roi_frame,
                result,
                status,
                rule_detail=self.runtime.rule_detail,
                camera_name=camera.name,
                detector_type=task.detector_type,
                roi=[x1, y1, x2, y2],
            )
            annotated_full = snapshot_service.draw_roi_on_full_frame(frame, annotated_roi, (x1, y1, x2, y2))
            video_stream_service.frame_cache.publish_annotated(camera.id, annotated_full)
        except Exception as exc:
            self._note_error("publish annotated frame failed", exc=exc)

        if status in {"STOPPED", "UNKNOWN"} or status != self.last_status:
            self._save_status_annotated(roi_frame, result, status, camera, task)

        event_service.handle_status_change(
            db,
            camera=camera,
            task=task,
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
                task=task,
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

    def mark_offline(self, db: Session, camera: Camera, task: DetectionTask, rule: DetectionRule | None = None, message: str = "rtsp read failed") -> None:
        last_motion_time = self.runtime.rule.last_motion_time if self.runtime.rule else None
        detail = {
            "final_status": "OFFLINE",
            "reason_code": "RTSP_READ_FAILED",
            "reason": "RTSP video stream read failed; task marked offline.",
            "message": message,
        }
        status_service.update_task_status(
            db,
            task.id,
            camera.id,
            "OFFLINE",
            last_motion_time=last_motion_time,
            confidence=0.0,
            message=message,
            reason_code="RTSP_READ_FAILED",
            detail=detail,
            result=result_to_dict(self.last_result),
            rule_version=rule.version if rule else None,
            previous_status=self.last_status,
        )
        event_service.handle_status_change(
            db,
            camera=camera,
            task=task,
            status="OFFLINE",
            open_event_ids=self.open_event_ids,
            full_frame=None,
            roi_frame=None,
            result=None,
            message=message,
            rule_detail=detail,
        )
        self.last_status = "OFFLINE"

    def get_debug_state(self) -> dict:
        runtime_health = self.runtime.health()
        return {
            "task_id": self.task_id,
            "camera_id": self.camera_id,
            "running": self.running,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "uptime_seconds": round((datetime.utcnow() - self.started_at).total_seconds(), 3) if self.started_at else 0,
            "last_frame_time": self.last_frame_time,
            "last_detect_time": self.last_detect_time,
            "detect_fps_actual": self._actual_rate(self._detect_ticks),
            "detect_count": self.detect_count,
            "error_count": self.error_count,
            "last_error": self.last_error,
            "last_error_time": self.last_error_time,
            "last_status": self.last_status,
            "last_annotated_path": self.last_annotated_path,
            "last_annotated_url": snapshot_service.storage_url(self.last_annotated_path),
            "detector_signature": self.runtime.detector_signature,
            "last_config_version": self.runtime.last_config_version,
            "last_rule_version": self.runtime.last_rule_version,
            "runtime": runtime_health,
            "open_event_ids": self.open_event_ids,
            "rule": self.runtime.rule_detail,
            "tracker": self.last_tracker_stats.to_dict() if self.last_tracker_stats else None,
            "last_result": result_to_dict(self.last_result),
        }


class CameraStreamWorker:
    """One RTSP reader per camera, feeding all running detection task runtimes."""

    def __init__(self, camera_id: int, runtime_provider: Callable[[int], list[TaskRuntime]]):
        self.camera_id = camera_id
        self.runtime_provider = runtime_provider
        self.running = False
        self.started_at: datetime | None = None
        self.stopped_at: datetime | None = None
        self.last_frame_time: datetime | None = None
        self.last_error_time: datetime | None = None
        self.last_error: str | None = None
        self.loop_count = 0
        self.frames_read = 0
        self.error_count = 0
        self.reconnect_count = 0
        self.consecutive_read_failures = 0
        self.rtsp_connected = False
        self._frame_ticks: deque[float] = deque(maxlen=60)

    def stop(self) -> None:
        logger.info("camera stream stop signal camera_id=%s", self.camera_id)
        self.running = False

    def _actual_rate(self, ticks: deque[float]) -> float:
        if len(ticks) < 2:
            return 0.0
        span = ticks[-1] - ticks[0]
        if span <= 0:
            return 0.0
        return round((len(ticks) - 1) / span, 3)

    def _note_error(self, message: str, *, exc: Exception | None = None) -> None:
        self.error_count += 1
        self.last_error = f"{message}: {exc}" if exc else message
        self.last_error_time = datetime.utcnow()
        logger.warning("stream worker error camera_id=%s error=%s", self.camera_id, self.last_error)

    def _get_camera(self, db: Session) -> Camera | None:
        return db.query(Camera).filter(Camera.id == self.camera_id).first()

    def _get_task_and_rule(self, db: Session, task_id: int) -> tuple[DetectionTask | None, DetectionRule | None]:
        task = db.query(DetectionTask).filter(DetectionTask.id == task_id).first()
        if not task or not task.enabled:
            return None, None
        rule = db.query(DetectionRule).filter(DetectionRule.id == task.rule_id).first() if task.rule_id else None
        return task, rule

    def _active_sleep_interval(self, runtimes: list[TaskRuntime]) -> float:
        if not runtimes:
            return 0.2
        return 1.0 / max(1, len(runtimes), 3)

    def _mark_all_offline(self, db: Session, camera: Camera, runtimes: list[TaskRuntime], message: str) -> None:
        status_service.update_stream_status(db, camera.id, "OFFLINE", last_error=message)
        for runtime in runtimes:
            task, rule = self._get_task_and_rule(db, runtime.task_id)
            if not task:
                continue
            runtime.mark_offline(db, camera, task, rule, message)

    def get_debug_state(self) -> dict:
        return {
            "camera_id": self.camera_id,
            "running": self.running,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "uptime_seconds": round((datetime.utcnow() - self.started_at).total_seconds(), 3) if self.started_at else 0,
            "rtsp_connected": self.rtsp_connected,
            "last_frame_time": self.last_frame_time,
            "fps_actual": self._actual_rate(self._frame_ticks),
            "loop_count": self.loop_count,
            "frames_read": self.frames_read,
            "error_count": self.error_count,
            "reconnect_count": self.reconnect_count,
            "consecutive_read_failures": self.consecutive_read_failures,
            "last_error": self.last_error,
            "last_error_time": self.last_error_time,
        }

    def run(self) -> None:
        self.running = True
        self.started_at = datetime.utcnow()
        self.stopped_at = None
        logger.info("camera stream run started camera_id=%s", self.camera_id)
        db = SessionLocal()
        reader: FrameReader | None = None
        try:
            camera = self._get_camera(db)
            if not camera:
                logger.error("camera not found, stream exit camera_id=%s", self.camera_id)
                return
            reader = FrameReader(camera.rtsp_url, self.camera_id)
            reader.open()

            while self.running:
                self.loop_count += 1
                db.expire_all()
                camera = self._get_camera(db)
                runtimes = self.runtime_provider(self.camera_id)
                if not camera or not camera.enabled:
                    logger.info("camera disabled or deleted, stream exit camera_id=%s", self.camera_id)
                    break
                if not runtimes:
                    logger.info("no active tasks, stream exit camera_id=%s", self.camera_id)
                    break

                try:
                    if reader.url != camera.rtsp_url:
                        logger.info("rtsp url changed camera_id=%s, reconnect", self.camera_id)
                        self.reconnect_count += 1
                        reader.reconnect(camera.rtsp_url)

                    ok, frame = reader.read() if reader else (False, None)
                    if not ok or frame is None:
                        self.consecutive_read_failures += 1
                        self.rtsp_connected = False
                        self._note_error("rtsp read failed")
                        self._mark_all_offline(db, camera, runtimes, "rtsp read failed")
                        time.sleep(2)
                        self.reconnect_count += 1
                        reader.reconnect(camera.rtsp_url)
                        continue

                    self.frames_read += 1
                    self.last_frame_time = datetime.utcnow()
                    self._frame_ticks.append(time.time())
                    self.rtsp_connected = True
                    self.consecutive_read_failures = 0
                    status_service.update_stream_status(db, camera.id, "ONLINE", last_frame_time=self.last_frame_time)
                    video_stream_service.frame_cache.publish_raw(camera.id, frame)

                    for runtime in runtimes:
                        task, rule = self._get_task_and_rule(db, runtime.task_id)
                        if not task or not rule:
                            runtime.stop()
                            continue
                        if not runtime.should_process(task):
                            continue
                        try:
                            runtime.process_frame(db, camera, task, rule, frame)
                        except Exception as exc:
                            logger.exception("task process error task_id=%s camera_id=%s", runtime.task_id, camera.id)
                            runtime._note_error("task process error", exc=exc)

                    time.sleep(self._active_sleep_interval(runtimes))
                except Exception as exc:
                    logger.exception("stream loop error camera_id=%s", self.camera_id)
                    self._note_error("stream loop error", exc=exc)
                    time.sleep(2)

        finally:
            self.running = False
            self.stopped_at = datetime.utcnow()
            self.rtsp_connected = False
            try:
                status_service.update_stream_status(db, self.camera_id, "OFFLINE", last_error=self.last_error or "stream stopped")
            except Exception:
                logger.exception("stream status final update failed camera_id=%s", self.camera_id)
            if reader:
                reader.close()
            db.close()
            logger.info("camera stream run stopped camera_id=%s", self.camera_id)
