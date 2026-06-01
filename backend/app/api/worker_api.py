from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.database import get_db
from app.services import worker_service

router = APIRouter(prefix="/api/cameras", tags=["camera-workers"])


@router.post("/{camera_id}/start")
def start_camera(camera_id: int, db: Session = Depends(get_db)):
    return ok(worker_service.start_camera(db, camera_id), "start requested")


@router.post("/{camera_id}/stop")
def stop_camera(camera_id: int):
    return ok(worker_service.stop_camera(camera_id), "stop requested")


@router.get("/{camera_id}/last-result")
def last_result_camera(camera_id: int):
    return ok(worker_service.get_last_result(camera_id))
