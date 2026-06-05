from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.detection_task import CameraStreamStatus, TaskStatus
from app.models.status import CameraStatus

logger = get_logger(__name__)


def status_to_dict(row: CameraStatus) -> dict[str, Any]:
    return {
        "camera_id": row.camera_id,
        "status": row.status,
        "last_frame_time": row.last_frame_time,
        "last_motion_time": row.last_motion_time,
        "confidence": row.confidence,
        "message": row.message,
        "updated_at": row.updated_at,
    }


def task_status_to_dict(row: TaskStatus) -> dict[str, Any]:
    return {
        "task_id": row.task_id,
        "camera_id": row.camera_id,
        "status": row.status,
        "last_frame_time": row.last_frame_time,
        "last_motion_time": row.last_motion_time,
        "last_detect_time": row.last_detect_time,
        "confidence": row.confidence,
        "message": row.message,
        "reason_code": row.reason_code,
        "detail": row.detail,
        "result": row.result_json,
        "rule_version": row.rule_version,
        "updated_at": row.updated_at,
    }


def stream_status_to_dict(row: CameraStreamStatus) -> dict[str, Any]:
    return {
        "camera_id": row.camera_id,
        "stream_status": row.stream_status,
        "last_frame_time": row.last_frame_time,
        "last_error": row.last_error,
        "updated_at": row.updated_at,
    }


def list_status(db: Session) -> list[CameraStatus]:
    rows = db.query(CameraStatus).order_by(CameraStatus.camera_id.asc()).all()
    logger.debug("list status count=%s", len(rows))
    return rows


def get_status(db: Session, camera_id: int) -> CameraStatus | None:
    row = db.query(CameraStatus).filter(CameraStatus.camera_id == camera_id).first()
    logger.debug("get status camera_id=%s found=%s", camera_id, bool(row))
    return row


def list_task_status(db: Session, *, camera_id: int | None = None) -> list[TaskStatus]:
    q = db.query(TaskStatus)
    if camera_id is not None:
        q = q.filter(TaskStatus.camera_id == camera_id)
    rows = q.order_by(TaskStatus.task_id.asc()).all()
    logger.debug("list task status count=%s camera_id=%s", len(rows), camera_id)
    return rows


def get_task_status(db: Session, task_id: int) -> TaskStatus | None:
    return db.query(TaskStatus).filter(TaskStatus.task_id == task_id).first()


def get_stream_status(db: Session, camera_id: int) -> CameraStreamStatus | None:
    return db.query(CameraStreamStatus).filter(CameraStreamStatus.camera_id == camera_id).first()


def ensure_task_status(db: Session, task_id: int, camera_id: int, *, message: str = "created") -> TaskStatus:
    row = get_task_status(db, task_id)
    if row is None:
        row = TaskStatus(task_id=task_id, camera_id=camera_id, status="UNKNOWN", message=message)
        db.add(row)
        db.flush()
    return row


def ensure_stream_status(db: Session, camera_id: int, *, message: str = "created") -> CameraStreamStatus:
    row = get_stream_status(db, camera_id)
    if row is None:
        row = CameraStreamStatus(camera_id=camera_id, stream_status="OFFLINE", last_error=message)
        db.add(row)
        db.flush()
    return row


def update_task_status(
    db: Session,
    task_id: int,
    camera_id: int,
    status: str,
    *,
    last_frame_time=None,
    last_motion_time=None,
    last_detect_time=None,
    confidence: float = 0.0,
    message: str = "",
    reason_code: str = "",
    detail: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    rule_version: int | None = None,
    previous_status: str | None = None,
) -> TaskStatus:
    row = get_task_status(db, task_id)
    now = datetime.utcnow()
    if row is None:
        row = TaskStatus(task_id=task_id, camera_id=camera_id)
        db.add(row)
    row.camera_id = camera_id
    row.status = status
    if last_frame_time is not None:
        row.last_frame_time = last_frame_time
    if last_detect_time is not None:
        row.last_detect_time = last_detect_time
    row.last_motion_time = last_motion_time
    row.confidence = confidence
    row.message = message[:512]
    row.reason_code = reason_code[:128]
    row.detail = detail
    row.result_json = result
    row.rule_version = rule_version
    row.updated_at = now
    db.commit()

    if status != previous_status:
        logger.info(
            "task status changed task_id=%s camera_id=%s from=%s to=%s confidence=%.3f message=%s",
            task_id,
            camera_id,
            previous_status,
            status,
            confidence or 0.0,
            message,
        )
    else:
        logger.debug(
            "task status updated task_id=%s camera_id=%s status=%s confidence=%.3f message=%s",
            task_id,
            camera_id,
            status,
            confidence or 0.0,
            message,
        )
    refresh_camera_aggregate(db, camera_id)
    return row


def update_stream_status(
    db: Session,
    camera_id: int,
    stream_status: str,
    *,
    last_frame_time=None,
    last_error: str = "",
) -> CameraStreamStatus:
    row = get_stream_status(db, camera_id)
    now = datetime.utcnow()
    if row is None:
        row = CameraStreamStatus(camera_id=camera_id)
        db.add(row)
    row.stream_status = stream_status
    if last_frame_time is not None:
        row.last_frame_time = last_frame_time
    row.last_error = (last_error or "")[:512]
    row.updated_at = now
    db.commit()
    refresh_camera_aggregate(db, camera_id)
    return row


def refresh_camera_aggregate(db: Session, camera_id: int) -> CameraStatus:
    """Maintain the legacy camera_status row as an aggregate of stream/task state."""
    stream = get_stream_status(db, camera_id)
    task_rows = list_task_status(db, camera_id=camera_id)
    status = "UNKNOWN"
    confidence = 0.0
    message = ""
    last_motion_time = None

    if stream and stream.stream_status == "OFFLINE" and any(row.status == "OFFLINE" for row in task_rows):
        status = "OFFLINE"
        message = stream.last_error or "stream offline"
    elif task_rows:
        priority = ["STOPPED", "UNKNOWN", "RUNNING", "IDLE", "OFFLINE"]
        statuses = {row.status for row in task_rows}
        status = next((item for item in priority if item in statuses), task_rows[0].status)
        selected = next((row for row in task_rows if row.status == status), task_rows[0])
        confidence = float(selected.confidence or 0.0)
        message = selected.message or ""
        last_motion_time = selected.last_motion_time
    elif stream:
        status = "UNKNOWN" if stream.stream_status == "ONLINE" else "OFFLINE"
        message = stream.last_error or stream.stream_status.lower()

    row = db.query(CameraStatus).filter(CameraStatus.camera_id == camera_id).first()
    now = datetime.utcnow()
    if row is None:
        row = CameraStatus(camera_id=camera_id)
        db.add(row)
    row.status = status
    row.last_frame_time = stream.last_frame_time if stream else row.last_frame_time
    row.last_motion_time = last_motion_time
    row.confidence = confidence
    row.message = message[:512]
    row.updated_at = now
    db.commit()
    return row


def update_status(
    db: Session,
    camera_id: int,
    status: str,
    *,
    last_motion_time=None,
    confidence: float = 0.0,
    message: str = "",
    previous_status: str | None = None,
) -> CameraStatus:
    row = db.query(CameraStatus).filter(CameraStatus.camera_id == camera_id).first()
    now = datetime.utcnow()
    if row is None:
        row = CameraStatus(camera_id=camera_id)
        db.add(row)
    row.status = status
    row.last_frame_time = now if status != "OFFLINE" else row.last_frame_time
    row.last_motion_time = last_motion_time
    row.confidence = confidence
    row.message = message[:512]
    row.updated_at = now
    db.commit()

    if status != previous_status:
        logger.info(
            "status changed camera_id=%s from=%s to=%s confidence=%.3f message=%s",
            camera_id,
            previous_status,
            status,
            confidence or 0.0,
            message,
        )
    else:
        logger.debug(
            "status updated camera_id=%s status=%s confidence=%.3f message=%s",
            camera_id,
            status,
            confidence or 0.0,
            message,
        )
    return row
