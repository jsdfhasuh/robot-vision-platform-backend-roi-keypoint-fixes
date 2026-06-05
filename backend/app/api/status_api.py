from __future__ import annotations

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.responses import ok
from app.database import get_db
from app.services import status_service
from app.workers.ws_manager import ws_manager

router = APIRouter(tags=["status"])
logger = get_logger(__name__)


@router.get("/api/status")
def list_status(db: Session = Depends(get_db)):
    rows = status_service.list_task_status(db)
    return ok([status_service.task_status_to_dict(row) for row in rows])


@router.get("/api/status/{camera_id}")
def get_status(camera_id: int, db: Session = Depends(get_db)):
    aggregate = status_service.get_status(db, camera_id)
    stream = status_service.get_stream_status(db, camera_id)
    tasks = status_service.list_task_status(db, camera_id=camera_id)
    return ok({
        "camera": status_service.status_to_dict(aggregate) if aggregate else None,
        "stream": status_service.stream_status_to_dict(stream) if stream else None,
        "tasks": [status_service.task_status_to_dict(row) for row in tasks],
    })


@router.websocket("/ws/status")
async def websocket_status(websocket: WebSocket):
    await ws_manager.connect(websocket)
    logger.info("websocket status connected clients=%s", len(ws_manager.connections))
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
        logger.info("websocket status disconnected clients=%s", len(ws_manager.connections))
