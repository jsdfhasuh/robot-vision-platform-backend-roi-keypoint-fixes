from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.detection_task import DetectionRule, DetectionTask

logger = get_logger(__name__)

DEFAULT_RULE_CONFIG = {
    "motion_threshold": 5.0,
    "stop_seconds": 30,
    "unknown_seconds": 10,
    "confirm_frames": 2,
    "status_hold_seconds": 1.0,
}

DEFAULT_MOTION_RULE_NAME = "Default motion stop rule"
DEFAULT_GENERAL_RULE_NAME = "Default detector stop rule"
SUPPORTED_DETECTORS = {"motion", "aruco", "yolo", "yolo_pose"}


def normalize_detector_types(values: list[str] | None) -> list[str]:
    if not values:
        return ["motion"]
    normalized = []
    for value in values:
        item = str(value or "").lower().strip()
        if not item:
            continue
        if item != "*" and item not in SUPPORTED_DETECTORS:
            raise HTTPException(422, f"unsupported detector_type: {item}")
        if item not in normalized:
            normalized.append(item)
    return normalized or ["motion"]


def rule_supports(rule: DetectionRule, detector_type: str) -> bool:
    supported = normalize_detector_types(list(rule.supported_detector_types or ["motion"]))
    detector = (detector_type or "motion").lower().strip()
    return "*" in supported or detector in supported


def validate_rule_config(config: dict[str, Any] | None) -> dict[str, Any]:
    data = dict(DEFAULT_RULE_CONFIG)
    data.update(config or {})
    out = {
        "motion_threshold": float(data["motion_threshold"]),
        "stop_seconds": int(data["stop_seconds"]),
        "unknown_seconds": int(data["unknown_seconds"]),
        "confirm_frames": int(data["confirm_frames"]),
        "status_hold_seconds": float(data["status_hold_seconds"]),
    }
    if out["motion_threshold"] < 0:
        raise HTTPException(422, "motion_threshold must be >= 0")
    if out["stop_seconds"] < 1:
        raise HTTPException(422, "stop_seconds must be >= 1")
    if out["unknown_seconds"] < 0:
        raise HTTPException(422, "unknown_seconds must be >= 0")
    if out["confirm_frames"] < 1:
        raise HTTPException(422, "confirm_frames must be >= 1")
    if out["status_hold_seconds"] < 0:
        raise HTTPException(422, "status_hold_seconds must be >= 0")
    return out


def rule_to_dict(rule: DetectionRule) -> dict[str, Any]:
    return {
        "id": rule.id,
        "name": rule.name,
        "description": rule.description,
        "supported_detector_types": list(rule.supported_detector_types or []),
        "rule_config": rule.rule_config,
        "parameters": rule.rule_config,
        "version": rule.version,
        "created_at": rule.created_at,
        "updated_at": rule.updated_at,
    }


def get_rule(db: Session, rule_id: int) -> DetectionRule:
    row = db.query(DetectionRule).filter(DetectionRule.id == rule_id).first()
    if not row:
        raise HTTPException(404, "rule not found")
    return row


def list_rules(db: Session) -> list[dict[str, Any]]:
    rows = db.query(DetectionRule).order_by(DetectionRule.id.desc()).all()
    return [rule_to_dict(row) for row in rows]


def _payload_config(payload: dict[str, Any]) -> dict[str, Any]:
    return payload.get("rule_config") or payload.get("parameters") or {}


def create_rule(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(422, "name is required")
    rule = DetectionRule(
        name=name,
        description=str(payload.get("description") or ""),
        supported_detector_types=normalize_detector_types(payload.get("supported_detector_types")),
        rule_config=validate_rule_config(_payload_config(payload)),
        version=1,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    logger.info("shared rule created id=%s name=%s detectors=%s", rule.id, rule.name, rule.supported_detector_types)
    return rule_to_dict(rule)


def update_rule(db: Session, rule_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    rule = get_rule(db, rule_id)
    changed = False
    if "name" in payload:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise HTTPException(422, "name is required")
        if rule.name != name:
            rule.name = name
            changed = True
    if "description" in payload and rule.description != str(payload.get("description") or ""):
        rule.description = str(payload.get("description") or "")
        changed = True
    if "supported_detector_types" in payload:
        supported = normalize_detector_types(payload.get("supported_detector_types"))
        if rule.supported_detector_types != supported:
            rule.supported_detector_types = supported
            changed = True
    if "rule_config" in payload or "parameters" in payload:
        config = validate_rule_config(_payload_config(payload))
        if rule.rule_config != config:
            rule.rule_config = config
            changed = True
    if changed:
        rule.version = int(rule.version or 1) + 1
        rule.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(rule)
    logger.info("shared rule updated id=%s changed=%s version=%s", rule.id, changed, rule.version)
    return rule_to_dict(rule)


def delete_rule(db: Session, rule_id: int) -> dict[str, Any]:
    rule = get_rule(db, rule_id)
    in_use = db.query(DetectionTask).filter(DetectionTask.rule_id == rule.id).count()
    if in_use:
        raise HTTPException(409, f"rule is used by {in_use} detection task(s)")
    data = rule_to_dict(rule)
    db.delete(rule)
    db.commit()
    logger.info("shared rule deleted id=%s", rule_id)
    return data


def usage(db: Session, rule_id: int) -> dict[str, Any]:
    rule = get_rule(db, rule_id)
    tasks = db.query(DetectionTask).filter(DetectionTask.rule_id == rule.id).order_by(DetectionTask.id.asc()).all()
    return {
        "rule": rule_to_dict(rule),
        "count": len(tasks),
        "tasks": [
            {
                "id": task.id,
                "name": task.name,
                "camera_id": task.camera_id,
                "detector_type": task.detector_type,
                "enabled": task.enabled,
                "is_default": task.is_default,
                "config_version": task.config_version,
            }
            for task in tasks
        ],
    }


def ensure_default_rules(db: Session) -> dict[str, DetectionRule]:
    motion = db.query(DetectionRule).filter(DetectionRule.name == DEFAULT_MOTION_RULE_NAME).first()
    if motion is None:
        motion = DetectionRule(
            name=DEFAULT_MOTION_RULE_NAME,
            description="Default shared rule for motion detection tasks.",
            supported_detector_types=["motion"],
            rule_config=dict(DEFAULT_RULE_CONFIG),
            version=1,
        )
        db.add(motion)
        db.flush()

    general = db.query(DetectionRule).filter(DetectionRule.name == DEFAULT_GENERAL_RULE_NAME).first()
    if general is None:
        general = DetectionRule(
            name=DEFAULT_GENERAL_RULE_NAME,
            description="Default shared rule for model or marker detection tasks.",
            supported_detector_types=["aruco", "yolo", "yolo_pose"],
            rule_config=dict(DEFAULT_RULE_CONFIG),
            version=1,
        )
        db.add(general)
        db.flush()
    db.commit()
    return {"motion": motion, "general": general}


def default_rule_for_detector(db: Session, detector_type: str) -> DetectionRule:
    defaults = ensure_default_rules(db)
    detector = (detector_type or "motion").lower().strip()
    if detector == "motion":
        return defaults["motion"]
    if rule_supports(defaults["general"], detector):
        return defaults["general"]
    row = db.query(DetectionRule).order_by(DetectionRule.id.asc()).all()
    for rule in row:
        if rule_supports(rule, detector):
            return rule
    raise HTTPException(422, f"no shared rule supports detector_type={detector}")
