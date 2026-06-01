from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any

from app.detectors.base import DetectResult
from app.tracker.track_buffer import TrackBuffer, MotionStats


class SimpleTracker:
    """单目标轨迹分析器。

    目前工业机器人场景通常一个 ROI 对应一个机器人，所以先实现单目标版本。
    后续如果一个画面多个机器人，可以把这里替换为 ByteTrack/DeepSORT。
    """

    def __init__(self, config: dict[str, Any] | None = None):
        cfg = config or {}
        self.motion_window_seconds = int(cfg.get("motion_window_seconds", cfg.get("window_seconds", 30)))
        self.min_step_px = float(cfg.get("min_step_px", 1.5))
        self.movement_score = str(cfg.get("movement_score", "total_displacement"))
        self.buffer = TrackBuffer(self.motion_window_seconds, self.min_step_px)
        self.last_stats = MotionStats(window_seconds=self.motion_window_seconds)
        self.last_result: DetectResult | None = None

    def reset(self) -> None:
        self.buffer.clear()
        self.last_stats = MotionStats(window_seconds=self.motion_window_seconds)
        self.last_result = None

    def update(self, result: DetectResult, now: datetime | None = None) -> tuple[DetectResult, MotionStats]:
        stats = self.buffer.add_result(result, now)
        score = self._select_score(result, stats)
        metadata = dict(result.metadata or {})
        metadata["tracker"] = stats.to_dict()
        tracked = replace(
            result,
            motion_distance=float(score),
            message=f"{result.message}; tracker_score={score:.2f}, total={stats.total_displacement:.2f}, speed={stats.avg_speed:.2f}",
            metadata=metadata,
        )
        self.last_stats = stats
        self.last_result = tracked
        return tracked, stats

    def _select_score(self, result: DetectResult, stats: MotionStats) -> float:
        if self.movement_score == "avg_speed":
            return stats.avg_speed
        if self.movement_score == "max_step":
            return stats.max_step
        if self.movement_score == "net_displacement":
            return stats.net_displacement
        if self.movement_score == "keypoint_mean_step":
            return stats.keypoint_mean_step
        if self.movement_score == "keypoint_max_step":
            return stats.keypoint_max_step
        if self.movement_score == "angle_change":
            return stats.angle_change
        if self.movement_score == "raw":
            return result.motion_distance
        return stats.total_displacement
