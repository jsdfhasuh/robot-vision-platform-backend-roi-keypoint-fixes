from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import numpy as np

from app.detectors.base import DetectResult


def _keypoint_xy(kp):
    if not kp or len(kp) < 2:
        return None
    conf = float(kp[2]) if len(kp) > 2 else 1.0
    if conf <= 0:
        return None
    return np.array([float(kp[0]), float(kp[1])], dtype=np.float32)


def _angle_deg(a, b, c) -> float | None:
    pa, pb, pc = _keypoint_xy(a), _keypoint_xy(b), _keypoint_xy(c)
    if pa is None or pb is None or pc is None:
        return None
    v1 = pa - pb
    v2 = pc - pb
    n1 = float(np.linalg.norm(v1))
    n2 = float(np.linalg.norm(v2))
    if n1 < 1e-6 or n2 < 1e-6:
        return None
    cosv = float(np.clip(np.dot(v1, v2) / (n1 * n2), -1.0, 1.0))
    return float(np.degrees(np.arccos(cosv)))


@dataclass
class TrackPoint:
    timestamp: datetime
    target_found: bool
    center: tuple[float, float] | None = None
    keypoints: list | None = None
    motion_distance: float = 0.0
    confidence: float = 0.0
    bbox: tuple[float, float, float, float] | None = None
    message: str = ""


@dataclass
class MotionStats:
    window_seconds: int
    valid_points: int = 0
    total_displacement: float = 0.0
    net_displacement: float = 0.0
    max_step: float = 0.0
    mean_step: float = 0.0
    avg_speed: float = 0.0
    raw_motion_score: float = 0.0
    confidence: float = 0.0
    first_time: datetime | None = None
    last_time: datetime | None = None
    last_center: tuple[float, float] | None = None
    moving: bool = False
    keypoint_mean_step: float = 0.0
    keypoint_max_step: float = 0.0
    angle_change: float = 0.0
    keypoint_deltas: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "window_seconds": self.window_seconds,
            "valid_points": self.valid_points,
            "total_displacement": round(self.total_displacement, 3),
            "net_displacement": round(self.net_displacement, 3),
            "max_step": round(self.max_step, 3),
            "mean_step": round(self.mean_step, 3),
            "avg_speed": round(self.avg_speed, 3),
            "raw_motion_score": round(self.raw_motion_score, 3),
            "confidence": round(self.confidence, 3),
            "first_time": self.first_time.isoformat() if self.first_time else None,
            "last_time": self.last_time.isoformat() if self.last_time else None,
            "last_center": self.last_center,
            "moving": self.moving,
            "keypoint_mean_step": round(self.keypoint_mean_step, 3),
            "keypoint_max_step": round(self.keypoint_max_step, 3),
            "angle_change": round(self.angle_change, 3),
            "keypoint_deltas": self.keypoint_deltas,
            "metadata": self.metadata,
        }


class TrackBuffer:
    """每路摄像头的轻量轨迹缓存。

    目标：不要只看上一帧，而是看最近 N 秒的累计位移、最大步长和平均速度。
    对 yolo/yolo_pose/aruco 使用 center/keypoints；对 motion 使用 motion_distance 原始分数。
    """

    def __init__(self, window_seconds: int = 60, min_step_px: float = 1.5, max_points: int = 600):
        self.window_seconds = int(window_seconds)
        self.min_step_px = float(min_step_px)
        self.max_points = int(max_points)
        self.points: list[TrackPoint] = []

    def clear(self) -> None:
        self.points.clear()

    def add_result(self, result: DetectResult, now: datetime | None = None) -> MotionStats:
        now = now or datetime.utcnow()
        point = TrackPoint(
            timestamp=now,
            target_found=result.target_found,
            center=result.center,
            keypoints=(result.metadata or {}).get("keypoints"),
            motion_distance=float(result.motion_distance or 0.0),
            confidence=float(result.confidence or 0.0),
            bbox=result.bbox,
            message=result.message or "",
        )
        self.points.append(point)
        self._trim(now)
        return self.stats()

    def _trim(self, now: datetime) -> None:
        cutoff = now - timedelta(seconds=self.window_seconds)
        self.points = [p for p in self.points if p.timestamp >= cutoff]
        if len(self.points) > self.max_points:
            self.points = self.points[-self.max_points :]

    def stats(self) -> MotionStats:
        valid = [p for p in self.points if p.target_found]
        stats = MotionStats(window_seconds=self.window_seconds, valid_points=len(valid))
        if not valid:
            return stats

        stats.first_time = valid[0].timestamp
        stats.last_time = valid[-1].timestamp
        stats.confidence = float(np.mean([p.confidence for p in valid]))
        stats.raw_motion_score = float(np.mean([p.motion_distance for p in valid]))
        stats.last_center = valid[-1].center

        centers = [p.center for p in valid if p.center is not None]
        if len(centers) >= 2:
            steps = []
            for a, b in zip(centers, centers[1:]):
                step = float(np.linalg.norm(np.array(b, dtype=np.float32) - np.array(a, dtype=np.float32)))
                # 小抖动过滤，避免检测框轻微漂移导致误判 RUNNING。
                if step >= self.min_step_px:
                    steps.append(step)
                else:
                    steps.append(0.0)
            stats.total_displacement = float(np.sum(steps))
            stats.max_step = float(np.max(steps)) if steps else 0.0
            stats.mean_step = float(np.mean(steps)) if steps else 0.0
            stats.net_displacement = float(np.linalg.norm(np.array(centers[-1]) - np.array(centers[0])))
            duration = max((valid[-1].timestamp - valid[0].timestamp).total_seconds(), 1e-6)
            stats.avg_speed = stats.total_displacement / duration
        else:
            # motion detector 没有 center，就保留原始运动分数。
            stats.total_displacement = stats.raw_motion_score
            stats.max_step = stats.raw_motion_score
            stats.mean_step = stats.raw_motion_score
            stats.avg_speed = stats.raw_motion_score

        # 关键点运动统计：用于 YOLO Pose。
        # 除了整体均值/最大值，还输出逐关键点 delta，给前端关节点调试页面使用。
        kp_steps = []
        kp_max_steps = []
        angle_deltas = []
        keypoint_frames = [p.keypoints for p in valid if p.keypoints]
        per_index_steps: dict[int, list[float]] = {}
        latest_pair_steps: dict[int, float] = {}
        latest_valid_indexes: set[int] = set()
        if len(keypoint_frames) >= 2:
            for a_frame, b_frame in zip(keypoint_frames, keypoint_frames[1:]):
                frame_steps = []
                for idx, (a, b) in enumerate(zip(a_frame, b_frame)):
                    pa, pb = _keypoint_xy(a), _keypoint_xy(b)
                    if pa is None or pb is None:
                        continue
                    step_raw = float(np.linalg.norm(pb - pa))
                    step = 0.0 if step_raw < self.min_step_px else step_raw
                    frame_steps.append(step)
                    per_index_steps.setdefault(idx, []).append(step)
                    latest_pair_steps[idx] = step
                    latest_valid_indexes.add(idx)
                if frame_steps:
                    kp_steps.append(float(np.mean(frame_steps)))
                    kp_max_steps.append(float(np.max(frame_steps)))

                # 按连续三点估算关节角变化，例如 P1-P2-P3、P2-P3-P4。
                angles_a = [_angle_deg(a_frame[i], a_frame[i + 1], a_frame[i + 2]) for i in range(max(len(a_frame) - 2, 0))]
                angles_b = [_angle_deg(b_frame[i], b_frame[i + 1], b_frame[i + 2]) for i in range(max(len(b_frame) - 2, 0))]
                for aa, bb in zip(angles_a, angles_b):
                    if aa is not None and bb is not None:
                        angle_deltas.append(abs(float(bb) - float(aa)))

        if kp_steps:
            stats.keypoint_mean_step = float(np.mean(kp_steps))
            stats.keypoint_max_step = float(np.max(kp_max_steps)) if kp_max_steps else 0.0
        if angle_deltas:
            stats.angle_change = float(np.mean(angle_deltas))

        keypoint_deltas = []
        last_kps = keypoint_frames[-1] if keypoint_frames else []
        for idx, kp in enumerate(last_kps or []):
            conf = float(kp[2]) if kp and len(kp) > 2 else 0.0
            delta = float(latest_pair_steps.get(idx, 0.0))
            avg_delta = float(np.mean(per_index_steps.get(idx, [0.0])))
            keypoint_deltas.append({
                "index": idx,
                "delta_px": round(delta, 3),
                "avg_delta_px": round(avg_delta, 3),
                "moving_by_min_step": bool(delta >= self.min_step_px),
                "valid": bool(idx in latest_valid_indexes and conf > 0),
                "confidence": round(conf, 4),
            })
        stats.keypoint_deltas = keypoint_deltas

        trajectory = []
        for p in valid[-120:]:
            if p.center is not None:
                trajectory.append([float(p.center[0]), float(p.center[1])])

        stats.metadata = {
            "buffer_size": len(self.points),
            "min_step_px": self.min_step_px,
            "has_center": bool(centers),
            "trajectory": trajectory,
            "keypoint_frames": len(keypoint_frames),
        }
        return stats
