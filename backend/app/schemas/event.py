from datetime import datetime
from pydantic import BaseModel


class EventOut(BaseModel):
    id: int
    camera_id: int
    event_type: str
    status: str
    start_time: datetime
    end_time: datetime | None = None
    snapshot_path: str | None = None
    annotated_snapshot_path: str | None = None
    recovery_snapshot_path: str | None = None
    recovery_annotated_path: str | None = None
    clip_path: str | None = None
    reason: str = ""
    rule_detail: dict | None = None
    detector_type: str = ""
    handled: bool
    false_alarm: bool = False
    remark: str = ""

    class Config:
        from_attributes = True
