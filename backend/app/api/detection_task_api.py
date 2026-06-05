from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.database import get_db
from app.services import detection_task_service

router = APIRouter(prefix="/api/detection-tasks", tags=["detection-tasks"])


class DetectionTaskCreate(BaseModel):
    camera_id: int | str
    name: str | None = None
    detector_type: str = "motion"
    roi: list[int] | None = None
    detector_config: dict[str, Any] | None = None
    tracker_config: dict[str, Any] | None = None
    rule_id: int | None = None
    enabled: bool = True
    fps_limit: int = Field(default=3, ge=1, le=30)
    is_default: bool = False


class DetectionTaskUpdate(BaseModel):
    camera_id: int | str | None = None
    name: str | None = None
    detector_type: str | None = None
    roi: list[int] | None = None
    detector_config: dict[str, Any] | None = None
    tracker_config: dict[str, Any] | None = None
    rule_id: int | None = None
    enabled: bool | None = None
    fps_limit: int | None = Field(default=None, ge=1, le=30)
    is_default: bool | None = None


@router.get("")
def list_detection_tasks(
    camera_id: int | None = Query(None),
    enabled: bool | None = Query(None),
    detector_type: str | None = Query(None),
    db: Session = Depends(get_db),
):
    return ok(detection_task_service.list_tasks(db, camera_id=camera_id, enabled=enabled, detector_type=detector_type))


@router.post("")
def create_detection_task(payload: DetectionTaskCreate, db: Session = Depends(get_db)):
    return ok(detection_task_service.create_task(db, payload.model_dump(exclude_unset=True)), "detection task created")


@router.get("/{task_id}")
def get_detection_task(task_id: int, db: Session = Depends(get_db)):
    task = detection_task_service.get_task(db, task_id)
    return ok(detection_task_service.task_to_dict(db, task))


@router.put("/{task_id}")
def update_detection_task(task_id: int, payload: DetectionTaskUpdate, db: Session = Depends(get_db)):
    return ok(detection_task_service.update_task(db, task_id, payload.model_dump(exclude_unset=True)), "detection task updated")


@router.delete("/{task_id}")
def delete_detection_task(task_id: int, db: Session = Depends(get_db)):
    return ok(detection_task_service.delete_task(db, task_id), "detection task deleted")


@router.post("/{task_id}/start")
def start_detection_task(task_id: int, db: Session = Depends(get_db)):
    return ok(detection_task_service.start_task(db, task_id), "detection task start requested")


@router.post("/{task_id}/stop")
def stop_detection_task(task_id: int, db: Session = Depends(get_db)):
    return ok(detection_task_service.stop_task(db, task_id), "detection task stop requested")


@router.get("/{task_id}/last-result")
def last_result(task_id: int, db: Session = Depends(get_db)):
    return ok(detection_task_service.get_last_result(db, task_id))
