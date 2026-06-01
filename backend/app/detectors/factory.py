from __future__ import annotations

from app.detectors.aruco_detector import ArucoDetector
from app.detectors.base import BaseDetector
from app.detectors.motion_detector import MotionDetector
from app.detectors.yolo_detector import YoloDetector
from app.detectors.yolo_pose_detector import YoloPoseDetector
from app.core.logging import get_logger

logger = get_logger(__name__)


DETECTOR_REGISTRY = {
    "motion": MotionDetector,
    "aruco": ArucoDetector,
    "yolo": YoloDetector,
    "yolo_pose": YoloPoseDetector,
}


def create_detector(detector_type: str | None, config: dict | None = None) -> BaseDetector:
    key = (detector_type or "motion").lower().strip()
    cls = DETECTOR_REGISTRY.get(key)
    if cls is None:
        logger.error("unsupported detector_type=%s", detector_type)
        raise ValueError(f"unsupported detector_type: {detector_type}")
    logger.info("detector factory create type=%s", key)
    return cls(config=config or {})


def list_detectors() -> list[dict]:
    return [
        {
            "type": "motion",
            "name": "ROI 运动检测",
            "need_model": False,
            "best_for": "第一版快速上线，不需要训练模型",
            "config_example": {"diff_threshold": 25, "min_area": 80, "blur_size": 5},
        },
        {
            "type": "aruco",
            "name": "ArUco 标记检测",
            "need_model": False,
            "best_for": "现场可贴标记，稳定跟踪末端位移",
            "config_example": {"dictionary": "DICT_4X4_50", "target_id": 1},
        },
        {
            "type": "yolo",
            "name": "YOLO11 / YOLO26 ONNX 目标检测",
            "need_model": True,
            "best_for": "不能贴标记，需要识别机器人/夹具/末端执行器",
            "config_example": {"model_path": "/app/models/end_effector.onnx", "model_family": "auto", "input_size": 640, "target_class": None, "conf_threshold": 0.35, "iou_threshold": 0.45, "providers": ["CPUExecutionProvider"]},
        },
        {
            "type": "yolo_pose",
            "name": "YOLO11 Pose ONNX 关键点检测",
            "need_model": True,
            "best_for": "需要根据机器人关节/末端关键点姿态变化判断是否运行",
            "config_example": {"model_path": "/app/models/robot_pose.onnx", "model_family": "yolo11_pose", "input_size": 640, "num_keypoints": 6, "class_count": 1, "target_class": None, "conf_threshold": 0.35, "iou_threshold": 0.45, "keypoint_conf_threshold": 0.25, "target_keypoints": [2, 3, 4, 5], "motion_mode": "mean", "providers": ["CPUExecutionProvider"]},
        },
    ]
