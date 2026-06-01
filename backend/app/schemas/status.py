from datetime import datetime
from pydantic import BaseModel

class StatusOut(BaseModel):
    camera_id: int
    status: str
    last_frame_time: datetime | None = None
    last_motion_time: datetime | None = None
    confidence: float = 0.0
    message: str = ""
    updated_at: datetime

    class Config:
        from_attributes = True
