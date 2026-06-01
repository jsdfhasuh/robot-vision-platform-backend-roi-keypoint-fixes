from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.database import get_db
from app.services import debug_service

router = APIRouter(prefix="/api/cameras", tags=["debug"])


@router.post("/{camera_id}/snapshot")
def snapshot_camera(camera_id: int, db: Session = Depends(get_db)):
    return ok(debug_service.snapshot_camera(db, camera_id), "snapshot captured")


@router.post("/{camera_id}/debug-detect")
def debug_detect_camera(camera_id: int, db: Session = Depends(get_db)):
    return ok(debug_service.debug_detect_camera(db, camera_id), "debug detect completed")


@router.post("/{camera_id}/image-detect")
async def image_detect_camera(
    camera_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    return ok(await debug_service.image_detect_camera(db, camera_id, file), "image detect completed")


@router.post("/{camera_id}/image-pair-detect")
async def image_pair_detect_camera(
    camera_id: int,
    before: UploadFile = File(...),
    after: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    return ok(await debug_service.image_pair_detect_camera(db, camera_id, before, after), "image pair detect completed")
