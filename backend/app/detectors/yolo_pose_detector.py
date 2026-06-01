from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from app.core.logging import get_logger
from app.detectors.base import DetectResult
from app.detectors.yolo_detector import YoloDetector

logger = get_logger(__name__)


class YoloPoseDetector(YoloDetector):
    """YOLO11 Pose / YOLO Pose ONNX 关键点检测器。

    这个检测器用于“机器人是否运动”的 Pose 方案：
    - ONNX 推理得到 bbox + keypoints
    - 选择置信度最高的机器人/部件
    - 计算关键点在相邻帧之间的位移
    - 输出 DetectResult.motion_distance 给规则层判断 RUNNING / IDLE / STOPPED

    支持两类常见输出：
    1. Ultralytics YOLO11/YOLOv8 Pose raw 输出，例如 (1, 56, 8400) 或 (1, 8400, 56)
       其中 56 = 4 + 1 class + 17 * 3 keypoints。
    2. 已 NMS/end2end 的 Pose 输出，例如 (1, N, 6 + K*3) 或 (1, N, 7 + K*3)
       行格式为 [x1,y1,x2,y2,score,class,kpts...] 或 [batch,x1,y1,x2,y2,score,class,kpts...]。

    推荐配置：
    {
      "model_path": "/app/models/robot_pose.onnx",
      "model_family": "yolo11_pose",
      "input_size": 640,
      "num_keypoints": 6,
      "class_count": 1,
      "target_class": null,
      "conf_threshold": 0.35,
      "iou_threshold": 0.45,
      "keypoint_conf_threshold": 0.25,
      "target_keypoints": [2, 3, 4, 5],
      "motion_mode": "mean",       # mean / max / centroid
      "has_objectness": false,
      "providers": ["CPUExecutionProvider"]
    }
    """

    detector_type = "yolo_pose"

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self.num_keypoints = self._get_optional_int("num_keypoints")
        self.class_count = self._get_optional_int("class_count")
        self.keypoint_conf_threshold = float(self.config.get("keypoint_conf_threshold", 0.25))
        self.target_keypoints = self.config.get("target_keypoints")
        if self.target_keypoints is not None:
            self.target_keypoints = [int(i) for i in self.target_keypoints]
        self.motion_mode = str(self.config.get("motion_mode", "mean")).lower().strip()
        self.has_objectness = bool(self.config.get("has_objectness", False))
        self.prev_keypoints: list[tuple[float, float, float]] | None = None
        logger.info(
            "yolo pose detector initialized model_path=%s num_keypoints=%s class_count=%s target_keypoints=%s motion_mode=%s kpt_conf=%s",
            self.model_path,
            self.num_keypoints,
            self.class_count,
            self.target_keypoints,
            self.motion_mode,
            self.keypoint_conf_threshold,
        )

    def _get_optional_int(self, name: str) -> int | None:
        value = self.config.get(name)
        if value is None or value == "":
            return None
        return int(value)

    def detect(self, frame: np.ndarray) -> DetectResult:
        if frame is None or frame.size == 0:
            return DetectResult(False, 0.0, 0.0, "empty frame")
        if self.session is None or self.input_name is None:
            return DetectResult(False, 0.0, 0.0, f"YOLO Pose ONNX not ready: {self.load_error or 'unknown error'}")

        try:
            blob, ratio, pad = self._preprocess(frame)
            outputs = self.session.run(self.output_names, {self.input_name: blob})
            detections = self._postprocess_pose(outputs, frame.shape[:2], ratio, pad)
        except Exception as exc:
            logger.exception("yolo pose onnx detect failed model_path=%s", self.model_path)
            return DetectResult(False, 0.0, 0.0, f"YOLO Pose ONNX detect failed: {exc}")

        if not detections:
            return DetectResult(False, 0.0, 0.0, f"pose target not found mode={self.output_mode}")

        best = sorted(detections, key=lambda x: (x["confidence"], x["valid_keypoints"], x["area"]), reverse=True)[0]
        keypoints = best["keypoints"]
        bbox = best["bbox"]
        center = self._keypoint_centroid(keypoints)
        if center is None:
            x1, y1, x2, y2 = bbox
            center = ((x1 + x2) / 2.0, (y1 + y2) / 2.0)

        distance = self._motion_distance(keypoints, center)
        self.prev_keypoints = keypoints
        self.prev_center = center

        return DetectResult(
            target_found=True,
            motion_distance=distance,
            confidence=float(best["confidence"]),
            message=(
                f"{self.output_mode}_class={best['class_id']}, "
                f"conf={best['confidence']:.2f}, valid_kpts={best['valid_keypoints']}, "
                f"kpt_displacement={distance:.2f}px"
            ),
            center=center,
            bbox=bbox,
            metadata={
                "class_id": best["class_id"],
                "area": best["area"],
                "model_path": self.model_path,
                "backend": "onnxruntime",
                "model_family": self.model_family,
                "output_mode": self.output_mode,
                "detections": len(detections),
                "num_keypoints": len(keypoints),
                "valid_keypoints": best["valid_keypoints"],
                "keypoints": keypoints,
                "motion_mode": self.motion_mode,
                "target_keypoints": self.target_keypoints,
            },
        )

    def _postprocess_pose(
        self,
        outputs: list[np.ndarray],
        original_shape: tuple[int, int],
        ratio: float,
        pad: tuple[float, float],
    ) -> list[dict[str, Any]]:
        pred = np.asarray(outputs[0])
        if pred.ndim == 3 and pred.shape[0] == 1:
            pred = pred[0]
        elif pred.ndim > 3:
            pred = np.squeeze(pred)
        if pred.ndim == 1:
            pred = pred.reshape(1, -1)

        if pred.ndim == 2 and pred.shape[0] < pred.shape[1] and pred.shape[0] <= 256:
            pred = pred.T

        if pred.ndim != 2 or pred.shape[1] < 8:
            logger.warning("unsupported yolo pose output shape=%s", pred.shape)
            return []

        # 如果是 end2end/NMS 输出，通常列数为 6 + K*3 或 7 + K*3，且坐标为 xyxy。
        if self._looks_like_pose_end2end(pred):
            return self._postprocess_pose_end2end(pred, original_shape, ratio, pad)
        return self._postprocess_pose_raw(pred, original_shape, ratio, pad)

    def _looks_like_pose_end2end(self, pred: np.ndarray) -> bool:
        if pred.ndim != 2 or pred.shape[0] > 1000:
            return False
        if self.num_keypoints is None:
            # 无法明确知道 K 时，不贸然把 raw 输出当 end2end。
            return False
        cols = pred.shape[1]
        expected_6 = 6 + self.num_keypoints * 3
        expected_7 = 7 + self.num_keypoints * 3
        if cols not in (expected_6, expected_7):
            return False
        rows = pred[:, -expected_6:] if cols == expected_7 else pred[:, :expected_6]
        if rows.size == 0:
            return False
        sample = rows[: min(len(rows), 20)].astype(np.float32)
        x1, y1, x2, y2, conf = sample[:, 0], sample[:, 1], sample[:, 2], sample[:, 3], sample[:, 4]
        xyxy_rate = float(np.mean((x2 >= x1) & (y2 >= y1)))
        conf_rate = float(np.mean((conf >= 0.0) & (conf <= 1.0)))
        return xyxy_rate > 0.8 and conf_rate > 0.8

    def _postprocess_pose_end2end(
        self,
        pred: np.ndarray,
        original_shape: tuple[int, int],
        ratio: float,
        pad: tuple[float, float],
    ) -> list[dict[str, Any]]:
        self.output_mode = "yolo_pose_end2end"
        detections: list[dict[str, Any]] = []
        k = int(self.num_keypoints or 0)
        expected_6 = 6 + k * 3
        for row in pred.astype(np.float32):
            values = row[-expected_6:] if pred.shape[1] == expected_6 + 1 else row[:expected_6]
            x1, y1, x2, y2, score, class_id = values[:6]
            keypoints = self._decode_keypoints(values[6:], k, original_shape, ratio, pad)
            self._append_pose_detection(
                detections,
                [x1, y1, x2, y2],
                float(score),
                int(round(float(class_id))),
                keypoints,
                original_shape,
                ratio,
                pad,
                already_xyxy=True,
            )
        return detections

    def _postprocess_pose_raw(
        self,
        pred: np.ndarray,
        original_shape: tuple[int, int],
        ratio: float,
        pad: tuple[float, float],
    ) -> list[dict[str, Any]]:
        self.output_mode = "yolo_pose_raw"
        detections: list[dict[str, Any]] = []
        cols = pred.shape[1]

        k = self.num_keypoints
        if k is None:
            # Ultralytics Pose 通常为 4 + class_count + K*3。
            # 不传 num_keypoints 时，优先按 class_count 推断。
            cc = self.class_count or 1
            remain = cols - 4 - cc - (1 if self.has_objectness else 0)
            if remain > 0 and remain % 3 == 0:
                k = remain // 3
                logger.warning("num_keypoints not configured, inferred num_keypoints=%s from output_cols=%s", k, cols)
            else:
                logger.error("cannot infer num_keypoints from pose output shape=%s, please set detector_config.num_keypoints", pred.shape)
                return []

        keypoint_dims = int(k) * 3
        if cols <= 4 + keypoint_dims:
            logger.warning("unsupported yolo pose raw cols=%s num_keypoints=%s", cols, k)
            return []

        if self.class_count is None:
            class_count = cols - 4 - keypoint_dims - (1 if self.has_objectness else 0)
            class_count = max(1, int(class_count))
        else:
            class_count = int(self.class_count)

        for row in pred.astype(np.float32):
            cx, cy, bw, bh = row[:4]
            cursor = 4
            if self.has_objectness:
                obj_conf = float(row[cursor])
                cursor += 1
                cls_scores = row[cursor : cursor + class_count]
                class_id = int(np.argmax(cls_scores)) if cls_scores.size else 0
                score = obj_conf * (float(cls_scores[class_id]) if cls_scores.size else 1.0)
            else:
                cls_scores = row[cursor : cursor + class_count]
                class_id = int(np.argmax(cls_scores)) if cls_scores.size else 0
                score = float(cls_scores[class_id]) if cls_scores.size else 0.0
            cursor += class_count

            kpt_flat = row[cursor : cursor + keypoint_dims]
            if kpt_flat.size != keypoint_dims:
                continue
            x1 = cx - bw / 2
            y1 = cy - bh / 2
            x2 = cx + bw / 2
            y2 = cy + bh / 2
            keypoints = self._decode_keypoints(kpt_flat, int(k), original_shape, ratio, pad)
            self._append_pose_detection(
                detections,
                [x1, y1, x2, y2],
                score,
                class_id,
                keypoints,
                original_shape,
                ratio,
                pad,
                already_xyxy=True,
            )

        return self._nms(detections)

    def _decode_keypoints(
        self,
        flat: np.ndarray,
        num_keypoints: int,
        original_shape: tuple[int, int],
        ratio: float,
        pad: tuple[float, float],
    ) -> list[tuple[float, float, float]]:
        h, w = original_shape
        pad_x, pad_y = pad
        keypoints: list[tuple[float, float, float]] = []
        arr = np.asarray(flat, dtype=np.float32).reshape(num_keypoints, 3)
        for x, y, conf in arr:
            sx = (float(x) - pad_x) / ratio
            sy = (float(y) - pad_y) / ratio
            sx = min(max(0.0, sx), float(w - 1))
            sy = min(max(0.0, sy), float(h - 1))
            keypoints.append((sx, sy, float(conf)))
        return keypoints

    def _append_pose_detection(
        self,
        detections: list[dict[str, Any]],
        xyxy: list[float] | np.ndarray,
        score: float,
        class_id: int,
        keypoints: list[tuple[float, float, float]],
        original_shape: tuple[int, int],
        ratio: float,
        pad: tuple[float, float],
        already_xyxy: bool = True,
    ) -> None:
        if not np.isfinite(score) or score < self.conf_threshold:
            return
        if self.target_class is not None and class_id != int(self.target_class):
            return

        h, w = original_shape
        pad_x, pad_y = pad
        x1, y1, x2, y2 = [float(v) for v in xyxy]
        if already_xyxy:
            x1 = (x1 - pad_x) / ratio
            y1 = (y1 - pad_y) / ratio
            x2 = (x2 - pad_x) / ratio
            y2 = (y2 - pad_y) / ratio
        x1, y1 = max(0.0, x1), max(0.0, y1)
        x2, y2 = min(float(w - 1), x2), min(float(h - 1), y2)
        area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
        if area <= 0:
            return

        valid_keypoints = sum(1 for _, _, c in keypoints if c >= self.keypoint_conf_threshold)
        if valid_keypoints == 0:
            # bbox 有效但关键点全不可见时，仍保留检测结果，运动距离后续会 fallback 到 bbox center。
            logger.debug("pose detection has no valid keypoints score=%.3f class_id=%s", score, class_id)

        detections.append(
            {
                "bbox": (x1, y1, x2, y2),
                "confidence": float(score),
                "class_id": int(class_id),
                "area": area,
                "keypoints": keypoints,
                "valid_keypoints": valid_keypoints,
            }
        )

    def _selected_indices(self, keypoints: list[tuple[float, float, float]]) -> list[int]:
        if self.target_keypoints is None:
            return list(range(len(keypoints)))
        return [i for i in self.target_keypoints if 0 <= i < len(keypoints)]

    def _keypoint_centroid(self, keypoints: list[tuple[float, float, float]]) -> tuple[float, float] | None:
        points = []
        for i in self._selected_indices(keypoints):
            x, y, conf = keypoints[i]
            if conf >= self.keypoint_conf_threshold:
                points.append((x, y))
        if not points:
            return None
        arr = np.asarray(points, dtype=np.float32)
        return float(arr[:, 0].mean()), float(arr[:, 1].mean())

    def _motion_distance(self, keypoints: list[tuple[float, float, float]], center: tuple[float, float]) -> float:
        if self.prev_keypoints is None:
            return 0.0

        distances: list[float] = []
        for i in self._selected_indices(keypoints):
            if i >= len(self.prev_keypoints):
                continue
            x, y, conf = keypoints[i]
            px, py, pconf = self.prev_keypoints[i]
            if conf < self.keypoint_conf_threshold or pconf < self.keypoint_conf_threshold:
                continue
            distances.append(float(np.linalg.norm(np.array([x, y]) - np.array([px, py]))))

        if distances:
            if self.motion_mode == "max":
                return float(max(distances))
            if self.motion_mode == "centroid":
                prev_center = self._keypoint_centroid(self.prev_keypoints)
                if prev_center is not None:
                    return float(np.linalg.norm(np.array(center) - np.array(prev_center)))
            return float(np.mean(distances))

        # 关键点暂时不可见时，退回 bbox/centroid 的中心点位移，避免状态完全断掉。
        if self.prev_center is None:
            return 0.0
        return float(np.linalg.norm(np.array(center) - np.array(self.prev_center)))

    def draw_result(self, frame: np.ndarray, result: DetectResult) -> np.ndarray:
        """可选调试函数：在图像上绘制 bbox 和关键点。"""
        out = frame.copy()
        if result.bbox:
            x1, y1, x2, y2 = [int(v) for v in result.bbox]
            cv2.rectangle(out, (x1, y1), (x2, y2), (0, 255, 0), 2)
        keypoints = result.metadata.get("keypoints") if result.metadata else None
        if keypoints:
            for idx, (x, y, conf) in enumerate(keypoints):
                if conf < self.keypoint_conf_threshold:
                    continue
                cv2.circle(out, (int(x), int(y)), 4, (0, 0, 255), -1)
                cv2.putText(out, str(idx), (int(x) + 4, int(y) - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1)
        return out
