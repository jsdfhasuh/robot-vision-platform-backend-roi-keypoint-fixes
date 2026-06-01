from __future__ import annotations

import cv2

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.detectors.factory import create_detector
from app.models.camera import Camera
from app.rules.robot_stop_rule import RobotStopRule
from app.services import camera_service, snapshot_service, roi_service
from app.tracker.simple_tracker import SimpleTracker
from app.utils.annotator import draw_detection

logger = get_logger(__name__)


def _offset_point(point, dx: int, dy: int):
    if point is None:
        return None
    return [float(point[0]) + dx, float(point[1]) + dy]


def _offset_bbox(bbox, dx: int, dy: int):
    if bbox is None:
        return None
    x1, y1, x2, y2 = bbox
    return [float(x1) + dx, float(y1) + dy, float(x2) + dx, float(y2) + dy]


def _offset_keypoints(metadata: dict, dx: int, dy: int):
    meta = dict(metadata or {})
    kps = meta.get("keypoints")
    if isinstance(kps, list):
        new_kps = []
        for kp in kps:
            if not kp or len(kp) < 2:
                new_kps.append(kp)
                continue
            conf = kp[2] if len(kp) > 2 else 1.0
            new_kps.append([float(kp[0]) + dx, float(kp[1]) + dy, conf])
        meta["keypoints_full"] = new_kps
    return meta


def _build_runtime(camera: Camera):
    detector = create_detector(camera.detector_type, camera.detector_config or {})
    tracker_config = (camera.detector_config or {}).get("tracker", {}) if isinstance(camera.detector_config, dict) else {}
    tracker = SimpleTracker(tracker_config)
    rule_config = (camera.detector_config or {}).get("rule", {}) if isinstance(camera.detector_config, dict) else {}
    rule = RobotStopRule(camera.motion_threshold, camera.stop_seconds, rule_config)
    return detector, tracker, rule


def _detect_once(camera: Camera, roi_frame, *, warmup_frame=None, roi_ctx=None):
    detector, tracker, rule = _build_runtime(camera)
    warmup_result = None
    if warmup_frame is not None:
        warmup_result = detector.detect(warmup_frame)
        if roi_ctx is not None:
            warmup_result = roi_service.apply_result_filter(warmup_result, camera, roi_ctx)
        tracker.update(warmup_result)
    raw_result = detector.detect(roi_frame)
    if roi_ctx is not None:
        raw_result = roi_service.apply_result_filter(raw_result, camera, roi_ctx)
    result, stats = tracker.update(raw_result)
    status, last_motion_time, message = rule.update(result)
    result.metadata = dict(result.metadata or {})
    result.metadata["rule_detail"] = rule.last_detail
    return warmup_result, result, stats, rule, status, last_motion_time, message


def _result_payload(result, *, dx=0, dy=0):
    return {
        "target_found": result.target_found,
        "motion_distance": result.motion_distance,
        "confidence": result.confidence,
        "message": result.message,
        "center_roi": result.center,
        "center_full": _offset_point(result.center, dx, dy),
        "bbox_roi": result.bbox,
        "bbox_full": _offset_bbox(result.bbox, dx, dy),
        "metadata": _offset_keypoints(result.metadata, dx, dy),
    }


def snapshot_camera(db: Session, camera_id: int) -> dict:
    camera = camera_service.get_camera(db, camera_id)
    cap = cv2.VideoCapture(camera.rtsp_url)
    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        raise HTTPException(502, "rtsp read failed")
    path = snapshot_service.save_image(frame, camera_id=camera.id, suffix="snapshot")
    return {"ok": bool(path), "path": path, "url": snapshot_service.storage_url(path)}


def debug_detect_camera(db: Session, camera_id: int) -> dict:
    camera = camera_service.get_camera(db, camera_id)
    cap = cv2.VideoCapture(camera.rtsp_url)
    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        raise HTTPException(502, "rtsp read failed")

    roi_frame, roi_ctx = roi_service.crop_and_mask(frame, camera)
    _, result, stats, rule, status, last_motion_time, message = _detect_once(camera, roi_frame, roi_ctx=roi_ctx)
    annotated = draw_detection(
        roi_frame,
        result,
        status,
        rule_detail=rule.last_detail,
        camera_name=camera.name,
        detector_type=camera.detector_type,
        roi=list(roi_ctx.bounds),
    )
    path = snapshot_service.save_image(annotated, camera_id=camera.id, suffix="debug_detect")
    return {
        "ok": True,
        "detector_type": camera.detector_type,
        "status": status,
        "last_motion_time": last_motion_time,
        "annotated_image_path": path,
        "annotated_image_url": snapshot_service.storage_url(path),
        "result": {
            "target_found": result.target_found,
            "motion_distance": result.motion_distance,
            "confidence": result.confidence,
            "message": result.message,
            "center": result.center,
            "bbox": result.bbox,
            "metadata": result.metadata,
        },
        "tracker": stats.to_dict(),
        "rule": rule.last_detail,
    }


async def image_detect_camera(db: Session, camera_id: int, file: UploadFile) -> dict:
    camera = camera_service.get_camera(db, camera_id)
    content = await file.read()
    if not content:
        raise HTTPException(400, "empty image file")
    frame = snapshot_service.decode_upload_image(content)
    if frame is None:
        raise HTTPException(400, "invalid image file")

    roi_frame, roi_ctx = roi_service.crop_and_mask(frame, camera)
    x1, y1, x2, y2 = roi_ctx.bounds
    logger.info(
        "image detect camera_id=%s filename=%s detector=%s image_shape=%s roi=%s",
        camera.id,
        file.filename,
        camera.detector_type,
        frame.shape,
        [x1, y1, x2, y2],
    )

    _, result, stats, rule, status, last_motion_time, message = _detect_once(camera, roi_frame, roi_ctx=roi_ctx)
    annotated_roi = draw_detection(
        roi_frame,
        result,
        status,
        rule_detail=rule.last_detail,
        camera_name=camera.name,
        detector_type=camera.detector_type,
        roi=[x1, y1, x2, y2],
    )
    annotated_full = snapshot_service.draw_roi_on_full_frame(frame, annotated_roi, (x1, y1, x2, y2))
    path = snapshot_service.save_image(annotated_full, camera_id=camera.id, suffix="image_detect")
    return {
        "ok": True,
        "camera_id": camera.id,
        "filename": file.filename,
        "detector_type": camera.detector_type,
        "status": status,
        "rule": rule.last_detail,
        "image_size": {"width": int(frame.shape[1]), "height": int(frame.shape[0])},
        "roi": [x1, y1, x2, y2],
        "annotated_image_path": path,
        "annotated_image_url": snapshot_service.storage_url(path),
        "result": _result_payload(result, dx=x1, dy=y1),
        "tracker": stats.to_dict(),
    }


async def image_pair_detect_camera(db: Session, camera_id: int, before: UploadFile, after: UploadFile) -> dict:
    camera = camera_service.get_camera(db, camera_id)
    frame1 = snapshot_service.decode_upload_image(await before.read())
    frame2 = snapshot_service.decode_upload_image(await after.read())
    if frame1 is None or frame2 is None:
        raise HTTPException(400, "invalid image file")

    roi2, roi_ctx = roi_service.crop_and_mask(frame2, camera)
    x1, y1, x2, y2 = roi_ctx.bounds
    roi1 = frame1[y1:y2, x1:x2]
    # before/after 使用相同 ROI mask，保证 motion/pose 对比一致。
    if roi_ctx.roi_config and roi_ctx.include_polygons:
        mask = None
        import numpy as np, cv2
        mask = np.zeros(roi1.shape[:2], dtype=np.uint8)
        cv2.fillPoly(mask, roi_ctx.include_polygons, 255)
        if roi_ctx.exclude_polygons:
            cv2.fillPoly(mask, roi_ctx.exclude_polygons, 0)
        roi1 = cv2.bitwise_and(roi1, roi1, mask=mask)

    warmup, result, stats, rule, status, last_motion_time, message = _detect_once(camera, roi2, warmup_frame=roi1, roi_ctx=roi_ctx)
    annotated_roi = draw_detection(
        roi2,
        result,
        status,
        rule_detail=rule.last_detail,
        camera_name=camera.name,
        detector_type=camera.detector_type,
        roi=[x1, y1, x2, y2],
    )
    annotated_full = snapshot_service.draw_roi_on_full_frame(frame2, annotated_roi, (x1, y1, x2, y2))
    path = snapshot_service.save_image(annotated_full, camera_id=camera.id, suffix="image_pair_detect")
    return {
        "ok": True,
        "camera_id": camera.id,
        "detector_type": camera.detector_type,
        "status": status,
        "rule": rule.last_detail,
        "roi": [x1, y1, x2, y2],
        "annotated_image_path": path,
        "annotated_image_url": snapshot_service.storage_url(path),
        "before_result": {
            "target_found": warmup.target_found if warmup else False,
            "motion_distance": warmup.motion_distance if warmup else 0,
            "confidence": warmup.confidence if warmup else 0,
            "message": warmup.message if warmup else "",
            "center_roi": warmup.center if warmup else None,
            "bbox_roi": warmup.bbox if warmup else None,
        },
        "after_result": _result_payload(result, dx=x1, dy=y1),
        "tracker": stats.to_dict(),
    }
