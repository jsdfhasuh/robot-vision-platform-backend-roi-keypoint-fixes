from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.camera_api import router as camera_router
from app.api.detection_task_api import router as detection_task_router
from app.api.event_api import router as event_router
from app.api.frontend_compat_api import router as frontend_compat_router
from app.api.shared_rule_api import router as shared_rule_router
from app.database import Base, get_db
from app.models import Camera, DetectionRule, DetectionTask, Event
from app.services import detection_task_service, shared_rule_service
from app.workers import camera_manager as camera_manager_module
from app.workers.camera_manager import CameraManager, camera_manager


def _reset_global_manager() -> None:
    camera_manager.stream_workers.clear()
    camera_manager.stream_threads.clear()
    camera_manager.task_runtimes.clear()
    camera_manager.task_to_camera.clear()
    camera_manager.last_task_states.clear()


def _client():
    _reset_global_manager()
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    app = FastAPI()
    app.include_router(camera_router)
    app.include_router(detection_task_router)
    app.include_router(shared_rule_router)
    app.include_router(event_router)
    app.include_router(frontend_compat_router)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    def session():
        return TestingSessionLocal()

    return TestClient(app), session


def _camera_payload(name: str = "cam1") -> dict:
    return {"name": name, "rtsp_url": "rtsp://example/stream", "enabled": True, "fps_limit": 3}


def _error_text(response) -> str:
    data = response.json()
    return str(data.get("message") or data.get("detail") or "")


def test_create_camera_creates_default_motion_task_not_running():
    client, _ = _client()

    created = client.post("/api/cameras", json=_camera_payload())
    assert created.status_code == 200
    camera_id = created.json()["data"]["numeric_id"]

    tasks = client.get(f"/api/detection-tasks?camera_id={camera_id}")
    assert tasks.status_code == 200
    data = tasks.json()["data"]
    assert len(data) == 1
    assert data[0]["detector_type"] == "motion"
    assert data[0]["is_default"] is True
    assert data[0]["running"] is False
    assert data[0]["status"]["status"] == "UNKNOWN"


def test_legacy_worker_and_tasks_routes_are_404():
    client, _ = _client()

    assert client.post("/api/cameras/1/start").status_code == 404
    assert client.post("/api/cameras/1/stop").status_code == 404
    assert client.get("/api/cameras/1/last-result").status_code == 404
    assert client.get("/api/tasks").status_code == 404
    assert client.post("/api/tasks/task_001/start").status_code == 404
    assert client.post("/api/tasks/task_001/stop").status_code == 404
    assert client.get("/api/cameras/1/rule").status_code == 404
    assert client.put("/api/cameras/1/rule", json={}).status_code == 404
    assert client.get("/api/rule-templates").status_code == 404


def test_motion_only_rule_cannot_bind_yolo_task():
    client, _ = _client()
    camera_id = client.post("/api/cameras", json=_camera_payload()).json()["data"]["numeric_id"]
    rule = client.post(
        "/api/rules",
        json={
            "name": "motion only",
            "supported_detector_types": ["motion"],
            "rule_config": {"motion_threshold": 3, "stop_seconds": 10},
        },
    )
    assert rule.status_code == 200
    rule_id = rule.json()["data"]["id"]

    response = client.post(
        "/api/detection-tasks",
        json={
            "camera_id": camera_id,
            "name": "bad yolo task",
            "detector_type": "yolo",
            "rule_id": rule_id,
            "detector_config": {"model_path": "/missing/model.onnx"},
        },
    )
    assert response.status_code == 422
    assert "does not support" in _error_text(response)


def test_start_yolo_task_fails_when_model_file_missing():
    client, session = _client()
    camera_id = client.post("/api/cameras", json=_camera_payload()).json()["data"]["numeric_id"]
    db = session()
    try:
        rule = shared_rule_service.default_rule_for_detector(db, "yolo")
        task = DetectionTask(
            camera_id=camera_id,
            name="seeded yolo",
            detector_type="yolo",
            detector_config={"model_path": "/missing/model.onnx"},
            tracker_config=dict(detection_task_service.DEFAULT_TRACKER_CONFIG),
            rule_id=rule.id,
            enabled=True,
            fps_limit=3,
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        detection_task_service.ensure_status_rows(db, task)
        task_id = task.id
    finally:
        db.close()

    response = client.post(f"/api/detection-tasks/{task_id}/start")
    assert response.status_code == 422
    assert "model file not found" in _error_text(response)


def test_shared_rule_usage_and_version_update():
    client, _ = _client()
    camera_id = client.post("/api/cameras", json=_camera_payload()).json()["data"]["numeric_id"]
    rule = client.post(
        "/api/rules",
        json={
            "name": "motion shared",
            "supported_detector_types": ["motion"],
            "rule_config": {"motion_threshold": 4, "stop_seconds": 20},
        },
    ).json()["data"]

    task = client.post(
        "/api/detection-tasks",
        json={"camera_id": camera_id, "name": "roi motion", "detector_type": "motion", "rule_id": rule["id"], "roi": [0, 0, 100, 100]},
    )
    assert task.status_code == 200

    usage = client.get(f"/api/rules/{rule['id']}/usage")
    assert usage.status_code == 200
    assert usage.json()["data"]["count"] == 1

    updated = client.put(f"/api/rules/{rule['id']}", json={"rule_config": {"motion_threshold": 8, "stop_seconds": 30}})
    assert updated.status_code == 200
    assert updated.json()["data"]["version"] == rule["version"] + 1

    blocked = client.delete(f"/api/rules/{rule['id']}")
    assert blocked.status_code == 409


def test_events_can_filter_by_task_id_and_include_names():
    client, session = _client()
    camera_id = client.post("/api/cameras", json=_camera_payload("event cam")).json()["data"]["numeric_id"]
    db = session()
    try:
        task = detection_task_service.default_task_for_camera(db, camera_id)
        db.add(Event(camera_id=camera_id, task_id=task.id, event_type="STOPPED", status="OPEN", detector_type="motion"))
        db.add(Event(camera_id=camera_id, task_id=None, event_type="OFFLINE", status="OPEN", detector_type=""))
        db.commit()
        task_id = task.id
    finally:
        db.close()

    response = client.get(f"/api/events?task_id={task_id}")
    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) == 1
    assert data[0]["task_id"] == task_id
    assert data[0]["task_name"]
    assert data[0]["camera_name"] == "event cam"


def test_camera_manager_uses_one_stream_for_multiple_tasks(monkeypatch):
    class DummyWorker:
        def __init__(self, camera_id, runtime_provider):
            self.camera_id = camera_id
            self.runtime_provider = runtime_provider
            self.running = False
            self.stop_called = False

        def run(self):
            self.running = True

        def stop(self):
            self.stop_called = True
            self.running = False

        def get_debug_state(self):
            return {"camera_id": self.camera_id, "running": self.running}

    class DummyThread:
        def __init__(self, target, daemon, name):
            self.target = target
            self.daemon = daemon
            self.name = name
            self._alive = False

        def start(self):
            self._alive = True

        def is_alive(self):
            return self._alive

    monkeypatch.setattr(camera_manager_module, "CameraStreamWorker", DummyWorker)
    monkeypatch.setattr(camera_manager_module.threading, "Thread", DummyThread)

    manager = CameraManager()
    assert manager.start_task(1, 10)[0] is True
    assert manager.start_task(2, 10)[0] is True
    workers = manager.list_workers()
    assert len(workers) == 1
    assert workers[0]["camera_id"] == 10
    assert workers[0]["active_task_count"] == 2
    assert set(workers[0]["task_ids"]) == {1, 2}
