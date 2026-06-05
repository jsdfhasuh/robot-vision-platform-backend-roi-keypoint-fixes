from __future__ import annotations

from datetime import datetime

from app.core.logging import get_logger
from app.detectors.factory import create_detector
from app.models.camera import Camera
from app.models.detection_task import DetectionRule, DetectionTask
from app.rules.robot_stop_rule import RobotStopRule
from app.tracker.simple_tracker import SimpleTracker

logger = get_logger(__name__)


class RuntimeContext:
    """单摄像头运行时上下文。

    负责 detector / tracker / rule 的创建和热更新。Worker 主循环只调用它。
    config_version 变化时统一重置 tracker/rule，避免前端改 ROI、模型、阈值后旧状态继续污染新配置。
    """

    def __init__(self, camera_id: int):
        self.camera_id = camera_id
        self.detector = None
        self.detector_signature: tuple[str, str] | None = None
        self.tracker: SimpleTracker | None = None
        self.tracker_signature: str | None = None
        self.rule: RobotStopRule | None = None
        self.rule_signature: tuple | None = None
        self.last_config_version: int | None = None
        self.last_rule_version: int | None = None
        self.reset_count = 0
        self.last_reset_at: datetime | None = None
        self.last_reset_reason: str | None = None

    def ensure(self, camera: Camera) -> None:
        self._handle_config_version(camera)
        self._ensure_detector(camera)
        self._ensure_tracker(camera)
        self._ensure_rule(camera)

    def ensure_task(self, task: DetectionTask, rule: DetectionRule) -> None:
        self._handle_task_config_version(task, rule)
        self._ensure_task_detector(task)
        self._ensure_task_tracker(task)
        self._ensure_shared_rule(rule)

    def _handle_config_version(self, camera: Camera) -> None:
        version = int(getattr(camera, "config_version", 1) or 1)
        if self.last_config_version is None:
            self.last_config_version = version
            return
        if version != self.last_config_version:
            logger.info(
                "camera config version changed camera_id=%s old=%s new=%s, reset runtime state",
                self.camera_id,
                self.last_config_version,
                version,
            )
            self.reset_state(reason=f"config_version {self.last_config_version}->{version}")
            self.last_config_version = version

    def _handle_task_config_version(self, task: DetectionTask, rule: DetectionRule) -> None:
        version = int(getattr(task, "config_version", 1) or 1)
        rule_version = int(getattr(rule, "version", 1) or 1)
        if self.last_config_version is None:
            self.last_config_version = version
            self.last_rule_version = rule_version
            return
        if version != self.last_config_version or rule_version != self.last_rule_version:
            logger.info(
                "task runtime config changed task_id=%s camera_id=%s task_version=%s->%s rule_version=%s->%s",
                task.id,
                self.camera_id,
                self.last_config_version,
                version,
                self.last_rule_version,
                rule_version,
            )
            self.reset_state(reason=f"task_config {self.last_config_version}->{version}, rule {self.last_rule_version}->{rule_version}")
            self.last_config_version = version
            self.last_rule_version = rule_version

    def reset_state(self, reason: str = "manual") -> None:
        if self.tracker:
            self.tracker.reset()
        if self.rule:
            self.rule.last_motion_time = None
            self.rule.last_found_time = None
            self.rule.candidates.clear()
        self.reset_count += 1
        self.last_reset_at = datetime.utcnow()
        self.last_reset_reason = reason

    def _ensure_detector(self, camera: Camera) -> None:
        config_str = str(camera.detector_config or {})
        signature = (camera.detector_type or "motion", config_str)
        if self.detector is None or self.detector_signature != signature:
            logger.info("create detector camera_id=%s detector_type=%s config=%s", self.camera_id, signature[0], config_str)
            self.detector = create_detector(camera.detector_type, camera.detector_config or {})
            self.detector_signature = signature
            if self.tracker:
                self.tracker.reset()

    def _ensure_task_detector(self, task: DetectionTask) -> None:
        config_str = str(task.detector_config or {})
        signature = (task.detector_type or "motion", config_str)
        if self.detector is None or self.detector_signature != signature:
            logger.info("create task detector task_id=%s camera_id=%s detector_type=%s config=%s", task.id, self.camera_id, signature[0], config_str)
            self.detector = create_detector(task.detector_type, task.detector_config or {})
            self.detector_signature = signature
            if self.tracker:
                self.tracker.reset()

    def _ensure_tracker(self, camera: Camera) -> None:
        config = camera.detector_config or {}
        tracker_config = config.get("tracker", {}) if isinstance(config, dict) else {}
        signature = str(tracker_config)
        if self.tracker is None or self.tracker_signature != signature:
            logger.info("create/update tracker camera_id=%s config=%s", self.camera_id, tracker_config)
            self.tracker = SimpleTracker(tracker_config)
            self.tracker_signature = signature

    def _ensure_task_tracker(self, task: DetectionTask) -> None:
        tracker_config = task.tracker_config or {}
        signature = str(tracker_config)
        if self.tracker is None or self.tracker_signature != signature:
            logger.info("create/update task tracker task_id=%s camera_id=%s config=%s", task.id, self.camera_id, tracker_config)
            self.tracker = SimpleTracker(tracker_config)
            self.tracker_signature = signature

    def _ensure_rule(self, camera: Camera) -> None:
        config = camera.detector_config or {}
        rule_config = config.get("rule", {}) if isinstance(config, dict) else {}
        signature = (float(camera.motion_threshold), int(camera.stop_seconds), str(rule_config))
        if self.rule is None or self.rule_signature != signature:
            logger.info(
                "create/update rule camera_id=%s threshold=%s stop_seconds=%s config=%s",
                self.camera_id,
                camera.motion_threshold,
                camera.stop_seconds,
                rule_config,
            )
            self.rule = RobotStopRule(camera.motion_threshold, camera.stop_seconds, rule_config)
            self.rule_signature = signature
            return
        self.rule.motion_threshold = camera.motion_threshold
        self.rule.stop_seconds = camera.stop_seconds

    def _ensure_shared_rule(self, rule: DetectionRule) -> None:
        config = rule.rule_config or {}
        motion_threshold = float(config.get("motion_threshold", 5.0))
        stop_seconds = int(config.get("stop_seconds", 30))
        rule_config = {
            "unknown_seconds": int(config.get("unknown_seconds", 10)),
            "confirm_frames": int(config.get("confirm_frames", 2)),
            "status_hold_seconds": float(config.get("status_hold_seconds", 1.0)),
        }
        signature = (motion_threshold, stop_seconds, str(rule_config), int(rule.id), int(rule.version or 1))
        if self.rule is None or self.rule_signature != signature:
            logger.info(
                "create/update shared rule camera_id=%s rule_id=%s version=%s threshold=%s stop_seconds=%s config=%s",
                self.camera_id,
                rule.id,
                rule.version,
                motion_threshold,
                stop_seconds,
                rule_config,
            )
            self.rule = RobotStopRule(motion_threshold, stop_seconds, rule_config)
            self.rule_signature = signature
            return
        self.rule.motion_threshold = motion_threshold
        self.rule.stop_seconds = stop_seconds

    def run_detector(self, roi_frame):
        return self.detector.detect(roi_frame)

    def update_tracker(self, raw_result):
        if self.tracker:
            return self.tracker.update(raw_result)
        return raw_result, None

    def update_rule(self, result):
        return self.rule.update(result)

    @property
    def rule_detail(self):
        return self.rule.last_detail if self.rule else None

    def health(self) -> dict:
        return {
            "detector_signature": self.detector_signature,
            "tracker_signature": self.tracker_signature,
            "rule_signature": self.rule_signature,
            "last_config_version": self.last_config_version,
            "last_rule_version": self.last_rule_version,
            "reset_count": self.reset_count,
            "last_reset_at": self.last_reset_at,
            "last_reset_reason": self.last_reset_reason,
            "rule_detail": self.rule_detail,
        }
