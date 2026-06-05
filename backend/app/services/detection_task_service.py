from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.detectors.factory import DETECTOR_REGISTRY
from app.models.camera import Camera
from app.models.detection_task import CameraStreamStatus, DetectionRule, DetectionTask, TaskStatus
from app.services import frontend_adapter_service as fas
from app.services import shared_rule_service, status_service
from app.workers.camera_manager import camera_manager

logger = get_logger(__name__)

MOVEMENT_SCORE_OPTIONS = {
    "total_displacement",
    "avg_speed",
    "max_step",
    "net_displacement",
    "keypoint_mean_step",
    "keypoint_max_step",
    "angle_change",
    "raw",
}

DEFAULT_TRACKER_CONFIG = {
    "movement_score": "total_displacement",
    "window_seconds": 30,
    "min_step_px": 1.5,
}

DEFAULT_MOTION_CONFIG = {
    "diff_threshold": 25,
    "min_area": 80,
    "blur_size": 5,
}

MODEL_DETECTORS = {"yolo", "yolo_pose"}


def _camera(db: Session, camera_ref: str | int) -> Camera:
    if isinstance(camera_ref, int):
        camera_id = camera_ref
    else:
        camera_id = fas.parse_camera_ref(camera_ref)
    camera = db.query(Camera).filter(Camera.id == camera_id).first()
    if not camera:
        raise HTTPException(404, "camera not found")
    return camera


def _task(db: Session, task_id: int) -> DetectionTask:
    task = db.query(DetectionTask).filter(DetectionTask.id == task_id).first()
    if not task:
        raise HTTPException(404, "detection task not found")
    return task


def _rule(db: Session, rule_id: int | None, detector_type: str) -> DetectionRule:
    if rule_id is None:
        return shared_rule_service.default_rule_for_detector(db, detector_type)
    rule = shared_rule_service.get_rule(db, int(rule_id))
    if not shared_rule_service.rule_supports(rule, detector_type):
        raise HTTPException(422, f"rule {rule.id} does not support detector_type={detector_type}")
    return rule


def _normalize_detector_type(value: str | None) -> str:
    detector = (value or "motion").lower().strip()
    if detector not in DETECTOR_REGISTRY:
        raise HTTPException(422, f"unsupported detector_type: {detector}")
    return detector


def _validate_tracker(config: dict[str, Any] | None) -> dict[str, Any]:
    data = dict(DEFAULT_TRACKER_CONFIG)
    data.update(config or {})
    out = {
        "movement_score": str(data["movement_score"]),
        "window_seconds": int(data["window_seconds"]),
        "min_step_px": float(data["min_step_px"]),
    }
    if out["movement_score"] not in MOVEMENT_SCORE_OPTIONS:
        raise HTTPException(422, f"unsupported movement_score: {out['movement_score']}")
    if out["window_seconds"] < 1:
        raise HTTPException(422, "window_seconds must be >= 1")
    if out["min_step_px"] < 0:
        raise HTTPException(422, "min_step_px must be >= 0")
    return out


def _resolve_model_path(path: str | None) -> Path | None:
    if not path:
        return None
    candidate = Path(path)
    if candidate.exists():
        return candidate
    if not candidate.is_absolute():
        local = Path(settings.model_dir) / candidate
        if local.exists():
            return local
    return candidate


def _validate_detector_config(detector_type: str, config: dict[str, Any] | None) -> dict[str, Any]:
    cfg = dict(config or {})
    if detector_type == "motion":
        base = dict(DEFAULT_MOTION_CONFIG)
        base.update(cfg)
        return base
    if detector_type in MODEL_DETECTORS:
        model_path = str(cfg.get("model_path") or "").strip()
        if not model_path:
            raise HTTPException(422, f"{detector_type} task requires detector_config.model_path")
        resolved = _resolve_model_path(model_path)
        if resolved is None or not resolved.exists():
            raise HTTPException(422, f"model file not found: {model_path}")
        cfg["model_path"] = str(resolved)
    return cfg


def _normalize_roi(value: Any) -> list | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise HTTPException(422, "roi must be a list")
    if len(value) == 0:
        return None
    if len(value) != 4:
        raise HTTPException(422, "roi must be [x1, y1, x2, y2]")
    return [int(v) for v in value]


def ensure_status_rows(db: Session, task: DetectionTask) -> None:
    status_service.ensure_stream_status(db, task.camera_id)
    status_service.ensure_task_status(db, task.id, task.camera_id)
    db.commit()


def task_to_dict(db: Session, task: DetectionTask, *, include_status: bool = True) -> dict[str, Any]:
    camera = db.query(Camera).filter(Camera.id == task.camera_id).first()
    rule = db.query(DetectionRule).filter(DetectionRule.id == task.rule_id).first() if task.rule_id else None
    status = db.query(TaskStatus).filter(TaskStatus.task_id == task.id).first() if include_status else None
    stream = db.query(CameraStreamStatus).filter(CameraStreamStatus.camera_id == task.camera_id).first() if include_status else None
    running = camera_manager.is_task_running(task.id)
    return {
        "id": task.id,
        "name": task.name,
        "camera_id": task.camera_id,
        "camera_ref": fas.camera_code(task.camera_id),
        "camera_name": camera.name if camera else "",
        "detector_type": task.detector_type,
        "roi": task.roi,
        "detector_config": task.detector_config,
        "tracker_config": task.tracker_config,
        "rule_id": task.rule_id,
        "rule_name": rule.name if rule else "",
        "rule_version": rule.version if rule else None,
        "enabled": bool(task.enabled),
        "fps_limit": task.fps_limit,
        "config_version": task.config_version,
        "is_default": bool(task.is_default),
        "running": running,
        "status": status_service.task_status_to_dict(status) if status else None,
        "stream": status_service.stream_status_to_dict(stream) if stream else None,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


def list_tasks(
    db: Session,
    *,
    camera_id: int | None = None,
    enabled: bool | None = None,
    detector_type: str | None = None,
) -> list[dict[str, Any]]:
    q = db.query(DetectionTask)
    if camera_id is not None:
        q = q.filter(DetectionTask.camera_id == camera_id)
    if enabled is not None:
        q = q.filter(DetectionTask.enabled == enabled)
    if detector_type:
        q = q.filter(DetectionTask.detector_type == detector_type.lower().strip())
    rows = q.order_by(DetectionTask.id.desc()).all()
    return [task_to_dict(db, row) for row in rows]


def get_task(db: Session, task_id: int) -> DetectionTask:
    return _task(db, task_id)


def create_task(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    camera_ref = payload.get("camera_id") or payload.get("camera_ref")
    if camera_ref is None:
        raise HTTPException(422, "camera_id is required")
    camera = _camera(db, camera_ref)
    detector_type = _normalize_detector_type(payload.get("detector_type"))
    rule = _rule(db, payload.get("rule_id"), detector_type)
    detector_config = _validate_detector_config(detector_type, payload.get("detector_config"))
    tracker_config = _validate_tracker(payload.get("tracker_config"))
    name = str(payload.get("name") or f"{camera.name} {detector_type} task").strip()
    if not name:
        raise HTTPException(422, "name is required")
    fps_limit = int(payload.get("fps_limit") or camera.fps_limit or 3)
    if fps_limit < 1 or fps_limit > 30:
        raise HTTPException(422, "fps_limit must be between 1 and 30")
    task = DetectionTask(
        camera_id=camera.id,
        name=name,
        detector_type=detector_type,
        roi=_normalize_roi(payload.get("roi")),
        detector_config=detector_config,
        tracker_config=tracker_config,
        rule_id=rule.id,
        enabled=bool(payload.get("enabled", True)),
        fps_limit=fps_limit,
        config_version=1,
        is_default=bool(payload.get("is_default", False)),
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    ensure_status_rows(db, task)
    logger.info("detection task created id=%s camera_id=%s detector=%s rule_id=%s", task.id, task.camera_id, task.detector_type, task.rule_id)
    return task_to_dict(db, task)


def update_task(db: Session, task_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    task = _task(db, task_id)
    changed = False
    detector_type = task.detector_type
    if "detector_type" in payload and payload["detector_type"] is not None:
        detector_type = _normalize_detector_type(payload["detector_type"])
        if task.detector_type != detector_type:
            task.detector_type = detector_type
            changed = True
    if "camera_id" in payload and payload["camera_id"] is not None:
        camera = _camera(db, payload["camera_id"])
        if task.camera_id != camera.id:
            task.camera_id = camera.id
            changed = True
    if "name" in payload and payload["name"] is not None:
        name = str(payload["name"]).strip()
        if not name:
            raise HTTPException(422, "name is required")
        if task.name != name:
            task.name = name
            changed = True
    if "roi" in payload:
        roi = _normalize_roi(payload.get("roi"))
        if task.roi != roi:
            task.roi = roi
            changed = True
    if "detector_config" in payload:
        detector_config = _validate_detector_config(detector_type, payload.get("detector_config"))
        if task.detector_config != detector_config:
            task.detector_config = detector_config
            changed = True
    if "tracker_config" in payload:
        tracker_config = _validate_tracker(payload.get("tracker_config"))
        if task.tracker_config != tracker_config:
            task.tracker_config = tracker_config
            changed = True
    if "rule_id" in payload:
        rule = _rule(db, payload.get("rule_id"), detector_type)
        if task.rule_id != rule.id:
            task.rule_id = rule.id
            changed = True
    else:
        _rule(db, task.rule_id, detector_type)
    if "enabled" in payload and payload["enabled"] is not None and task.enabled != bool(payload["enabled"]):
        task.enabled = bool(payload["enabled"])
        changed = True
    if "fps_limit" in payload and payload["fps_limit"] is not None:
        fps_limit = int(payload["fps_limit"])
        if fps_limit < 1 or fps_limit > 30:
            raise HTTPException(422, "fps_limit must be between 1 and 30")
        if task.fps_limit != fps_limit:
            task.fps_limit = fps_limit
            changed = True
    if "is_default" in payload and payload["is_default"] is not None and task.is_default != bool(payload["is_default"]):
        task.is_default = bool(payload["is_default"])
        changed = True
    if changed:
        task.config_version = int(task.config_version or 1) + 1
    db.commit()
    db.refresh(task)
    ensure_status_rows(db, task)
    logger.info("detection task updated id=%s changed=%s config_version=%s", task.id, changed, task.config_version)
    return task_to_dict(db, task)


def delete_task(db: Session, task_id: int) -> dict[str, Any]:
    task = _task(db, task_id)
    camera_manager.stop_task(task.id)
    data = task_to_dict(db, task)
    status = db.query(TaskStatus).filter(TaskStatus.task_id == task.id).first()
    if status:
        db.delete(status)
    db.delete(task)
    db.commit()
    logger.info("detection task deleted id=%s", task_id)
    return data


def delete_tasks_for_camera(db: Session, camera_id: int) -> None:
    camera_manager.stop_camera(camera_id)
    for status in db.query(TaskStatus).filter(TaskStatus.camera_id == camera_id).all():
        db.delete(status)
    for task in db.query(DetectionTask).filter(DetectionTask.camera_id == camera_id).all():
        db.delete(task)
    stream = db.query(CameraStreamStatus).filter(CameraStreamStatus.camera_id == camera_id).first()
    if stream:
        db.delete(stream)
    db.commit()


def validate_start(db: Session, task: DetectionTask) -> tuple[Camera, DetectionRule]:
    camera = _camera(db, task.camera_id)
    if not camera.enabled:
        raise HTTPException(409, "camera is disabled")
    if not task.enabled:
        raise HTTPException(409, "detection task is disabled")
    detector_type = _normalize_detector_type(task.detector_type)
    rule = _rule(db, task.rule_id, detector_type)
    task.detector_config = _validate_detector_config(detector_type, task.detector_config)
    task.tracker_config = _validate_tracker(task.tracker_config)
    db.commit()
    return camera, rule


def start_task(db: Session, task_id: int) -> dict[str, Any]:
    task = _task(db, task_id)
    validate_start(db, task)
    success, message = camera_manager.start_task(task.id, task.camera_id)
    if success:
        status_service.update_task_status(
            db,
            task.id,
            task.camera_id,
            "UNKNOWN",
            message="task starting",
            previous_status=(status_service.get_task_status(db, task.id).status if status_service.get_task_status(db, task.id) else None),
        )
        status_service.ensure_stream_status(db, task.camera_id, message="task starting")
        db.commit()
    logger.info("detection task start requested id=%s success=%s message=%s", task.id, success, message)
    return {"success": success, "message": message, "task": task_to_dict(db, task)}


def stop_task(db: Session, task_id: int) -> dict[str, Any]:
    task = _task(db, task_id)
    success, message = camera_manager.stop_task(task.id)
    previous = status_service.get_task_status(db, task.id)
    status_service.update_task_status(
        db,
        task.id,
        task.camera_id,
        "UNKNOWN",
        confidence=0.0,
        message="task stopped",
        previous_status=previous.status if previous else None,
    )
    logger.info("detection task stop requested id=%s success=%s message=%s", task.id, success, message)
    return {"success": success, "message": message, "task": task_to_dict(db, task)}


def get_last_result(db: Session, task_id: int) -> dict[str, Any]:
    task = _task(db, task_id)
    state = camera_manager.get_task_state(task.id)
    status = status_service.get_task_status(db, task.id)
    if state is None:
        state = {
            "task_id": task.id,
            "camera_id": task.camera_id,
            "running": False,
            "message": "task not running",
        }
    state["status"] = status_service.task_status_to_dict(status) if status else None
    return state


def default_task_for_camera(db: Session, camera_id: int) -> DetectionTask | None:
    return (
        db.query(DetectionTask)
        .filter(DetectionTask.camera_id == camera_id, DetectionTask.is_default == True)  # noqa: E712
        .order_by(DetectionTask.id.asc())
        .first()
    )


def ensure_default_task_for_camera(db: Session, camera: Camera) -> DetectionTask:
    rule = shared_rule_service.default_rule_for_detector(db, "motion")
    existing = default_task_for_camera(db, camera.id)
    if existing:
        ensure_status_rows(db, existing)
        return existing
    task = DetectionTask(
        camera_id=camera.id,
        name=f"{camera.name} default motion task",
        detector_type="motion",
        roi=None,
        detector_config=dict(DEFAULT_MOTION_CONFIG),
        tracker_config=dict(DEFAULT_TRACKER_CONFIG),
        rule_id=rule.id,
        enabled=bool(camera.enabled),
        fps_limit=int(camera.fps_limit or 3),
        config_version=1,
        is_default=True,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    ensure_status_rows(db, task)
    logger.info("default detection task ensured camera_id=%s task_id=%s", camera.id, task.id)
    return task


def ensure_defaults_for_all_cameras(db: Session) -> dict[str, Any]:
    shared_rule_service.ensure_default_rules(db)
    created = 0
    cameras = db.query(Camera).order_by(Camera.id.asc()).all()
    for camera in cameras:
        before = default_task_for_camera(db, camera.id)
        ensure_default_task_for_camera(db, camera)
        if before is None:
            created += 1
    logger.info("default detection tasks checked cameras=%s created=%s", len(cameras), created)
    return {"cameras": len(cameras), "created_tasks": created}
