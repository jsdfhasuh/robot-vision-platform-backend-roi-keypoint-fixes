from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from typing import Any

from app.detectors.base import DetectResult


class RobotStopRule:
    """机器人停机判断规则。

    输入的 motion_distance 已经可以是检测器原始位移，也可以是 tracker 统计后的窗口累计位移。
    规则层负责状态防抖、连续帧确认和停机超时判断，并输出可解释的 rule_detail。
    """

    def __init__(self, motion_threshold: float = 5.0, stop_seconds: int = 30, config: dict[str, Any] | None = None):
        cfg = config or {}
        self.motion_threshold = float(motion_threshold)
        self.stop_seconds = int(stop_seconds)
        self.unknown_seconds = int(cfg.get("unknown_seconds", 10))
        self.confirm_frames = int(cfg.get("confirm_frames", 2))
        self.status_hold_seconds = float(cfg.get("status_hold_seconds", 1.0))
        self.last_motion_time: datetime | None = None
        self.last_found_time: datetime | None = None
        self.current_status: str = "UNKNOWN"
        self.last_status_change_time: datetime | None = None
        self.candidates: deque[str] = deque(maxlen=max(self.confirm_frames, 1))
        self.last_raw_status: str = "UNKNOWN"
        self.last_detail: dict[str, Any] = {}

    def update(self, result: DetectResult) -> tuple[str, datetime | None, str]:
        now = datetime.utcnow()
        raw_status, message, raw_detail = self._raw_status(result, now)
        status = self._debounce(raw_status, now)
        self.last_raw_status = raw_status
        self.last_detail = self._build_detail(result, now, raw_status, status, message, raw_detail)
        return status, self.last_motion_time, message

    def _raw_status(self, result: DetectResult, now: datetime) -> tuple[str, str, dict[str, Any]]:
        tracker = (result.metadata or {}).get("tracker") or {}
        if not result.target_found:
            if self.last_found_time and now - self.last_found_time <= timedelta(seconds=self.unknown_seconds):
                fallback = self.current_status if self.current_status != "UNKNOWN" else "IDLE"
                return fallback, f"temporary target lost: {result.message}", {
                    "reason_code": "TEMPORARY_TARGET_LOST",
                    "target_found": False,
                    "unknown_grace_seconds": self.unknown_seconds,
                }
            return "UNKNOWN", result.message or "target not found", {
                "reason_code": "TARGET_NOT_FOUND",
                "target_found": False,
                "unknown_grace_seconds": self.unknown_seconds,
            }

        self.last_found_time = now
        if result.motion_distance > self.motion_threshold:
            self.last_motion_time = now
            return "RUNNING", result.message, {
                "reason_code": "MOTION_OVER_THRESHOLD",
                "motion_distance": float(result.motion_distance),
                "motion_threshold": self.motion_threshold,
                "tracker": tracker,
            }

        if self.last_motion_time is None:
            self.last_motion_time = now
            return "IDLE", result.message, {
                "reason_code": "FIRST_VALID_FRAME",
                "motion_distance": float(result.motion_distance),
                "motion_threshold": self.motion_threshold,
            }

        idle_seconds = (now - self.last_motion_time).total_seconds()
        if idle_seconds > self.stop_seconds:
            return "STOPPED", result.message, {
                "reason_code": "NO_MOTION_TIMEOUT",
                "idle_seconds": round(idle_seconds, 3),
                "stop_seconds": self.stop_seconds,
                "motion_distance": float(result.motion_distance),
                "motion_threshold": self.motion_threshold,
                "tracker": tracker,
            }

        return "IDLE", result.message, {
            "reason_code": "MOTION_UNDER_THRESHOLD_WITHIN_TIMEOUT",
            "idle_seconds": round(idle_seconds, 3),
            "stop_seconds": self.stop_seconds,
            "motion_distance": float(result.motion_distance),
            "motion_threshold": self.motion_threshold,
            "tracker": tracker,
        }

    def _debounce(self, raw_status: str, now: datetime) -> str:
        self.candidates.append(raw_status)
        if self.confirm_frames <= 1:
            candidate_ok = True
        else:
            candidate_ok = len(self.candidates) >= self.confirm_frames and all(s == raw_status for s in self.candidates)

        if not candidate_ok:
            return self.current_status

        if raw_status == self.current_status:
            return self.current_status

        if self.last_status_change_time is not None:
            elapsed = (now - self.last_status_change_time).total_seconds()
            if elapsed < self.status_hold_seconds:
                return self.current_status

        self.current_status = raw_status
        self.last_status_change_time = now
        return self.current_status

    def _build_detail(self, result: DetectResult, now: datetime, raw_status: str, final_status: str, message: str, raw_detail: dict[str, Any]) -> dict[str, Any]:
        idle_seconds = None
        if self.last_motion_time is not None:
            idle_seconds = round((now - self.last_motion_time).total_seconds(), 3)
        return {
            "final_status": final_status,
            "raw_status": raw_status,
            "reason_code": raw_detail.get("reason_code", ""),
            "reason": self.explain(final_status, raw_detail),
            "message": message,
            "motion_distance": round(float(result.motion_distance or 0.0), 3),
            "motion_threshold": self.motion_threshold,
            "stop_seconds": self.stop_seconds,
            "idle_seconds": idle_seconds,
            "target_found": bool(result.target_found),
            "confidence": round(float(result.confidence or 0.0), 3),
            "confirm_frames": self.confirm_frames,
            "status_hold_seconds": self.status_hold_seconds,
            "raw_detail": raw_detail,
        }

    def explain(self, status: str, detail: dict[str, Any] | None = None) -> str:
        detail = detail or self.last_detail or {}
        code = detail.get("reason_code")
        if status == "RUNNING":
            return f"最近窗口运动量 {detail.get('motion_distance', 0):.2f} > 阈值 {self.motion_threshold:.2f}，判定运行。"
        if status == "STOPPED":
            return f"最近 {detail.get('idle_seconds', 0)} 秒运动量未超过阈值，超过停机阈值 {self.stop_seconds} 秒，判定疑似停机。"
        if status == "IDLE":
            return f"检测到目标，但运动量未超过阈值；未达到停机超时，判定静止/待机。"
        if status == "OFFLINE":
            return "RTSP 视频流读取失败，判定离线。"
        if status == "UNKNOWN":
            if code == "TARGET_NOT_FOUND":
                return "未检测到目标或关键点，暂时无法判断运行状态。"
            return "检测结果不确定。"
        return "状态由规则层计算得到。"
