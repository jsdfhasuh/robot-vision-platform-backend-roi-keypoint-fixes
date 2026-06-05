from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.models.camera import Camera
from app.models.detection_task import CameraStreamStatus, DetectionTask, TaskStatus
from app.models.status import CameraStatus
from app.services import status_service, worker_service
from app.services.video_stream_service import frame_cache

logger = get_logger(__name__)


def _settings_path() -> str:
    os.makedirs(settings.storage_dir, exist_ok=True)
    return os.path.join(settings.storage_dir, "settings.json")


def camera_code(camera_id: int) -> str:
    return f"cam_{int(camera_id):03d}"


def parse_camera_ref(camera_ref: str | int) -> int:
    if isinstance(camera_ref, int):
        return camera_ref
    ref = str(camera_ref).strip()
    if ref.isdigit():
        return int(ref)
    if ref.lower().startswith("cam_"):
        tail = ref[4:]
        if tail.isdigit():
            return int(tail)
    raise HTTPException(400, f"invalid camera_id: {camera_ref}")


def get_camera_by_ref(db: Session, camera_ref: str | int) -> Camera:
    camera_id = parse_camera_ref(camera_ref)
    camera = db.query(Camera).filter(Camera.id == camera_id).first()
    if not camera:
        raise HTTPException(404, "camera not found")
    return camera


def _mask_rtsp(url: str) -> str:
    if not url:
        return ""
    if "://" not in url or "@" not in url:
        return url
    prefix, rest = url.split("://", 1)
    if "@" not in rest:
        return url
    _, host = rest.rsplit("@", 1)
    return f"{prefix}://****:****@{host}"


def _status_name(status: str | None) -> str:
    if not status:
        return "offline"
    s = status.upper()
    if s in {"RUNNING", "IDLE", "STOPPED", "UNKNOWN"}:
        return "online"
    if s == "OFFLINE":
        return "offline"
    return "error"


def camera_card(db: Session, camera: Camera) -> dict[str, Any]:
    st = db.query(CameraStatus).filter(CameraStatus.camera_id == camera.id).first()
    stream = db.query(CameraStreamStatus).filter(CameraStreamStatus.camera_id == camera.id).first()
    task_rows = db.query(DetectionTask).filter(DetectionTask.camera_id == camera.id).all()
    task_statuses = db.query(TaskStatus).filter(TaskStatus.camera_id == camera.id).all()
    running_count = sum(1 for task in task_rows if worker_service.get_task_result(task.id).get("running"))
    area, line = _split_location(camera.location or "")
    cfg = camera.detector_config or {}
    meta = cfg.get("frontend_meta") if isinstance(cfg, dict) else None
    meta = meta if isinstance(meta, dict) else {}
    area = meta.get("area") or area
    line = meta.get("line") or line
    robot_id = meta.get("robot_id") or f"robot_{camera.id:03d}"
    robot_name = meta.get("robot_name") or camera.name
    return {
        "id": camera_code(camera.id),
        "numeric_id": camera.id,
        "name": camera.name,
        "area": area,
        "line": line,
        "location": camera.location or "",
        "robot_id": robot_id,
        "robot_name": robot_name,
        "enabled": bool(camera.enabled),
        "status": _status_name(st.status if st else None),
        "runtime_state": st.status if st else "UNKNOWN",
        "stream_status": stream.stream_status if stream else "OFFLINE",
        "stream_last_frame_time": _fmt_dt(stream.last_frame_time if stream else None),
        "task_count": len(task_rows),
        "running_task_count": running_count,
        "task_status_summary": {
            "RUNNING": sum(1 for row in task_statuses if row.status == "RUNNING"),
            "IDLE": sum(1 for row in task_statuses if row.status == "IDLE"),
            "STOPPED": sum(1 for row in task_statuses if row.status == "STOPPED"),
            "UNKNOWN": sum(1 for row in task_statuses if row.status == "UNKNOWN"),
            "OFFLINE": sum(1 for row in task_statuses if row.status == "OFFLINE"),
        },
        "stream_type": "mjpeg",
        "last_online_at": _fmt_dt(st.last_frame_time if st else None),
        "rtsp_url_masked": _mask_rtsp(camera.rtsp_url),
        "stream_urls": {
            "mjpeg": f"/stream/cameras/{camera_code(camera.id)}/mjpeg",
            "mjpeg_annotated": f"/stream/cameras/{camera_code(camera.id)}/mjpeg?annotated=true",
            "snapshot": f"/stream/cameras/{camera_code(camera.id)}/snapshot",
        },
        "detector_type": camera.detector_type,
        "fps_limit": camera.fps_limit,
        "motion_threshold": camera.motion_threshold,
        "stop_seconds": camera.stop_seconds,
        "config_version": camera.config_version,
    }


def _split_location(location: str) -> tuple[str, str]:
    if not location:
        return "", ""
    for sep in ["/", "-", "，", ","]:
        if sep in location:
            a, b = location.split(sep, 1)
            return a.strip(), b.strip()
    return location, ""


def _fmt_dt(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return value.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(value)


def runtime_status_list(db: Session) -> list[dict[str, Any]]:
    cameras = db.query(Camera).order_by(Camera.id.asc()).all()
    rows: list[dict[str, Any]] = []
    for camera in cameras:
        st = db.query(CameraStatus).filter(CameraStatus.camera_id == camera.id).first()
        worker = worker_service.get_last_result(camera.id)
        last_result = worker.get("last_result") or {}
        meta = last_result.get("metadata") or {}
        tracker = worker.get("tracker") or meta.get("tracker") or {}
        rule = worker.get("rule") or meta.get("rule_detail") or {}
        kps = meta.get("keypoints") or meta.get("keypoints_full") or []
        valid_kps = meta.get("valid_keypoints")
        if valid_kps is None and isinstance(kps, list):
            conf_th = _keypoint_conf(camera)
            valid_kps = sum(1 for p in kps if len(p) >= 3 and float(p[2] or 0) >= conf_th)
        moving_keypoints = _moving_keypoint_count(kps, tracker, camera)
        rows.append({
            "camera_id": camera_code(camera.id),
            "numeric_camera_id": camera.id,
            "camera_name": camera.name,
            "robot_id": f"robot_{camera.id:03d}",
            "robot_name": camera.name,
            "state": st.status if st else "UNKNOWN",
            "fps": worker.get("detect_fps_actual") or worker.get("fps_actual") or 0,
            "valid_keypoints": int(valid_kps or 0),
            "moving_keypoints": int(moving_keypoints or 0),
            "mean_delta": _first_number(tracker, ["keypoint_mean_step", "avg_speed", "total_displacement"], default=0),
            "max_delta": _first_number(tracker, ["keypoint_max_step", "max_step"], default=0),
            "motion_score": last_result.get("motion_distance") or 0,
            "stop_duration_seconds": rule.get("stop_duration_seconds") or rule.get("idle_seconds") or 0,
            "last_update_at": _fmt_dt(st.updated_at if st else None),
            "message": st.message if st else "",
            "rule": rule,
        })
    return rows


def _first_number(data: dict, keys: list[str], default=0):
    for key in keys:
        val = data.get(key)
        if isinstance(val, (int, float)):
            return round(float(val), 3)
    return default


def _keypoint_conf(camera: Camera) -> float:
    cfg = camera.detector_config or {}
    try:
        return float(cfg.get("keypoint_conf_threshold", 0.25))
    except Exception:
        return 0.25


def _moving_keypoint_count(kps: list, tracker: dict, camera: Camera) -> int:
    if not kps:
        return 0
    threshold = float(camera.motion_threshold or 0)
    deltas = tracker.get("keypoint_deltas") if isinstance(tracker, dict) else None
    if isinstance(deltas, list) and deltas:
        return sum(1 for row in deltas if bool(row.get("valid")) and float(row.get("delta_px") or 0) > threshold)
    score = _first_number(tracker, ["keypoint_mean_step", "keypoint_max_step", "total_displacement"], default=0)
    if score <= threshold:
        return 0
    return sum(1 for p in kps if len(p) >= 3 and float(p[2] or 0) >= _keypoint_conf(camera))


def stream_info(db: Session, camera_ref: str | int) -> dict[str, Any]:
    camera = get_camera_by_ref(db, camera_ref)
    info = frame_cache.info(camera.id)
    size = info.get("raw_shape") or info.get("annotated_shape") or None
    width = int(size[1]) if isinstance(size, (list, tuple)) and len(size) >= 2 else None
    height = int(size[0]) if isinstance(size, (list, tuple)) and len(size) >= 2 else None
    return {
        "camera_id": camera_code(camera.id),
        "numeric_camera_id": camera.id,
        "stream_type": "mjpeg",
        "mjpeg_url": f"/stream/cameras/{camera_code(camera.id)}/mjpeg",
        "annotated_mjpeg_url": f"/stream/cameras/{camera_code(camera.id)}/mjpeg?annotated=true",
        "snapshot_url": f"/stream/cameras/{camera_code(camera.id)}/snapshot",
        "annotated_snapshot_url": f"/stream/cameras/{camera_code(camera.id)}/snapshot?annotated=true",
        "hls_url": f"/stream/cameras/{camera_code(camera.id)}/hls/index.m3u8",
        "width": width,
        "height": height,
        "fps": 0,
        "online": bool(info.get("has_raw_frame") or info.get("has_annotated_frame")),
        "cache": info,
    }


def _frame_size(camera_id: int) -> tuple[int, int]:
    info = frame_cache.info(camera_id)
    shape = info.get("raw_shape") or info.get("annotated_shape")
    if isinstance(shape, (list, tuple)) and len(shape) >= 2:
        return int(shape[1]), int(shape[0])
    return 1920, 1080


def _rect_to_normalized_roi(camera: Camera, width: int, height: int) -> dict[str, Any]:
    roi = camera.roi if isinstance(camera.roi, list) else None
    if not roi or len(roi) != 4:
        points = [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 0.0}, {"x": 1.0, "y": 1.0}, {"x": 0.0, "y": 1.0}]
    else:
        x1, y1, x2, y2 = [float(v) for v in roi]
        points = [
            {"x": max(0, min(1, x1 / width)), "y": max(0, min(1, y1 / height))},
            {"x": max(0, min(1, x2 / width)), "y": max(0, min(1, y1 / height))},
            {"x": max(0, min(1, x2 / width)), "y": max(0, min(1, y2 / height))},
            {"x": max(0, min(1, x1 / width)), "y": max(0, min(1, y2 / height))},
        ]
    cfg = camera.detector_config or {}
    target_keypoints = cfg.get("target_keypoints") if isinstance(cfg, dict) else None
    return {
        "id": "roi_1",
        "name": "机器人本体区域",
        "enabled": True,
        "type": "polygon",
        "points": points,
        "keypoint_indexes": target_keypoints or [],
    }


def get_roi_payload(db: Session, camera_ref: str | int) -> dict[str, Any]:
    camera = get_camera_by_ref(db, camera_ref)
    width, height = _frame_size(camera.id)
    cfg = camera.detector_config or {}
    roi_config = cfg.get("roi_config") if isinstance(cfg, dict) else None
    if not isinstance(roi_config, dict):
        roi_config = {"rois": [_rect_to_normalized_roi(camera, width, height)], "exclude_zones": []}
    return {
        "camera_id": camera_code(camera.id),
        "numeric_camera_id": camera.id,
        "image_width": int(roi_config.get("image_width") or width),
        "image_height": int(roi_config.get("image_height") or height),
        "rois": roi_config.get("rois") or [],
        "exclude_zones": roi_config.get("exclude_zones") or [],
        "pixel_roi": camera.roi,
    }


def save_roi_payload(db: Session, camera_ref: str | int, payload: dict[str, Any]) -> dict[str, Any]:
    camera = get_camera_by_ref(db, camera_ref)
    width = int(payload.get("image_width") or _frame_size(camera.id)[0])
    height = int(payload.get("image_height") or _frame_size(camera.id)[1])
    rois = payload.get("rois") or []
    exclude_zones = payload.get("exclude_zones") or []
    roi_config = {
        "image_width": width,
        "image_height": height,
        "rois": rois,
        "exclude_zones": exclude_zones,
        "coordinate_mode": "normalized",
        "updated_at": datetime.utcnow().isoformat(),
    }
    pixel_roi = _normalized_rois_to_pixel_rect(rois, width, height)
    cfg = dict(camera.detector_config or {})
    cfg["roi_config"] = roi_config
    camera.detector_config = cfg
    camera.roi = pixel_roi
    camera.config_version = (camera.config_version or 1) + 1
    db.commit()
    db.refresh(camera)
    logger.info("roi saved camera_id=%s pixel_roi=%s config_version=%s", camera.id, pixel_roi, camera.config_version)
    return get_roi_payload(db, camera.id)


def _normalized_rois_to_pixel_rect(rois: list, width: int, height: int) -> list[int] | None:
    points: list[tuple[float, float]] = []
    for roi in rois:
        if not roi or roi.get("enabled") is False:
            continue
        for p in roi.get("points") or []:
            try:
                x = float(p.get("x"))
                y = float(p.get("y"))
                # 兼容前端误传像素坐标。
                if x > 1.0 or y > 1.0:
                    x = x / max(width, 1)
                    y = y / max(height, 1)
                points.append((max(0.0, min(1.0, x)), max(0.0, min(1.0, y))))
            except Exception:
                continue
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x1 = int(max(0, min(xs)) * width)
    y1 = int(max(0, min(ys)) * height)
    x2 = int(min(1, max(xs)) * width)
    y2 = int(min(1, max(ys)) * height)
    if x2 <= x1 or y2 <= y1:
        return None
    return [x1, y1, x2, y2]


def default_settings(db: Session | None = None) -> dict[str, Any]:
    camera = None
    if db is not None:
        camera = db.query(Camera).order_by(Camera.id.asc()).first()
    cfg = camera.detector_config if camera and isinstance(camera.detector_config, dict) else {}
    return {
        "detect": {
            "detector_type": camera.detector_type if camera else "yolo_pose",
            "motion_threshold": camera.motion_threshold if camera else 4,
            "stop_duration_seconds": camera.stop_seconds if camera else 30,
            "recover_duration_seconds": (cfg.get("rule") or {}).get("recover_seconds", 3) if isinstance(cfg, dict) else 3,
            "detect_interval_ms": int(1000 / max(camera.fps_limit, 1)) if camera else 200,
            "roi_filter_mode": cfg.get("roi_filter_mode", "filter_keypoints") if isinstance(cfg, dict) else "filter_keypoints",
        },
        "detector_config": cfg or {
            "model_path": "/app/models/robot_pose.onnx",
            "model_family": "yolo11_pose",
            "input_size": 640,
            "num_keypoints": 6,
            "class_count": 1,
            "target_keypoints": [2, 3, 4, 5],
            "motion_mode": "mean",
            "keypoint_conf_threshold": 0.25,
            "providers": ["CPUExecutionProvider"],
        },
        "keypoint_rules": (cfg.get("keypoint_rules") if isinstance(cfg, dict) else None) or [],
        "video": {
            "target_fps": camera.fps_limit if camera else 5,
            "frame_width": 1280,
            "frame_height": 720,
            "snapshot_interval_seconds": settings.event_frame_sample_seconds,
        },
        "alarm": {
            "alarm_enabled": False,
            "alarm_cooldown_seconds": 60,
            "save_snapshot": True,
            "save_video_clip": False,
        },
        "log": {"log_level": settings.log_level, "log_retention_days": settings.log_backup_count},
    }


def load_settings(db: Session | None = None) -> dict[str, Any]:
    path = _settings_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            logger.exception("load settings failed path=%s", path)
    return default_settings(db)


def save_settings(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload or {}
    with open(_settings_path(), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    logger.info("settings saved path=%s", _settings_path())
    return data


def apply_settings(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    data = save_settings(payload)
    detect = data.get("detect") or {}
    detector_config = data.get("detector_config") or {}
    video = data.get("video") or {}
    camera_refs = data.get("camera_ids") or data.get("camera_id")
    if camera_refs is None:
        cameras = db.query(Camera).all()
    else:
        if not isinstance(camera_refs, list):
            camera_refs = [camera_refs]
        ids = [parse_camera_ref(x) for x in camera_refs]
        cameras = db.query(Camera).filter(Camera.id.in_(ids)).all()
    changed_ids: list[int] = []
    for camera in cameras:
        changed = False
        if detect.get("detector_type") and camera.detector_type != detect.get("detector_type"):
            camera.detector_type = str(detect.get("detector_type"))
            changed = True
        if "motion_threshold" in detect and camera.motion_threshold != float(detect["motion_threshold"]):
            camera.motion_threshold = float(detect["motion_threshold"])
            changed = True
        if "stop_duration_seconds" in detect and camera.stop_seconds != int(detect["stop_duration_seconds"]):
            camera.stop_seconds = int(detect["stop_duration_seconds"])
            changed = True
        if video.get("target_fps") and camera.fps_limit != int(video["target_fps"]):
            camera.fps_limit = max(1, min(30, int(video["target_fps"])))
            changed = True
        if detector_config and camera.detector_config != detector_config:
            camera.detector_config = detector_config
            changed = True
        if changed:
            camera.config_version = (camera.config_version or 1) + 1
            changed_ids.append(camera.id)
    db.commit()
    logger.info("settings applied cameras=%s", changed_ids)
    return {"settings": data, "applied_camera_ids": changed_ids}


def reset_settings(db: Session | None = None) -> dict[str, Any]:
    data = default_settings(db)
    save_settings(data)
    return data


def keypoints_debug(db: Session, camera_ref: str | int) -> dict[str, Any]:
    camera = get_camera_by_ref(db, camera_ref)
    worker = worker_service.get_last_result(camera.id)
    last_result = worker.get("last_result") or {}
    meta = last_result.get("metadata") or {}
    keypoints = meta.get("keypoints_full") or meta.get("keypoints") or []
    info = frame_cache.info(camera.id)
    shape = info.get("raw_shape") or info.get("annotated_shape") or []
    frame_height = int(shape[0]) if isinstance(shape, (list, tuple)) and len(shape) >= 2 else 1080
    frame_width = int(shape[1]) if isinstance(shape, (list, tuple)) and len(shape) >= 2 else 1920
    tracker = worker.get("tracker") or meta.get("tracker") or {}
    roi_filter = meta.get("roi_filter") if isinstance(meta, dict) else None
    bounds = roi_filter.get("bounds") if isinstance(roi_filter, dict) else None
    dx, dy = (int(bounds[0]), int(bounds[1])) if isinstance(bounds, list) and len(bounds) >= 2 else (0, 0)
    cfg = camera.detector_config or {}
    labels = cfg.get("keypoint_names") or cfg.get("labels") or [] if isinstance(cfg, dict) else []
    conf_th = _keypoint_conf(camera)
    motion_threshold = float(camera.motion_threshold or 0)
    kp_rows = []
    delta_by_index = {}
    for row in tracker.get("keypoint_deltas") or []:
        try:
            delta_by_index[int(row.get("index"))] = row
        except Exception:
            continue
    for idx, kp in enumerate(keypoints or []):
        if not kp or len(kp) < 2:
            continue
        x, y = float(kp[0]), float(kp[1])
        x_full, y_full = x + dx, y + dy
        conf = float(kp[2]) if len(kp) > 2 else 1.0
        delta_info = delta_by_index.get(idx, {})
        delta = float(delta_info.get("delta_px") or 0.0)
        avg_delta = float(delta_info.get("avg_delta_px") or delta)
        kp_rows.append({
            "index": idx,
            "name": labels[idx] if isinstance(labels, list) and idx < len(labels) else f"kp_{idx}",
            "x": max(0, min(1, x_full / max(frame_width, 1))),
            "y": max(0, min(1, y_full / max(frame_height, 1))),
            "x_px": round(x_full, 3),
            "y_px": round(y_full, 3),
            "x_roi_px": round(x, 3),
            "y_roi_px": round(y, 3),
            "confidence": round(conf, 4),
            "delta_px": round(delta, 3),
            "avg_delta_px": round(avg_delta, 3),
            "moving": bool(delta > motion_threshold and conf >= conf_th),
            "valid": bool(conf >= conf_th and delta_info.get("valid", True)),
        })
    valid = sum(1 for k in kp_rows if k["valid"])
    moving = sum(1 for k in kp_rows if k["moving"])
    state = worker.get("last_status") or "UNKNOWN"
    reason = (worker.get("rule") or {}).get("reason") or (worker.get("rule") or {}).get("message") or last_result.get("message") or ""
    bbox = last_result.get("bbox")
    bbox_obj = None
    if isinstance(bbox, list) and len(bbox) == 4:
        bbox_obj = {"x1": bbox[0] + dx, "y1": bbox[1] + dy, "x2": bbox[2] + dx, "y2": bbox[3] + dy, "confidence": last_result.get("confidence") or 0, "class_id": meta.get("class_id", 0), "roi_bbox": bbox}
    return {
        "camera_id": camera_code(camera.id),
        "numeric_camera_id": camera.id,
        "state": state,
        "frame_width": frame_width,
        "frame_height": frame_height,
        "bbox": bbox_obj,
        "keypoints": kp_rows,
        "summary": {
            "valid_keypoints": valid,
            "moving_keypoints": moving,
            "mean_delta": _first_number(tracker, ["keypoint_mean_step", "avg_speed", "total_displacement"], default=0),
            "max_delta": _first_number(tracker, ["keypoint_max_step", "max_step"], default=0),
            "reason": reason,
            "motion_score": last_result.get("motion_distance") or 0,
            "rule": worker.get("rule"),
        },
        "annotated_image_url": worker.get("last_annotated_url"),
        "worker": worker,
    }


def evaluate_keypoints(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    camera_ref = payload.get("camera_id") or payload.get("numeric_camera_id")
    if camera_ref is None:
        raise HTTPException(400, "camera_id is required")
    debug = keypoints_debug(db, camera_ref)
    settings_payload = payload.get("settings") or {}
    threshold = float(settings_payload.get("motion_threshold") or debug["summary"].get("motion_threshold") or 4)
    stop_seconds = int(settings_payload.get("stop_duration_seconds") or 30)
    moving = debug["summary"].get("mean_delta", 0) > threshold or debug["summary"].get("max_delta", 0) > threshold
    state = "RUNNING" if moving else "IDLE"
    return {
        "state": state,
        "valid_keypoints": debug["summary"]["valid_keypoints"],
        "moving_keypoints": debug["summary"]["moving_keypoints"],
        "mean_delta": debug["summary"]["mean_delta"],
        "max_delta": debug["summary"]["max_delta"],
        "reason": f"mean/max delta compared with threshold {threshold}px",
        "trigger_frames": 1 if moving else 0,
        "alarm": False if moving else stop_seconds <= 0,
        "source_state": debug["state"],
    }
