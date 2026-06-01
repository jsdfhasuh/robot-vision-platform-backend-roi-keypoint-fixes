from __future__ import annotations

import cv2
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.camera import Camera
from app.models.status import CameraStatus
from app.schemas.camera import CameraCreate, CameraUpdate

logger = get_logger(__name__)

FRONTEND_META_KEYS = {"area", "line", "robot_id", "robot_name"}


def _split_frontend_fields(data: dict) -> tuple[dict, dict]:
    meta = {k: data.pop(k) for k in list(data.keys()) if k in FRONTEND_META_KEYS and data.get(k) is not None}
    if (not data.get("location")) and (meta.get("area") or meta.get("line")):
        data["location"] = "/".join([x for x in [meta.get("area"), meta.get("line")] if x])
    return data, meta


def _merge_frontend_meta(config: dict | None, meta: dict) -> dict | None:
    if not meta:
        return config
    cfg = dict(config or {})
    current = dict(cfg.get("frontend_meta") or {})
    current.update({k: v for k, v in meta.items() if v is not None})
    cfg["frontend_meta"] = current
    return cfg


def list_cameras(db: Session) -> list[Camera]:
    rows = db.query(Camera).order_by(Camera.id.desc()).all()
    logger.debug("list cameras count=%s", len(rows))
    return rows


def get_camera(db: Session, camera_id: int) -> Camera:
    camera = db.query(Camera).filter(Camera.id == camera_id).first()
    if not camera:
        raise HTTPException(404, "camera not found")
    return camera


def create_camera(db: Session, payload: CameraCreate) -> Camera:
    data, meta = _split_frontend_fields(payload.model_dump())
    data["detector_config"] = _merge_frontend_meta(data.get("detector_config"), meta)
    camera = Camera(**data)
    db.add(camera)
    db.commit()
    db.refresh(camera)

    status = db.query(CameraStatus).filter(CameraStatus.camera_id == camera.id).first()
    if status is None:
        db.add(CameraStatus(camera_id=camera.id, status="UNKNOWN", message="created"))
        db.commit()

    logger.info(
        "camera created id=%s name=%s detector=%s enabled=%s",
        camera.id,
        camera.name,
        camera.detector_type,
        camera.enabled,
    )
    return camera


def update_camera(db: Session, camera_id: int, payload: CameraUpdate) -> Camera:
    camera = get_camera(db, camera_id)
    changed = False
    data, meta = _split_frontend_fields(payload.model_dump(exclude_unset=True))
    if meta:
        new_cfg = _merge_frontend_meta(camera.detector_config, meta)
        if camera.detector_config != new_cfg:
            camera.detector_config = new_cfg
            changed = True
    for key, value in data.items():
        if getattr(camera, key) != value:
            setattr(camera, key, value)
            changed = True
    if changed:
        camera.config_version = (camera.config_version or 1) + 1
    db.commit()
    db.refresh(camera)
    logger.info(
        "camera updated id=%s name=%s detector=%s enabled=%s config_version=%s",
        camera.id,
        camera.name,
        camera.detector_type,
        camera.enabled,
        camera.config_version,
    )
    return camera


def delete_camera(db: Session, camera_id: int) -> Camera:
    camera = get_camera(db, camera_id)
    logger.info("camera deleted id=%s name=%s", camera.id, camera.name)
    db.delete(camera)
    db.commit()
    return camera


def test_rtsp(camera: Camera) -> bool:
    logger.info("testing rtsp connection camera_id=%s name=%s", camera.id, camera.name)
    cap = cv2.VideoCapture(camera.rtsp_url)
    ok, frame = cap.read()
    cap.release()
    result = bool(ok and frame is not None)
    logger.info("rtsp test result camera_id=%s ok=%s", camera.id, result)
    return result
