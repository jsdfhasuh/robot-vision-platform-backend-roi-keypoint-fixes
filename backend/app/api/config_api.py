from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.database import get_db
from app.services import config_service

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("/export")
def export_config(db: Session = Depends(get_db)):
    return ok(config_service.export_config(db))


@router.post("/import")
def import_config(payload: dict, db: Session = Depends(get_db)):
    return ok(config_service.import_config(db, payload), "config imported")
