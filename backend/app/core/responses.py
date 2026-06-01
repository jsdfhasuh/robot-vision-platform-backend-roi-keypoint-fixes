from __future__ import annotations

from typing import Any

from fastapi.encoders import jsonable_encoder


def ok(data: Any = None, message: str = "") -> dict[str, Any]:
    """统一成功响应。

    同时兼容两种前端风格：
    - 新后端调试风格：ok/data/message
    - 前端工程文档风格：code/message/data，其中 code=0 表示成功
    """
    return {
        "ok": True,
        "code": 0,
        "data": jsonable_encoder(data),
        "message": message or "ok",
    }


def fail(message: str, data: Any = None, code: int = 40001) -> dict[str, Any]:
    """统一失败响应。"""
    return {
        "ok": False,
        "code": code,
        "data": jsonable_encoder(data),
        "message": message,
    }
