from fastapi import FastAPI, HTTPException, Request
from app.core.logging import setup_logging, get_logger
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from app.core.responses import fail, ok
from fastapi.staticfiles import StaticFiles
import os
from app.core.config import settings
from app.database import Base, engine
from app.database_migrations import ensure_sqlite_columns, ensure_sqlite_indexes
from app.models import Camera, CameraStatus, Event
from app.api.camera_api import router as camera_router
from app.api.debug_api import router as debug_router
from app.api.status_api import router as status_router
from app.api.event_api import router as event_router
from app.api.system_api import router as system_router
from app.api.model_api import router as model_router
from app.api.config_api import router as config_router
from app.api.video_api import router as video_router
from app.api.frontend_compat_api import router as frontend_compat_router
from app.api.detection_task_api import router as detection_task_router
from app.api.shared_rule_api import router as shared_rule_router

setup_logging()
logger = get_logger(__name__)
logger.info("backend booting, app_name=%s", settings.app_name)

Base.metadata.create_all(bind=engine)
ensure_sqlite_columns(engine)
ensure_sqlite_indexes(engine)
logger.info("database tables checked/created")

app = FastAPI(title=settings.app_name)
allow_all_origins = "*" in settings.cors_origin_list
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if allow_all_origins else settings.cors_origin_list,
    allow_credentials=not allow_all_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(camera_router)
app.include_router(debug_router)
app.include_router(status_router)
app.include_router(event_router)
app.include_router(system_router)
app.include_router(model_router)
app.include_router(config_router)
app.include_router(video_router)
app.include_router(detection_task_router)
app.include_router(shared_rule_router)
app.include_router(frontend_compat_router)

os.makedirs(settings.storage_dir, exist_ok=True)
os.makedirs(settings.model_dir, exist_ok=True)
os.makedirs(os.path.join(settings.storage_dir, "snapshots"), exist_ok=True)
os.makedirs(os.path.join(settings.storage_dir, "clips"), exist_ok=True)
app.mount("/data", StaticFiles(directory=settings.storage_dir), name="data")
logger.info("static storage mounted at /data -> %s", settings.storage_dir)

@app.get("/")
def root():
    logger.debug("root endpoint called")
    return ok({"name": settings.app_name, "docs": "/docs"})


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content=fail(str(exc.detail)))


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content=fail("request validation failed", exc.errors()))


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("unhandled request error path=%s", request.url.path)
    return JSONResponse(status_code=500, content=fail("internal server error"))


import asyncio
from app.database import SessionLocal
from app.models.detection_task import TaskStatus
from app.models.status import CameraStatus
from app.services import detection_task_service, status_service
from app.workers.ws_manager import ws_manager

@app.on_event("startup")
async def start_status_broadcaster():
    db = SessionLocal()
    try:
        detection_task_service.ensure_defaults_for_all_cameras(db)
    finally:
        db.close()
    logger.info("status websocket broadcaster started")
    async def loop():
        while True:
            db = SessionLocal()
            try:
                rows = db.query(TaskStatus).all()
                data = [{
                    "task_id": r.task_id,
                    "camera_id": r.camera_id,
                    "status": r.status,
                    "last_frame_time": r.last_frame_time,
                    "last_motion_time": r.last_motion_time,
                    "last_detect_time": r.last_detect_time,
                    "confidence": r.confidence,
                    "message": r.message,
                    "reason_code": r.reason_code,
                    "detail": r.detail,
                    "updated_at": r.updated_at,
                } for r in rows]
                await ws_manager.broadcast({"type": "status", "data": data})
                logger.debug("broadcast status count=%s", len(data))
            finally:
                db.close()
            await asyncio.sleep(2)
    asyncio.create_task(loop())
