from __future__ import annotations

from typing import Any
from fastapi.encoders import jsonable_encoder


def code_ok(data: Any = None, message: str = "ok") -> dict[str, Any]:
    """前端兼容响应格式。

    新增兼容接口使用 code/message/data，保留旧接口的 ok/data/message 不变。
    这样前端可以逐步迁移，不破坏现有调试接口。
    """
    return {"code": 0, "message": message or "ok", "data": jsonable_encoder(data)}


def code_fail(message: str, code: int = 40001, data: Any = None) -> dict[str, Any]:
    return {"code": code, "message": message, "data": jsonable_encoder(data)}
