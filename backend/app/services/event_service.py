from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.detectors.base import DetectResult
from app.models.camera import Camera
from app.models.detection_task import DetectionTask
from app.models.event import Event
from app.models.event_frame import EventFrame
from app.services import snapshot_service

logger = get_logger(__name__)


def duration_seconds(event: Event, now: datetime | None = None) -> float | None:
    if not event.start_time:
        return None
    now = now or datetime.utcnow()
    return round(((event.end_time or now) - event.start_time).total_seconds(), 3)




def frame_to_dict(frame: EventFrame) -> dict[str, Any]:
    return {
        "id": frame.id,
        "event_id": frame.event_id,
        "camera_id": frame.camera_id,
        "task_id": frame.task_id,
        "frame_time": frame.frame_time,
        "frame_type": frame.frame_type,
        "status": frame.status,
        "image_path": frame.image_path,
        "image_url": snapshot_service.storage_url(frame.image_path),
        "annotated_image_path": frame.annotated_image_path,
        "annotated_image_url": snapshot_service.storage_url(frame.annotated_image_path),
        "detector_type": frame.detector_type,
        "reason": frame.reason,
        "rule_detail": frame.rule_detail,
    }


def add_event_frame(
    db: Session,
    *,
    event: Event,
    frame_type: str,
    status: str,
    task: DetectionTask | None = None,
    image_path: str | None = None,
    annotated_image_path: str | None = None,
    detector_type: str = "",
    reason: str = "",
    rule_detail: dict[str, Any] | None = None,
) -> EventFrame:
    row = EventFrame(
        event_id=event.id,
        camera_id=event.camera_id,
        task_id=task.id if task else event.task_id,
        frame_type=frame_type,
        status=status,
        image_path=image_path,
        annotated_image_path=annotated_image_path,
        detector_type=detector_type,
        reason=(reason or "")[:1024],
        rule_detail=rule_detail,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    logger.info("event frame added event_id=%s frame_id=%s type=%s status=%s", event.id, row.id, frame_type, status)
    return row


def list_event_frames(db: Session, event_id: int) -> list[dict[str, Any]]:
    get_event_or_404(db, event_id)
    rows = db.query(EventFrame).filter(EventFrame.event_id == event_id).order_by(EventFrame.id.asc()).all()
    return [frame_to_dict(row) for row in rows]

def event_to_dict(event: Event, now: datetime | None = None, db: Session | None = None) -> dict[str, Any]:
    camera_name = ""
    task_name = ""
    if db is not None:
        camera = db.query(Camera).filter(Camera.id == event.camera_id).first()
        task = db.query(DetectionTask).filter(DetectionTask.id == event.task_id).first() if event.task_id else None
        camera_name = camera.name if camera else ""
        task_name = task.name if task else ""
    return {
        "id": event.id,
        "camera_id": event.camera_id,
        "camera_name": camera_name,
        "task_id": event.task_id,
        "task_name": task_name,
        "event_type": event.event_type,
        "status": event.status,
        "start_time": event.start_time,
        "end_time": event.end_time,
        "duration_seconds": duration_seconds(event, now),
        "snapshot_path": event.snapshot_path,
        "snapshot_url": snapshot_service.storage_url(event.snapshot_path),
        "annotated_snapshot_path": event.annotated_snapshot_path,
        "annotated_snapshot_url": snapshot_service.storage_url(event.annotated_snapshot_path),
        "recovery_snapshot_path": event.recovery_snapshot_path,
        "recovery_snapshot_url": snapshot_service.storage_url(event.recovery_snapshot_path),
        "recovery_annotated_path": event.recovery_annotated_path,
        "recovery_annotated_url": snapshot_service.storage_url(event.recovery_annotated_path),
        "clip_path": event.clip_path,
        "clip_url": snapshot_service.storage_url(event.clip_path),
        "reason": event.reason,
        "rule_detail": event.rule_detail,
        "detector_type": event.detector_type,
        "handled": event.handled,
        "false_alarm": event.false_alarm,
        "remark": event.remark,
    }


def event_types_for_camera(camera: Camera | None = None, task: DetectionTask | None = None) -> set[str]:
    default = {"STOPPED", "OFFLINE"}
    if task and isinstance(task.detector_config, dict):
        configured = task.detector_config.get("event_types")
        if isinstance(configured, list):
            return {str(x).upper() for x in configured if str(x).strip()}
        if task.detector_config.get("enable_unknown_event") is True:
            return default | {"UNKNOWN"}
    if not camera or not isinstance(camera.detector_config, dict):
        return default
    configured = camera.detector_config.get("event_types")
    if isinstance(configured, list):
        return {str(x).upper() for x in configured if str(x).strip()}
    if camera.detector_config.get("enable_unknown_event") is True:
        return default | {"UNKNOWN"}
    return default


def load_open_events(db: Session, camera_id: int, event_types: set[str], task_id: int | None = None) -> dict[str, int]:
    rows = (
        db.query(Event)
        .filter(Event.camera_id == camera_id, Event.status == "OPEN", Event.event_type.in_(list(event_types)))
    )
    if task_id is None:
        rows = rows.filter(Event.task_id.is_(None))
    else:
        rows = rows.filter(Event.task_id == task_id)
    rows = rows.order_by(Event.id.desc()).all()
    events: dict[str, int] = {}
    for event in rows:
        events.setdefault(event.event_type, event.id)
    if events:
        logger.info("loaded open events camera_id=%s task_id=%s events=%s", camera_id, task_id, events)
    return events


def open_event(
    db: Session,
    *,
    camera: Camera,
    task: DetectionTask | None = None,
    event_type: str,
    open_event_ids: dict[str, int],
    full_frame=None,
    roi_frame=None,
    result: DetectResult | None = None,
    message: str = "",
    rule_detail: dict[str, Any] | None = None,
) -> Event | None:
    event_type = event_type.upper()
    if event_type in open_event_ids:
        return None

    existing = (
        db.query(Event)
        .filter(Event.camera_id == camera.id, Event.event_type == event_type, Event.status == "OPEN")
    )
    if task is None:
        existing = existing.filter(Event.task_id.is_(None))
    else:
        existing = existing.filter(Event.task_id == task.id)
    existing = existing.order_by(Event.id.desc()).first()
    if existing:
        open_event_ids[event_type] = existing.id
        return existing

    snapshot = snapshot_service.save_image(full_frame, camera_id=camera.id, suffix=f"{event_type.lower()}_open_raw") if full_frame is not None else None
    annotated = None
    camera_view = camera
    if task is not None:
        from types import SimpleNamespace

        camera_view = SimpleNamespace(id=camera.id, name=camera.name, detector_type=task.detector_type, roi=task.roi)
    if roi_frame is not None and result is not None:
        annotated = snapshot_service.save_annotated_image(
            roi_frame,
            result,
            event_type,
            camera=camera_view,
            rule_detail=rule_detail,
            event_type=event_type,
            suffix=f"{event_type.lower()}_open_annotated",
        )
    event = Event(
        camera_id=camera.id,
        task_id=task.id if task else None,
        event_type=event_type,
        status="OPEN",
        snapshot_path=snapshot,
        annotated_snapshot_path=annotated,
        reason=(rule_detail or {}).get("reason") or message[:1024],
        rule_detail=rule_detail,
        detector_type=task.detector_type if task else camera.detector_type,
        remark=message[:512],
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    add_event_frame(
        db,
        event=event,
        frame_type="open",
        status=event_type,
        task=task,
        image_path=snapshot,
        annotated_image_path=annotated,
        detector_type=task.detector_type if task else camera.detector_type,
        reason=event.reason,
        rule_detail=rule_detail,
    )
    open_event_ids[event_type] = event.id
    logger.warning(
        "event opened camera_id=%s task_id=%s event_type=%s event_id=%s snapshot=%s annotated=%s reason=%s",
        camera.id,
        task.id if task else None,
        event_type,
        event.id,
        snapshot,
        annotated,
        event.reason,
    )
    return event


def recover_event(
    db: Session,
    *,
    camera: Camera,
    task: DetectionTask | None = None,
    event_type: str,
    open_event_ids: dict[str, int],
    full_frame=None,
    roi_frame=None,
    result: DetectResult | None = None,
    message: str = "",
    rule_detail: dict[str, Any] | None = None,
) -> Event | None:
    event_type = event_type.upper()
    event_id = open_event_ids.get(event_type)
    if not event_id:
        return None

    event = db.query(Event).filter(Event.id == event_id).first()
    if event and event.status == "OPEN":
        camera_view = camera
        if task is not None:
            from types import SimpleNamespace

            camera_view = SimpleNamespace(id=camera.id, name=camera.name, detector_type=task.detector_type, roi=task.roi)
        event.end_time = datetime.utcnow()
        event.status = "RECOVERED"
        event.recovery_snapshot_path = snapshot_service.save_image(
            full_frame,
            camera_id=camera.id,
            suffix=f"{event_type.lower()}_recover_raw",
        ) if full_frame is not None else None
        if roi_frame is not None and result is not None:
            event.recovery_annotated_path = snapshot_service.save_annotated_image(
                roi_frame,
                result,
                "RECOVERED",
                camera=camera_view,
                rule_detail=rule_detail,
                event_type=event_type,
                suffix=f"{event_type.lower()}_recover_annotated",
            )
        event.reason = (rule_detail or {}).get("reason") or event.reason
        event.rule_detail = rule_detail or event.rule_detail
        add_event_frame(
            db,
            event=event,
            frame_type="recover",
            status="RECOVERED",
            task=task,
            image_path=event.recovery_snapshot_path,
            annotated_image_path=event.recovery_annotated_path,
            detector_type=task.detector_type if task else camera.detector_type,
            reason=event.reason,
            rule_detail=rule_detail,
        )
        db.commit()
        logger.info(
            "event recovered camera_id=%s task_id=%s event_type=%s event_id=%s recovery_snapshot=%s recovery_annotated=%s",
            camera.id,
            task.id if task else None,
            event_type,
            event.id,
            event.recovery_snapshot_path,
            event.recovery_annotated_path,
        )
    open_event_ids.pop(event_type, None)
    return event


def handle_status_change(
    db: Session,
    *,
    camera: Camera,
    task: DetectionTask | None = None,
    status: str,
    open_event_ids: dict[str, int],
    full_frame=None,
    roi_frame=None,
    result: DetectResult | None = None,
    message: str = "",
    rule_detail: dict[str, Any] | None = None,
) -> dict[str, int]:
    event_types = event_types_for_camera(camera, task)
    if not open_event_ids:
        open_event_ids.update(load_open_events(db, camera.id, event_types, task.id if task else None))

    if status in event_types:
        open_event(
            db,
            camera=camera,
            task=task,
            event_type=status,
            open_event_ids=open_event_ids,
            full_frame=full_frame,
            roi_frame=roi_frame,
            result=result,
            message=message or status,
            rule_detail=rule_detail,
        )

    if status in {"RUNNING", "IDLE"}:
        for event_type in list(open_event_ids.keys()):
            recover_event(
                db,
                camera=camera,
                task=task,
                event_type=event_type,
                open_event_ids=open_event_ids,
                full_frame=full_frame,
                roi_frame=roi_frame,
                result=result,
                message=f"recovered to {status}: {message}",
                rule_detail=rule_detail,
            )
    elif status != "OFFLINE":
        recover_event(
            db,
            camera=camera,
            task=task,
            event_type="OFFLINE",
            open_event_ids=open_event_ids,
            full_frame=full_frame,
            roi_frame=roi_frame,
            result=result,
            message=f"video recovered to {status}: {message}",
            rule_detail=rule_detail,
        )

    if status not in {"UNKNOWN", "OFFLINE"}:
        recover_event(
            db,
            camera=camera,
            task=task,
            event_type="UNKNOWN",
            open_event_ids=open_event_ids,
            full_frame=full_frame,
            roi_frame=roi_frame,
            result=result,
            message=f"target recovered to {status}: {message}",
            rule_detail=rule_detail,
        )
    return open_event_ids




def sample_open_event_frames(
    db: Session,
    *,
    camera: Camera,
    task: DetectionTask | None = None,
    open_event_ids: dict[str, int],
    full_frame=None,
    roi_frame=None,
    result: DetectResult | None = None,
    status: str = "",
    message: str = "",
    rule_detail: dict[str, Any] | None = None,
    interval_seconds: int = 10,
    max_frames_per_event: int = 60,
) -> list[EventFrame]:
    """给正在打开的事件周期性保存过程关键帧。

    open/recover 只表示事件边界；sample 帧用于内网复盘：
    - 事件持续很久时，可以看到过程中机器人/画面是否变化；
    - 不会每帧保存，避免磁盘快速膨胀；
    - 每个事件最多保存 max_frames_per_event 张 sample。
    """
    if full_frame is None or not open_event_ids:
        return []
    interval_seconds = max(1, int(interval_seconds or 10))
    max_frames_per_event = max(1, int(max_frames_per_event or 60))
    now = datetime.utcnow()
    created: list[EventFrame] = []
    camera_view = camera
    if task is not None:
        from types import SimpleNamespace

        camera_view = SimpleNamespace(id=camera.id, name=camera.name, detector_type=task.detector_type, roi=task.roi)
    for event_type, event_id in list(open_event_ids.items()):
        event = db.query(Event).filter(Event.id == event_id, Event.status == "OPEN").first()
        if not event:
            continue
        sample_count = db.query(EventFrame).filter(
            EventFrame.event_id == event.id,
            EventFrame.frame_type == "sample",
        ).count()
        if sample_count >= max_frames_per_event:
            continue
        last_sample = (
            db.query(EventFrame)
            .filter(EventFrame.event_id == event.id, EventFrame.frame_type == "sample")
            .order_by(EventFrame.frame_time.desc())
            .first()
        )
        if last_sample and (now - last_sample.frame_time).total_seconds() < interval_seconds:
            continue

        snapshot = snapshot_service.save_image(
            full_frame,
            camera_id=camera.id,
            suffix=f"{event_type.lower()}_sample_raw",
        )
        annotated = None
        if roi_frame is not None and result is not None:
            annotated = snapshot_service.save_annotated_image(
                roi_frame,
                result,
                status or event_type,
                camera=camera_view,
                rule_detail=rule_detail,
                event_type=event_type,
                suffix=f"{event_type.lower()}_sample_annotated",
            )
        created.append(add_event_frame(
            db,
            event=event,
            frame_type="sample",
            status=status or event_type,
            task=task,
            image_path=snapshot,
            annotated_image_path=annotated,
            detector_type=task.detector_type if task else camera.detector_type,
            reason=(rule_detail or {}).get("reason") or message or event.reason,
            rule_detail=rule_detail,
        ))
    return created

def list_events(
    db: Session,
    *,
    camera_id: int | None = None,
    task_id: int | None = None,
    event_type: str | None = None,
    status: str | None = None,
    handled: bool | None = None,
    false_alarm: bool | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    q = db.query(Event)
    if camera_id is not None:
        q = q.filter(Event.camera_id == camera_id)
    if task_id is not None:
        q = q.filter(Event.task_id == task_id)
    if event_type:
        q = q.filter(Event.event_type == event_type.upper())
    if status:
        q = q.filter(Event.status == status.upper())
    if handled is not None:
        q = q.filter(Event.handled == handled)
    if false_alarm is not None:
        q = q.filter(Event.false_alarm == false_alarm)
    if start_time is not None:
        q = q.filter(Event.start_time >= start_time)
    if end_time is not None:
        q = q.filter(Event.start_time <= end_time)
    rows = q.order_by(Event.id.desc()).limit(limit).all()
    now = datetime.utcnow()
    logger.debug("list events count=%s", len(rows))
    return [event_to_dict(e, now, db) for e in rows]


def summary(db: Session, *, camera_id: int | None = None, days: int = 1) -> dict[str, Any]:
    start = datetime.utcnow() - timedelta(days=days)
    q = db.query(Event).filter(Event.start_time >= start)
    if camera_id is not None:
        q = q.filter(Event.camera_id == camera_id)
    rows = q.all()
    by_type = Counter(e.event_type for e in rows)
    by_status = Counter(e.status for e in rows)
    open_events = [e for e in rows if e.status == "OPEN"]
    false_alarm_count = sum(1 for e in rows if e.false_alarm)
    durations = [duration_seconds(e, datetime.utcnow()) or 0 for e in rows if e.status != "OPEN"]
    return {
        "days": days,
        "camera_id": camera_id,
        "total": len(rows),
        "open": len(open_events),
        "false_alarm": false_alarm_count,
        "by_type": dict(by_type),
        "by_status": dict(by_status),
        "avg_duration_seconds": round(sum(durations) / len(durations), 3) if durations else 0,
        "open_events": [event_to_dict(e, db=db) for e in open_events[:50]],
    }


def get_event_or_404(db: Session, event_id: int) -> Event:
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(404, "event not found")
    return event


def set_handled(db: Session, event_id: int, handled: bool = True, remark: str | None = None) -> dict[str, Any]:
    event = get_event_or_404(db, event_id)
    event.handled = handled
    if remark:
        event.remark = remark[:512]
    db.commit()
    logger.info("event handled updated event_id=%s handled=%s", event.id, handled)
    return event_to_dict(event)


def update_remark(db: Session, event_id: int, remark: str = "") -> dict[str, Any]:
    event = get_event_or_404(db, event_id)
    event.remark = remark[:512]
    db.commit()
    logger.info("event remark updated event_id=%s", event.id)
    return event_to_dict(event)


def set_false_alarm(db: Session, event_id: int, flag: bool = True, remark: str | None = None) -> dict[str, Any]:
    event = get_event_or_404(db, event_id)
    event.false_alarm = flag
    event.handled = True if flag else event.handled
    if remark:
        event.remark = remark[:512]
    db.commit()
    logger.info("event false_alarm updated event_id=%s false_alarm=%s", event.id, flag)
    return event_to_dict(event)


def close_manually(db: Session, event_id: int, remark: str | None = None) -> dict[str, Any]:
    event = get_event_or_404(db, event_id)
    if event.status == "OPEN":
        event.status = "RECOVERED"
        event.end_time = datetime.utcnow()
    if remark:
        event.remark = remark[:512]
    db.commit()
    logger.info("event manually closed event_id=%s", event.id)
    return event_to_dict(event)
