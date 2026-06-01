from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.camera import Camera
from app.workers.camera_manager import camera_manager

logger = get_logger(__name__)


def ensure_camera_exists(db: Session, camera_id: int) -> Camera:
    camera = db.query(Camera).filter(Camera.id == camera_id).first()
    if not camera:
        raise HTTPException(404, "camera not found")
    return camera


def start_camera(db: Session, camera_id: int) -> dict:
    ensure_camera_exists(db, camera_id)
    success, message = camera_manager.start(camera_id)
    logger.info("start camera requested id=%s success=%s message=%s", camera_id, success, message)
    return {"success": success, "message": message, "camera_id": camera_id}


def stop_camera(camera_id: int) -> dict:
    success, message = camera_manager.stop(camera_id)
    logger.info("stop camera requested id=%s success=%s message=%s", camera_id, success, message)
    return {"success": success, "message": message, "camera_id": camera_id}


def list_workers() -> list[dict]:
    return camera_manager.list_workers()


def get_last_result(camera_id: int) -> dict:
    state = camera_manager.get_debug_state(camera_id)
    if state is None:
        return {"running": False, "message": "worker not running", "camera_id": camera_id}
    return state
