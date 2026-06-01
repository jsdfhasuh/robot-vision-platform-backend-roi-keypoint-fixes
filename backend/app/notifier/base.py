from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class AlertMessage:
    event_id: int | None
    camera_id: int
    camera_name: str
    event_type: str
    action: str  # OPEN / RECOVERED / HANDLED
    status: str
    message: str = ""
    snapshot_path: str | None = None
    timestamp: datetime | None = None
    extra: dict[str, Any] | None = None

    def title(self) -> str:
        return f"[{self.event_type}] {self.action} - {self.camera_name}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "camera_id": self.camera_id,
            "camera_name": self.camera_name,
            "event_type": self.event_type,
            "action": self.action,
            "status": self.status,
            "message": self.message,
            "snapshot_path": self.snapshot_path,
            "timestamp": (self.timestamp or datetime.utcnow()).isoformat(),
            "extra": self.extra or {},
        }

    def to_text(self) -> str:
        ts = (self.timestamp or datetime.utcnow()).strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            self.title(),
            f"时间: {ts}",
            f"摄像头: {self.camera_name} (ID: {self.camera_id})",
            f"事件: {self.event_type}",
            f"动作: {self.action}",
            f"状态: {self.status}",
        ]
        if self.message:
            lines.append(f"说明: {self.message}")
        if self.snapshot_path:
            lines.append(f"截图: {self.snapshot_path}")
        return "\n".join(lines)


class BaseNotifier:
    name = "base"

    def send(self, msg: AlertMessage) -> bool:
        raise NotImplementedError
