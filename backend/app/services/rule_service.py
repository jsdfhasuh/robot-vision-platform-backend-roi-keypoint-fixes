from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.camera import Camera
from app.models.rule_template import RuleTemplate
from app.services import frontend_adapter_service as fas
from app.services import worker_service

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


def _camera(db: Session, camera_ref: str | int) -> Camera:
    return fas.get_camera_by_ref(db, camera_ref)


def _rule_from_camera(camera: Camera) -> dict[str, Any]:
    cfg = camera.detector_config or {}
    rule_cfg = cfg.get("rule") if isinstance(cfg, dict) else {}
    rule_cfg = rule_cfg if isinstance(rule_cfg, dict) else {}
    return {
        "motion_threshold": float(camera.motion_threshold or 0.0),
        "stop_seconds": int(camera.stop_seconds or 30),
        "unknown_seconds": int(rule_cfg.get("unknown_seconds", 10)),
        "confirm_frames": int(rule_cfg.get("confirm_frames", 2)),
        "status_hold_seconds": float(rule_cfg.get("status_hold_seconds", 1.0)),
    }


def _tracker_from_camera(camera: Camera) -> dict[str, Any]:
    cfg = camera.detector_config or {}
    tracker_cfg = cfg.get("tracker") if isinstance(cfg, dict) else {}
    tracker_cfg = tracker_cfg if isinstance(tracker_cfg, dict) else {}
    return {
        "movement_score": str(tracker_cfg.get("movement_score", "total_displacement")),
        "window_seconds": int(tracker_cfg.get("window_seconds", tracker_cfg.get("motion_window_seconds", 30))),
        "min_step_px": float(tracker_cfg.get("min_step_px", 1.5)),
    }


def _validate_rule(rule: dict[str, Any]) -> dict[str, Any]:
    required = ["motion_threshold", "stop_seconds", "unknown_seconds", "confirm_frames", "status_hold_seconds"]
    missing = [key for key in required if key not in rule]
    if missing:
        raise HTTPException(422, f"missing rule fields: {', '.join(missing)}")
    data = {
        "motion_threshold": float(rule["motion_threshold"]),
        "stop_seconds": int(rule["stop_seconds"]),
        "unknown_seconds": int(rule["unknown_seconds"]),
        "confirm_frames": int(rule["confirm_frames"]),
        "status_hold_seconds": float(rule["status_hold_seconds"]),
    }
    if data["motion_threshold"] < 0:
        raise HTTPException(422, "motion_threshold must be >= 0")
    if data["stop_seconds"] < 1:
        raise HTTPException(422, "stop_seconds must be >= 1")
    if data["unknown_seconds"] < 0:
        raise HTTPException(422, "unknown_seconds must be >= 0")
    if data["confirm_frames"] < 1:
        raise HTTPException(422, "confirm_frames must be >= 1")
    if data["status_hold_seconds"] < 0:
        raise HTTPException(422, "status_hold_seconds must be >= 0")
    return data


def _validate_tracker(tracker: dict[str, Any]) -> dict[str, Any]:
    required = ["movement_score", "window_seconds", "min_step_px"]
    missing = [key for key in required if key not in tracker]
    if missing:
        raise HTTPException(422, f"missing tracker fields: {', '.join(missing)}")
    data = {
        "movement_score": str(tracker["movement_score"]),
        "window_seconds": int(tracker["window_seconds"]),
        "min_step_px": float(tracker["min_step_px"]),
    }
    if data["movement_score"] not in MOVEMENT_SCORE_OPTIONS:
        raise HTTPException(422, f"unsupported movement_score: {data['movement_score']}")
    if data["window_seconds"] < 1:
        raise HTTPException(422, "window_seconds must be >= 1")
    if data["min_step_px"] < 0:
        raise HTTPException(422, "min_step_px must be >= 0")
    return data


def validate_config(rule: dict[str, Any], tracker: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    return _validate_rule(rule), _validate_tracker(tracker)


def _current_state(camera_id: int) -> dict[str, Any]:
    state = worker_service.get_last_result(camera_id)
    if not state.get("running"):
        return {
            "status": "UNKNOWN",
            "message": state.get("message", "worker not running"),
            "motion_distance": None,
            "rule_detail": None,
            "tracker": None,
        }
    last_result = state.get("last_result") or {}
    return {
        "status": state.get("last_status") or "UNKNOWN",
        "message": last_result.get("message") or state.get("last_error") or "",
        "motion_distance": last_result.get("motion_distance"),
        "rule_detail": state.get("rule"),
        "tracker": state.get("tracker"),
    }


def rule_payload(db: Session, camera_ref: str | int) -> dict[str, Any]:
    camera = _camera(db, camera_ref)
    return {
        "camera_id": fas.camera_code(camera.id),
        "numeric_camera_id": camera.id,
        "detector_type": camera.detector_type,
        "rule": _rule_from_camera(camera),
        "tracker": _tracker_from_camera(camera),
        "current": _current_state(camera.id),
        "config_version": camera.config_version,
        "updated_at": camera.updated_at,
    }


def apply_rule_to_camera(
    db: Session,
    camera: Camera,
    *,
    rule: dict[str, Any],
    tracker: dict[str, Any],
    commit: bool = True,
) -> Camera:
    rule_data, tracker_data = validate_config(rule, tracker)
    cfg = dict(camera.detector_config or {})
    cfg["rule"] = {
        "unknown_seconds": rule_data["unknown_seconds"],
        "confirm_frames": rule_data["confirm_frames"],
        "status_hold_seconds": rule_data["status_hold_seconds"],
    }
    cfg["tracker"] = tracker_data

    changed = (
        float(camera.motion_threshold or 0.0) != rule_data["motion_threshold"]
        or int(camera.stop_seconds or 0) != rule_data["stop_seconds"]
        or camera.detector_config != cfg
    )
    camera.motion_threshold = rule_data["motion_threshold"]
    camera.stop_seconds = rule_data["stop_seconds"]
    camera.detector_config = cfg
    if changed:
        camera.config_version = (camera.config_version or 1) + 1
    if commit:
        db.commit()
        db.refresh(camera)
    logger.info("camera rule saved camera_id=%s changed=%s config_version=%s", camera.id, changed, camera.config_version)
    return camera


def save_camera_rule(db: Session, camera_ref: str | int, payload: dict[str, Any]) -> dict[str, Any]:
    camera = _camera(db, camera_ref)
    apply_rule_to_camera(db, camera, rule=payload.get("rule") or {}, tracker=payload.get("tracker") or {})
    return rule_payload(db, camera.id)


def copy_camera_rule(db: Session, source_ref: str | int, target_camera_ids: list[str | int]) -> dict[str, Any]:
    source = _camera(db, source_ref)
    if not target_camera_ids:
        raise HTTPException(422, "target_camera_ids is required")
    source_rule = _rule_from_camera(source)
    source_tracker = _tracker_from_camera(source)
    targets: list[dict[str, Any]] = []
    for target_ref in target_camera_ids:
        target = _camera(db, target_ref)
        apply_rule_to_camera(db, target, rule=source_rule, tracker=source_tracker, commit=False)
        targets.append({
            "camera_id": fas.camera_code(target.id),
            "numeric_camera_id": target.id,
            "config_version": target.config_version,
        })
    db.commit()
    logger.info("camera rule copied source=%s targets=%s", source.id, [x["numeric_camera_id"] for x in targets])
    return {
        "source_camera_id": fas.camera_code(source.id),
        "source_numeric_camera_id": source.id,
        "rule": source_rule,
        "tracker": source_tracker,
        "targets": targets,
    }


def template_to_dict(template: RuleTemplate) -> dict[str, Any]:
    return {
        "id": template.id,
        "name": template.name,
        "description": template.description,
        "detector_type": template.detector_type,
        "rule": template.rule_json,
        "tracker": template.tracker_json,
        "created_at": template.created_at,
        "updated_at": template.updated_at,
    }


def list_templates(db: Session) -> list[dict[str, Any]]:
    rows = db.query(RuleTemplate).order_by(RuleTemplate.id.desc()).all()
    return [template_to_dict(row) for row in rows]


def get_template(db: Session, template_id: int) -> RuleTemplate:
    row = db.query(RuleTemplate).filter(RuleTemplate.id == template_id).first()
    if not row:
        raise HTTPException(404, "rule template not found")
    return row


def create_template(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    rule, tracker = validate_config(payload.get("rule") or {}, payload.get("tracker") or {})
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(422, "name is required")
    row = RuleTemplate(
        name=name,
        description=str(payload.get("description") or ""),
        detector_type=str(payload.get("detector_type") or ""),
        rule_json=rule,
        tracker_json=tracker,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    logger.info("rule template created id=%s name=%s", row.id, row.name)
    return template_to_dict(row)


def update_template(db: Session, template_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    row = get_template(db, template_id)
    rule, tracker = validate_config(payload.get("rule") or {}, payload.get("tracker") or {})
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(422, "name is required")
    row.name = name
    row.description = str(payload.get("description") or "")
    row.detector_type = str(payload.get("detector_type") or "")
    row.rule_json = rule
    row.tracker_json = tracker
    db.commit()
    db.refresh(row)
    logger.info("rule template updated id=%s name=%s", row.id, row.name)
    return template_to_dict(row)


def delete_template(db: Session, template_id: int) -> dict[str, Any]:
    row = get_template(db, template_id)
    data = template_to_dict(row)
    db.delete(row)
    db.commit()
    logger.info("rule template deleted id=%s", template_id)
    return data


def apply_template(db: Session, template_id: int, camera_ids: list[str | int]) -> dict[str, Any]:
    template = get_template(db, template_id)
    if not camera_ids:
        raise HTTPException(422, "camera_ids is required")
    applied: list[dict[str, Any]] = []
    for camera_ref in camera_ids:
        camera = _camera(db, camera_ref)
        apply_rule_to_camera(db, camera, rule=template.rule_json, tracker=template.tracker_json, commit=False)
        applied.append({
            "camera_id": fas.camera_code(camera.id),
            "numeric_camera_id": camera.id,
            "config_version": camera.config_version,
        })
    db.commit()
    logger.info("rule template applied template_id=%s cameras=%s", template_id, [x["numeric_camera_id"] for x in applied])
    return {"template": template_to_dict(template), "applied": applied}
