from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.database import get_db
from app.services import model_service

router = APIRouter(prefix="/api/models", tags=["models"])


class ModelRegisterPayload(BaseModel):
    file_name: str
    file_path: str | None = None
    name: str | None = None
    display_name: str | None = None
    model_type: str | None = None
    model_family: str | None = None
    input_size: int = 640
    class_count: int = 1
    num_keypoints: int = 0
    labels: list | None = None
    metadata: dict | None = None


class ModelMetadataPayload(BaseModel):
    name: str | None = None
    model_type: str | None = None
    model_family: str | None = None
    input_size: int | None = None
    class_count: int | None = None
    num_keypoints: int | None = None
    labels: list | None = None
    metadata: dict | None = None


class BindModelPayload(BaseModel):
    camera_id: int
    model_id: int
    extra_config: dict | None = None


@router.get("")
def list_models(db: Session = Depends(get_db)):
    return ok(model_service.list_models(db))


@router.post("/upload")
async def upload_model(
    file: UploadFile = File(...),
    name: str | None = Form(None),
    model_type: str | None = Form(None),
    model_family: str | None = Form(None),
    input_size: int = Form(640),
    class_count: int = Form(1),
    num_keypoints: int = Form(0),
    labels: str | None = Form(None),
    metadata: str | None = Form(None),
    db: Session = Depends(get_db),
):
    data = await model_service.upload_model(
        db,
        file,
        name=name,
        model_type=model_type,
        model_family=model_family,
        input_size=input_size,
        class_count=class_count,
        num_keypoints=num_keypoints,
        labels=labels,
        metadata=metadata,
    )
    return ok(data, "model uploaded")


@router.post("/register")
def register_model(payload: ModelRegisterPayload, db: Session = Depends(get_db)):
    return ok(model_service.register_existing_model(db, payload.model_dump(exclude_unset=True)), "model registered")


@router.put("/registry/{model_id}")
def update_model_metadata(model_id: int, payload: ModelMetadataPayload, db: Session = Depends(get_db)):
    return ok(model_service.update_metadata(db, model_id, payload.model_dump(exclude_unset=True)), "model metadata updated")


@router.post("/bind-camera")
def bind_model_to_camera(payload: BindModelPayload, db: Session = Depends(get_db)):
    return ok(model_service.bind_model_to_camera(db, camera_id=payload.camera_id, model_id=payload.model_id, extra_config=payload.extra_config), "model bound to camera")


@router.delete("/{model_name}")
def delete_model(model_name: str, db: Session = Depends(get_db)):
    return ok(model_service.delete_model(db, model_name), "model deleted")


@router.post("/{model_id}/test-image")
async def test_model_image(
    model_id: int,
    file: UploadFile = File(...),
    extra_config: str | None = Form(None),
    db: Session = Depends(get_db),
):
    return ok(await model_service.test_model_image(db, model_id=model_id, file=file, extra_config=extra_config), "model image tested")
