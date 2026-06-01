from __future__ import annotations

from datetime import datetime
from typing import Any

import cv2
import numpy as np

from app.detectors.base import DetectResult


STATUS_COLOR = {
    "RUNNING": (0, 180, 0),
    "IDLE": (180, 180, 0),
    "STOPPED": (0, 0, 255),
    "OFFLINE": (120, 120, 120),
    "UNKNOWN": (0, 165, 255),
    "DEBUG": (255, 255, 255),
    "IMAGE_DEBUG": (255, 255, 255),
    "PAIR_DEBUG": (255, 255, 255),
}


def _draw_text(img, text: str, x: int, y: int, scale: float = 0.56):
    cv2.putText(img, text[:160], (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 4)
    cv2.putText(img, text[:160], (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), 1)


def _fmt(value: Any, default: str = "-") -> str:
    if value is None:
        return default
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def draw_detection(
    frame: np.ndarray,
    result: DetectResult,
    status: str | None = None,
    *,
    rule_detail: dict[str, Any] | None = None,
    camera_name: str | None = None,
    detector_type: str | None = None,
    roi: list[int] | None = None,
    event_type: str | None = None,
) -> np.ndarray:
    """绘制诊断标注图。

    这张图用于内网调试和事件复盘，不只画检测框，还会展示：
    - bbox / center / keypoints / skeleton
    - 最近轨迹
    - tracker 运动统计
    - 规则层解释信息
    """
    img = frame.copy()
    h, w = img.shape[:2]
    color = STATUS_COLOR.get(status or "", (255, 255, 255))

    if result.bbox:
        x1, y1, x2, y2 = [int(v) for v in result.bbox]
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
        _draw_text(img, f"bbox conf={result.confidence:.2f}", x1, max(18, y1 - 8), 0.5)

    tracker_meta = ((result.metadata or {}).get("tracker") or {}).get("metadata") or {}
    trajectory = tracker_meta.get("trajectory") or []
    if len(trajectory) >= 2:
        pts = [(int(x), int(y)) for x, y in trajectory if x is not None and y is not None]
        for idx, (a, b) in enumerate(zip(pts, pts[1:])):
            thickness = 1 if idx < max(len(pts) - 10, 0) else 2
            cv2.line(img, a, b, (0, 255, 255), thickness)
        if pts:
            cv2.circle(img, pts[-1], 5, (0, 255, 255), -1)
            _draw_text(img, "trail", pts[-1][0] + 8, pts[-1][1] + 8, 0.45)

    if result.center:
        cx, cy = [int(v) for v in result.center]
        cv2.circle(img, (cx, cy), 6, (0, 0, 255), -1)
        _draw_text(img, f"C({cx},{cy})", cx + 8, cy - 8, 0.45)

    keypoints = (result.metadata or {}).get("keypoints") or (result.metadata or {}).get("keypoints_full")
    if keypoints:
        valid_pts = []
        for idx, kp in enumerate(keypoints):
            if not kp or len(kp) < 2:
                valid_pts.append(None)
                continue
            x, y = int(kp[0]), int(kp[1])
            conf = float(kp[2]) if len(kp) > 2 else 1.0
            if conf <= 0:
                valid_pts.append(None)
                continue
            valid_pts.append((x, y))
            cv2.circle(img, (x, y), 4, (255, 0, 0), -1)
            _draw_text(img, str(idx), x + 4, y - 4, 0.42)
        # 默认按关键点顺序连接；后续可以从 detector_config.skeleton 自定义。
        for a, b in zip(valid_pts, valid_pts[1:]):
            if a is not None and b is not None:
                cv2.line(img, a, b, (255, 160, 0), 2)

    tracker = (result.metadata or {}).get("tracker") or {}
    rule_detail = rule_detail or (result.metadata or {}).get("rule_detail") or {}
    reason = rule_detail.get("reason") or rule_detail.get("reason_code") or ""

    # 顶部半透明面板
    panel_h = min(245, max(150, int(h * 0.28)))
    overlay = img.copy()
    cv2.rectangle(overlay, (0, 0), (min(w, 720), panel_h), (0, 0, 0), -1)
    img = cv2.addWeighted(overlay, 0.35, img, 0.65, 0)

    lines = [
        f"camera: {camera_name or '-'} detector: {detector_type or '-'}",
        f"time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} status: {status or '-'} event: {event_type or '-'}",
        f"found: {result.target_found} conf: {result.confidence:.2f} motion: {result.motion_distance:.2f}",
        f"total: {_fmt(tracker.get('total_displacement'))} speed: {_fmt(tracker.get('avg_speed'))} max_step: {_fmt(tracker.get('max_step'))}",
        f"kp_mean: {_fmt(tracker.get('keypoint_mean_step'))} kp_max: {_fmt(tracker.get('keypoint_max_step'))} angle: {_fmt(tracker.get('angle_change'))}",
        f"threshold: {_fmt(rule_detail.get('motion_threshold'))} stop_sec: {_fmt(rule_detail.get('stop_seconds'))} idle: {_fmt(rule_detail.get('idle_seconds'))}",
        f"reason: {reason}",
    ]
    if roi:
        lines.insert(2, f"roi: {roi}")

    y = 24
    for line in lines:
        _draw_text(img, line, 10, y, 0.52)
        y += 23

    return img
