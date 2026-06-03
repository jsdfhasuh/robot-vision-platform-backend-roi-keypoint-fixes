from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.database import get_db
from app.services import rule_service

camera_rule_router = APIRouter(prefix="/api/cameras", tags=["rules"])
template_router = APIRouter(prefix="/api/rule-templates", tags=["rule-templates"])


class RuleSettings(BaseModel):
    motion_threshold: float = Field(ge=0)
    stop_seconds: int = Field(ge=1)
    unknown_seconds: int = Field(ge=0)
    confirm_frames: int = Field(ge=1)
    status_hold_seconds: float = Field(ge=0)


class TrackerSettings(BaseModel):
    movement_score: str
    window_seconds: int = Field(ge=1)
    min_step_px: float = Field(ge=0)


class CameraRulePayload(BaseModel):
    rule: RuleSettings
    tracker: TrackerSettings


class RuleCopyPayload(BaseModel):
    target_camera_ids: list[str | int]


class RuleTemplatePayload(BaseModel):
    name: str
    description: str = ""
    detector_type: str = ""
    rule: RuleSettings
    tracker: TrackerSettings


class RuleTemplateApplyPayload(BaseModel):
    camera_ids: list[str | int]


def _payload(data: BaseModel) -> dict[str, Any]:
    return data.model_dump()


@camera_rule_router.get("/{camera_ref}/rule")
def get_camera_rule(camera_ref: str, db: Session = Depends(get_db)):
    return ok(rule_service.rule_payload(db, camera_ref))


@camera_rule_router.put("/{camera_ref}/rule")
def save_camera_rule(camera_ref: str, payload: CameraRulePayload, db: Session = Depends(get_db)):
    return ok(rule_service.save_camera_rule(db, camera_ref, _payload(payload)), "camera rule saved")


@camera_rule_router.post("/{source_ref}/rule/copy")
def copy_camera_rule(source_ref: str, payload: RuleCopyPayload, db: Session = Depends(get_db)):
    return ok(rule_service.copy_camera_rule(db, source_ref, payload.target_camera_ids), "camera rule copied")


@template_router.get("")
def list_rule_templates(db: Session = Depends(get_db)):
    return ok(rule_service.list_templates(db))


@template_router.post("")
def create_rule_template(payload: RuleTemplatePayload, db: Session = Depends(get_db)):
    return ok(rule_service.create_template(db, _payload(payload)), "rule template created")


@template_router.get("/{template_id}")
def get_rule_template(template_id: int, db: Session = Depends(get_db)):
    return ok(rule_service.template_to_dict(rule_service.get_template(db, template_id)))


@template_router.put("/{template_id}")
def update_rule_template(template_id: int, payload: RuleTemplatePayload, db: Session = Depends(get_db)):
    return ok(rule_service.update_template(db, template_id, _payload(payload)), "rule template updated")


@template_router.delete("/{template_id}")
def delete_rule_template(template_id: int, db: Session = Depends(get_db)):
    return ok(rule_service.delete_template(db, template_id), "rule template deleted")


@template_router.post("/{template_id}/apply")
def apply_rule_template(template_id: int, payload: RuleTemplateApplyPayload, db: Session = Depends(get_db)):
    return ok(rule_service.apply_template(db, template_id, payload.camera_ids), "rule template applied")
