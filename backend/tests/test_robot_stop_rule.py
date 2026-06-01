from app.detectors.base import DetectResult
from app.rules.robot_stop_rule import RobotStopRule


def test_robot_stop_rule_running_when_motion_over_threshold():
    rule = RobotStopRule(motion_threshold=5, stop_seconds=30, config={"confirm_frames": 1, "status_hold_seconds": 0})
    status, _, _ = rule.update(DetectResult(target_found=True, motion_distance=8, confidence=0.9, message="moving"))
    assert status == "RUNNING"
    assert rule.last_detail["reason_code"] == "MOTION_OVER_THRESHOLD"


def test_robot_stop_rule_unknown_when_target_not_found():
    rule = RobotStopRule(motion_threshold=5, stop_seconds=30, config={"confirm_frames": 1, "status_hold_seconds": 0})
    status, _, _ = rule.update(DetectResult(target_found=False, motion_distance=0, confidence=0, message="not found"))
    assert status == "UNKNOWN"
    assert rule.last_detail["reason_code"] == "TARGET_NOT_FOUND"
