from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session

from app.core.compat_responses import code_ok
from app.database import get_db
from app.services import frontend_adapter_service as fas
from app.services.video_stream_service import get_latest_jpeg, mjpeg_generator

router = APIRouter(tags=["frontend-compat"])


@router.get("/api/runtime/status")
def runtime_status(db: Session = Depends(get_db)):
    return code_ok(fas.runtime_status_list(db))


@router.get("/api/cameras/{camera_ref}/stream-info")
def compat_stream_info(camera_ref: str, db: Session = Depends(get_db)):
    return code_ok(fas.stream_info(db, camera_ref))


@router.get("/stream/cameras/{camera_ref}/snapshot")
def compat_snapshot(
    camera_ref: str,
    annotated: bool = Query(False),
    quality: int = Query(85, ge=10, le=100),
    max_width: int | None = Query(None, ge=160, le=4096),
    db: Session = Depends(get_db),
):
    camera = fas.get_camera_by_ref(db, camera_ref)
    jpg, ts, is_placeholder = get_latest_jpeg(
        camera.id,
        annotated=annotated,
        quality=quality,
        max_width=max_width,
        placeholder=True,
    )
    if not jpg:
        raise HTTPException(404, "no frame available")
    headers = {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "X-Frame-Placeholder": "1" if is_placeholder else "0",
    }
    if ts:
        headers["X-Frame-Time"] = ts.isoformat()
    return Response(content=jpg, media_type="image/jpeg", headers=headers)


@router.get("/stream/cameras/{camera_ref}/mjpeg")
def compat_mjpeg(
    camera_ref: str,
    annotated: bool = Query(False),
    fps: float = Query(8.0, ge=0.2, le=25.0),
    quality: int = Query(80, ge=10, le=100),
    max_width: int | None = Query(None, ge=160, le=4096),
    db: Session = Depends(get_db),
):
    camera = fas.get_camera_by_ref(db, camera_ref)
    return StreamingResponse(
        mjpeg_generator(
            camera.id,
            annotated=annotated,
            fps=fps,
            quality=quality,
            max_width=max_width,
        ),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


@router.get("/stream/cameras/{camera_ref}/hls/index.m3u8")
def compat_hls_placeholder(camera_ref: str):
    raise HTTPException(501, "HLS is not implemented yet; use MJPEG stream")


@router.get("/api/cameras/{camera_ref}/roi")
def get_camera_roi(camera_ref: str, db: Session = Depends(get_db)):
    return code_ok(fas.get_roi_payload(db, camera_ref))


@router.post("/api/cameras/{camera_ref}/roi")
async def save_camera_roi(camera_ref: str, request: Request, db: Session = Depends(get_db)):
    payload = await request.json()
    return code_ok(fas.save_roi_payload(db, camera_ref, payload), "roi saved")


@router.get("/api/settings")
def get_settings(db: Session = Depends(get_db)):
    return code_ok(fas.load_settings(db))


@router.post("/api/settings/save")
async def save_settings(request: Request):
    payload = await request.json()
    return code_ok(fas.save_settings(payload), "settings saved")


@router.post("/api/settings/apply")
async def apply_settings(request: Request, db: Session = Depends(get_db)):
    payload = await request.json()
    return code_ok(fas.apply_settings(db, payload), "settings applied")


@router.post("/api/settings/reset")
def reset_settings(db: Session = Depends(get_db)):
    return code_ok(fas.reset_settings(db), "settings reset")


@router.get("/api/debug/keypoints")
def debug_keypoints(camera_id: str = Query(...), db: Session = Depends(get_db)):
    return code_ok(fas.keypoints_debug(db, camera_id))


@router.post("/api/debug/keypoints/evaluate")
async def evaluate_keypoints(request: Request, db: Session = Depends(get_db)):
    payload = await request.json()
    return code_ok(fas.evaluate_keypoints(db, payload))

# -------------------------
# 前端“告警中心”兼容层：后端实际使用 events 事件中心。
# -------------------------
from fastapi import Body
from app.services import event_service, snapshot_service, camera_service
from app.models.camera import Camera
from app.models.event import Event
from datetime import datetime
import os


def _alarm_item(db: Session, event_dict: dict) -> dict:
    camera = db.query(Camera).filter(Camera.id == event_dict.get("camera_id")).first()
    event_type = event_dict.get("event_type") or "UNKNOWN"
    status = "unhandled" if not event_dict.get("handled") else "handled"
    if event_dict.get("false_alarm"):
        status = "false_alarm"
    return {
        "id": f"alarm_{int(event_dict['id']):03d}",
        "numeric_id": event_dict["id"],
        "camera_id": fas.camera_code(event_dict.get("camera_id")),
        "numeric_camera_id": event_dict.get("camera_id"),
        "camera_name": camera.name if camera else "",
        "robot_id": f"robot_{int(event_dict.get('camera_id') or 0):03d}",
        "robot_name": camera.name if camera else "",
        "event_type": event_type,
        "level": "critical" if event_type == "STOPPED" else "warning",
        "status": status,
        "event_status": event_dict.get("status"),
        "duration_seconds": event_dict.get("duration_seconds") or 0,
        "message": event_dict.get("reason") or event_dict.get("remark") or event_type,
        "snapshot_url": f"/api/alarms/alarm_{int(event_dict['id']):03d}/snapshot",
        "annotated_snapshot_url": event_dict.get("annotated_snapshot_url"),
        "clip_url": event_dict.get("clip_url"),
        "created_at": event_dict.get("start_time"),
        "recovered_at": event_dict.get("end_time"),
        "raw_event": event_dict,
    }


def _parse_alarm_id(alarm_id: str | int) -> int:
    if isinstance(alarm_id, int):
        return alarm_id
    ref = str(alarm_id)
    if ref.startswith("alarm_"):
        ref = ref[6:]
    if not ref.isdigit():
        raise HTTPException(400, "invalid alarm_id")
    return int(ref)


@router.get("/api/alarms")
def list_alarms(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    status: str | None = Query(None),
    camera_id: str | None = Query(None),
    db: Session = Depends(get_db),
):
    event_status = None
    handled = None
    false_alarm = None
    if status in {"unhandled", "open"}:
        handled = False
    elif status == "handled":
        handled = True
    elif status == "false_alarm":
        false_alarm = True
    elif status:
        event_status = status
    numeric_camera = fas.parse_camera_ref(camera_id) if camera_id else None
    limit = page * page_size
    events = event_service.list_events(
        db,
        camera_id=numeric_camera,
        status=event_status,
        handled=handled,
        false_alarm=false_alarm,
        limit=limit,
    )
    start = (page - 1) * page_size
    items = [_alarm_item(db, e) for e in events[start:start + page_size]]
    return code_ok({"total": len(events), "items": items, "page": page, "page_size": page_size})


@router.put("/api/alarms/{alarm_id}/ack")
async def ack_alarm(alarm_id: str, request: Request, db: Session = Depends(get_db)):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    event_id = _parse_alarm_id(alarm_id)
    data = event_service.set_handled(db, event_id, True, payload.get("remark") if isinstance(payload, dict) else None)
    return code_ok(_alarm_item(db, data), "alarm acknowledged")


@router.get("/api/alarms/{alarm_id}/snapshot")
def alarm_snapshot(alarm_id: str, annotated: bool = Query(False), db: Session = Depends(get_db)):
    event_id = _parse_alarm_id(alarm_id)
    event = event_service.get_event_or_404(db, event_id)
    path = event.annotated_snapshot_path if annotated else event.snapshot_path
    if not path:
        path = event.recovery_annotated_path if annotated else event.recovery_snapshot_path
    if not path or not os.path.exists(path):
        raise HTTPException(404, "snapshot not found")
    with open(path, "rb") as f:
        data = f.read()
    return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
