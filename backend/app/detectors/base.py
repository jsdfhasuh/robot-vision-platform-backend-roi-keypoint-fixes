from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class DetectResult:
    """统一检测输出。

    Worker 和规则引擎只依赖这个结构，不关心底层是帧差、ArUco、YOLO 还是 Keypoint。
    motion_distance 的单位由检测器决定：
    - motion: 归一化后的运动分数
    - aruco/yolo/keypoint: 目标中心点或关键点的像素位移
    """

    target_found: bool
    motion_distance: float
    confidence: float = 0.0
    message: str = ""
    center: tuple[float, float] | None = None
    bbox: tuple[float, float, float, float] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseDetector(ABC):
    detector_type: str = "base"

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}

    @abstractmethod
    def detect(self, frame: np.ndarray) -> DetectResult:
        raise NotImplementedError
