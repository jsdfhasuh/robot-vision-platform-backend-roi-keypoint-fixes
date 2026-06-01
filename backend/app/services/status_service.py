from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
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


def list_status(db: Session) -> list[CameraStatus]:
    rows = db.query(CameraStatus).order_by(CameraStatus.camera_id.asc()).all()
    logger.debug("list status count=%s", len(rows))
    return rows


def get_status(db: Session, camera_id: int) -> CameraStatus | None:
    row = db.query(CameraStatus).filter(CameraStatus.camera_id == camera_id).first()
    logger.debug("get status camera_id=%s found=%s", camera_id, bool(row))
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
