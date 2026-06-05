from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.database import get_db
from app.services import event_service

router = APIRouter(prefix="/api/events", tags=["events"])


class EventRemarkPayload(BaseModel):
    remark: str = ""


class FalseAlarmPayload(BaseModel):
    false_alarm: bool = True
    remark: str = ""


@router.get("")
def list_events(
    camera_id: int | None = Query(None),
    task_id: int | None = Query(None),
    event_type: str | None = Query(None),
    status: str | None = Query(None),
    handled: bool | None = Query(None),
    false_alarm: bool | None = Query(None),
    start_time: datetime | None = Query(None),
    end_time: datetime | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    data = event_service.list_events(
        db,
        camera_id=camera_id,
        task_id=task_id,
        event_type=event_type,
        status=status,
        handled=handled,
        false_alarm=false_alarm,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
    )
    return ok(data)


@router.get("/summary")
def event_summary(
    camera_id: int | None = Query(None),
    days: int = Query(1, ge=1, le=365),
    db: Session = Depends(get_db),
):
    return ok(event_service.summary(db, camera_id=camera_id, days=days))


@router.get("/{event_id}")
def get_event(event_id: int, db: Session = Depends(get_db)):
    event = event_service.get_event_or_404(db, event_id)
    return ok(event_service.event_to_dict(event, db=db))




@router.get("/{event_id}/frames")
def list_event_frames(event_id: int, db: Session = Depends(get_db)):
    return ok(event_service.list_event_frames(db, event_id))

@router.put("/{event_id}/handle")
def handle_event(event_id: int, payload: EventRemarkPayload | None = None, db: Session = Depends(get_db)):
    data = event_service.set_handled(db, event_id, True, payload.remark if payload else None)
    return ok(data, "event handled")


@router.put("/{event_id}/unhandle")
def unhandle_event(event_id: int, db: Session = Depends(get_db)):
    data = event_service.set_handled(db, event_id, False)
    return ok(data, "event unhandled")


@router.put("/{event_id}/remark")
def update_event_remark(event_id: int, payload: EventRemarkPayload, db: Session = Depends(get_db)):
    data = event_service.update_remark(db, event_id, payload.remark)
    return ok(data, "event remark updated")


@router.put("/{event_id}/false-alarm")
def mark_false_alarm(event_id: int, payload: FalseAlarmPayload | None = None, db: Session = Depends(get_db)):
    flag = True if payload is None else payload.false_alarm
    data = event_service.set_false_alarm(db, event_id, flag, payload.remark if payload else None)
    return ok(data, "event false_alarm updated")


@router.put("/{event_id}/close")
def close_event_manually(event_id: int, payload: EventRemarkPayload | None = None, db: Session = Depends(get_db)):
    data = event_service.close_manually(db, event_id, payload.remark if payload else None)
    return ok(data, "event closed")
