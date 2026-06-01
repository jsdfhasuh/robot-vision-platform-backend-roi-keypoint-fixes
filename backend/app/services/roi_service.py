from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from dataclasses import replace

import cv2
import numpy as np

from app.core.logging import get_logger
from app.detectors.base import DetectResult
from app.models.camera import Camera

logger = get_logger(__name__)


@dataclass
class RoiContext:
    """一次检测使用的 ROI 上下文。所有点坐标均为裁剪后的 ROI 局部坐标。"""

    enabled: bool
    bounds: tuple[int, int, int, int]
    include_polygons: list[np.ndarray]
    exclude_polygons: list[np.ndarray]
    keypoint_indexes: set[int] | None
    roi_config: dict[str, Any] | None = None

    @property
    def offset(self) -> tuple[int, int]:
        return self.bounds[0], self.bounds[1]


def get_roi_config(camera: Camera) -> dict[str, Any] | None:
    cfg = camera.detector_config or {}
    if not isinstance(cfg, dict):
        return None
    roi_config = cfg.get("roi_config")
    return roi_config if isinstance(roi_config, dict) else None


def _coerce_norm_point(p: Any, width: int, height: int) -> tuple[float, float] | None:
    if not isinstance(p, dict):
        return None
    try:
        x = float(p.get("x"))
        y = float(p.get("y"))
    except Exception:
        return None
    # 兼容前端误传像素坐标。
    if x > 1.0 or y > 1.0:
        x = x / max(width, 1)
        y = y / max(height, 1)
    return max(0.0, min(1.0, x)), max(0.0, min(1.0, y))


def _norm_points_to_pixels(points: list, width: int, height: int) -> np.ndarray | None:
    pts: list[list[int]] = []
    for p in points or []:
        norm = _coerce_norm_point(p, width, height)
        if norm is None:
            continue
        x, y = norm
        pts.append([int(round(x * width)), int(round(y * height))])
    if len(pts) < 3:
        return None
    return np.asarray(pts, dtype=np.int32)


def _points_from_rect_roi(roi: list | None, width: int, height: int) -> np.ndarray | None:
    if not roi or len(roi) != 4:
        return np.asarray([[0, 0], [width, 0], [width, height], [0, height]], dtype=np.int32)
    x1, y1, x2, y2 = [int(v) for v in roi]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(width, x2), min(height, y2)
    if x2 <= x1 or y2 <= y1:
        return None
    return np.asarray([[x1, y1], [x2, y1], [x2, y2], [x1, y2]], dtype=np.int32)


def _polygon_bounds(polygons: list[np.ndarray], width: int, height: int) -> tuple[int, int, int, int]:
    if not polygons:
        return 0, 0, width, height
    all_pts = np.vstack(polygons)
    x1 = int(max(0, np.min(all_pts[:, 0])))
    y1 = int(max(0, np.min(all_pts[:, 1])))
    x2 = int(min(width, np.max(all_pts[:, 0])))
    y2 = int(min(height, np.max(all_pts[:, 1])))
    if x2 <= x1 or y2 <= y1:
        return 0, 0, width, height
    return x1, y1, x2, y2


def _shift_polygon(poly: np.ndarray, dx: int, dy: int) -> np.ndarray:
    shifted = poly.copy().astype(np.int32)
    shifted[:, 0] -= int(dx)
    shifted[:, 1] -= int(dy)
    return shifted


def build_roi_context(frame: np.ndarray, camera: Camera) -> RoiContext:
    h, w = frame.shape[:2]
    roi_config = get_roi_config(camera)
    include_full: list[np.ndarray] = []
    exclude_full: list[np.ndarray] = []
    keypoint_indexes: set[int] = set()

    if roi_config:
        for roi in roi_config.get("rois") or []:
            if not isinstance(roi, dict) or roi.get("enabled") is False:
                continue
            poly = _norm_points_to_pixels(roi.get("points") or [], w, h)
            if poly is not None:
                include_full.append(poly)
                indexes = roi.get("keypoint_indexes")
                if isinstance(indexes, list):
                    for idx in indexes:
                        try:
                            keypoint_indexes.add(int(idx))
                        except Exception:
                            continue
        for zone in roi_config.get("exclude_zones") or []:
            if not isinstance(zone, dict) or zone.get("enabled") is False:
                continue
            poly = _norm_points_to_pixels(zone.get("points") or [], w, h)
            if poly is not None:
                exclude_full.append(poly)

    if not include_full:
        rect_poly = _points_from_rect_roi(camera.roi if isinstance(camera.roi, list) else None, w, h)
        if rect_poly is not None:
            include_full.append(rect_poly)

    x1, y1, x2, y2 = _polygon_bounds(include_full, w, h)
    include_crop = [_shift_polygon(poly, x1, y1) for poly in include_full]
    exclude_crop = [_shift_polygon(poly, x1, y1) for poly in exclude_full]
    return RoiContext(
        enabled=bool(roi_config or camera.roi),
        bounds=(x1, y1, x2, y2),
        include_polygons=include_crop,
        exclude_polygons=exclude_crop,
        keypoint_indexes=(keypoint_indexes if keypoint_indexes else None),
        roi_config=roi_config,
    )


def crop_and_mask(frame: np.ndarray, camera: Camera) -> tuple[np.ndarray, RoiContext]:
    ctx = build_roi_context(frame, camera)
    x1, y1, x2, y2 = ctx.bounds
    roi_frame = frame[y1:y2, x1:x2].copy()
    if roi_frame.size == 0:
        return frame, RoiContext(False, (0, 0, frame.shape[1], frame.shape[0]), [], [], None, None)

    # 有 polygon 配置时，检测前先把 ROI 外和 exclude 区域遮掉，减少背景误检。
    if ctx.roi_config and ctx.include_polygons:
        mask = np.zeros(roi_frame.shape[:2], dtype=np.uint8)
        cv2.fillPoly(mask, ctx.include_polygons, 255)
        if ctx.exclude_polygons:
            cv2.fillPoly(mask, ctx.exclude_polygons, 0)
        roi_frame = cv2.bitwise_and(roi_frame, roi_frame, mask=mask)
    return roi_frame, ctx


def _point_in_polygons(x: float, y: float, polygons: list[np.ndarray]) -> bool:
    if not polygons:
        return True
    pt = (float(x), float(y))
    return any(cv2.pointPolygonTest(poly.astype(np.float32), pt, False) >= 0 for poly in polygons)


def point_allowed(x: float, y: float, ctx: RoiContext) -> bool:
    if not _point_in_polygons(x, y, ctx.include_polygons):
        return False
    if ctx.exclude_polygons and _point_in_polygons(x, y, ctx.exclude_polygons):
        return False
    return True


def apply_result_filter(result: DetectResult, camera: Camera, ctx: RoiContext) -> DetectResult:
    """检测后执行真正的 ROI polygon / keypoint 过滤。

    - yolo_pose：按 polygon/exclude/keypoint_indexes 过滤关键点；无有效关键点时 target_found=False。
    - yolo/aruco：按 center 是否落入 polygon 过滤。
    - motion：检测前 mask 已经处理，这里不额外过滤。
    """
    if not ctx.enabled or not ctx.roi_config:
        return result

    cfg = camera.detector_config or {}
    roi_filter_mode = str(cfg.get("roi_filter_mode", "filter_keypoints") if isinstance(cfg, dict) else "filter_keypoints")
    metadata = dict(result.metadata or {})
    keypoints = metadata.get("keypoints") or metadata.get("keypoints_full")

    if isinstance(keypoints, list) and keypoints:
        filtered: list = []
        valid_indexes: list[int] = []
        invalid_indexes: list[int] = []
        raw_keypoints = []
        for idx, kp in enumerate(keypoints):
            if not kp or len(kp) < 2:
                filtered.append(kp)
                invalid_indexes.append(idx)
                continue
            x, y = float(kp[0]), float(kp[1])
            conf = float(kp[2]) if len(kp) > 2 else 1.0
            raw_keypoints.append([x, y, conf])
            in_index_scope = ctx.keypoint_indexes is None or idx in ctx.keypoint_indexes
            allowed = in_index_scope and point_allowed(x, y, ctx)
            if allowed and conf > 0:
                filtered.append([x, y, conf])
                valid_indexes.append(idx)
            else:
                # 保留坐标，置信度置 0，前端仍能看到点位但不会参与运动统计。
                filtered.append([x, y, 0.0])
                invalid_indexes.append(idx)

        metadata["keypoints_raw"] = raw_keypoints
        metadata["keypoints"] = filtered
        metadata["keypoints_full"] = filtered
        metadata["valid_keypoints"] = len(valid_indexes)
        metadata["roi_filter"] = {
            "mode": roi_filter_mode,
            "bounds": list(ctx.bounds),
            "keypoint_indexes": sorted(ctx.keypoint_indexes) if ctx.keypoint_indexes else None,
            "valid_keypoint_indexes": valid_indexes,
            "invalid_keypoint_indexes": invalid_indexes,
            "include_polygons": [poly.tolist() for poly in ctx.include_polygons],
            "exclude_polygons": [poly.tolist() for poly in ctx.exclude_polygons],
        }
        new_center = result.center
        if valid_indexes:
            pts = np.asarray([[filtered[i][0], filtered[i][1]] for i in valid_indexes], dtype=np.float32)
            new_center = (float(np.mean(pts[:, 0])), float(np.mean(pts[:, 1])))
        target_found = result.target_found
        message = result.message
        if roi_filter_mode == "filter_keypoints" and not valid_indexes:
            target_found = False
            message = f"{message}; all keypoints filtered by ROI"
        return replace(result, target_found=target_found, center=new_center, metadata=metadata, message=message)

    if result.center is not None and roi_filter_mode in {"filter_center", "filter_keypoints", "strict"}:
        allowed = point_allowed(float(result.center[0]), float(result.center[1]), ctx)
        metadata["roi_filter"] = {"mode": roi_filter_mode, "bounds": list(ctx.bounds), "center_allowed": allowed}
        if not allowed:
            return replace(result, target_found=False, metadata=metadata, message=f"{result.message}; center filtered by ROI")
        return replace(result, metadata=metadata)

    return replace(result, metadata=metadata)
