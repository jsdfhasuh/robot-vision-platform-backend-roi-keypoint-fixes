from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.database import get_db
from app.schemas.camera import CameraCreate, CameraUpdate
from app.services import camera_service
from app.services import frontend_adapter_service as fas
from app.services.worker_service import stop_camera

router = APIRouter(prefix="/api/cameras", tags=["cameras"])


@router.get("")
def list_cameras(db: Session = Depends(get_db)):
    cameras = camera_service.list_cameras(db)
    return ok([fas.camera_card(db, c) for c in cameras])


@router.post("")
def create_camera(payload: CameraCreate, db: Session = Depends(get_db)):
    camera = camera_service.create_camera(db, payload)
    return ok(fas.camera_card(db, camera), "camera created")


@router.get("/{camera_id}")
def get_camera(camera_id: int, db: Session = Depends(get_db)):
    camera = camera_service.get_camera(db, camera_id)
    data = fas.camera_card(db, camera)
    data.update({
        "roi": camera.roi,
        "detector_config": camera.detector_config,
        "created_at": camera.created_at,
        "updated_at": camera.updated_at,
    })
    return ok(data)


@router.put("/{camera_id}")
def update_camera(camera_id: int, payload: CameraUpdate, db: Session = Depends(get_db)):
    camera = camera_service.update_camera(db, camera_id, payload)
    return ok(fas.camera_card(db, camera), "camera updated")


@router.delete("/{camera_id}")
def delete_camera(camera_id: int, db: Session = Depends(get_db)):
    stop_camera(camera_id)
    deleted = camera_service.delete_camera(db, camera_id)
    return ok({"id": deleted.id}, "camera deleted")


@router.post("/{camera_id}/test")
def test_camera(camera_id: int, db: Session = Depends(get_db)):
    camera = camera_service.get_camera(db, camera_id)
    return ok({"connected": camera_service.test_rtsp(camera)}, "rtsp tested")
