from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.database import get_db
from app.services import shared_rule_service

router = APIRouter(prefix="/api/rules", tags=["shared-rules"])


class SharedRuleCreate(BaseModel):
    name: str
    description: str = ""
    supported_detector_types: list[str] | None = None
    rule_config: dict[str, Any] | None = None
    parameters: dict[str, Any] | None = None


class SharedRuleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    supported_detector_types: list[str] | None = None
    rule_config: dict[str, Any] | None = None
    parameters: dict[str, Any] | None = None


@router.get("")
def list_rules(db: Session = Depends(get_db)):
    return ok(shared_rule_service.list_rules(db))


@router.post("")
def create_rule(payload: SharedRuleCreate, db: Session = Depends(get_db)):
    return ok(shared_rule_service.create_rule(db, payload.model_dump(exclude_unset=True)), "rule created")


@router.get("/{rule_id}")
def get_rule(rule_id: int, db: Session = Depends(get_db)):
    return ok(shared_rule_service.rule_to_dict(shared_rule_service.get_rule(db, rule_id)))


@router.put("/{rule_id}")
def update_rule(rule_id: int, payload: SharedRuleUpdate, db: Session = Depends(get_db)):
    return ok(shared_rule_service.update_rule(db, rule_id, payload.model_dump(exclude_unset=True)), "rule updated")


@router.delete("/{rule_id}")
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    return ok(shared_rule_service.delete_rule(db, rule_id), "rule deleted")


@router.get("/{rule_id}/usage")
def rule_usage(rule_id: int, db: Session = Depends(get_db)):
    return ok(shared_rule_service.usage(db, rule_id))
