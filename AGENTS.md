# AGENTS.md

## Project Overview

This repository is the backend for a Robot Vision Platform. It is a FastAPI
service for industrial robot status monitoring over RTSP camera streams.

The backend manages cameras, reads frames from RTSP streams, applies ROI
cropping/masking, runs detector implementations, tracks motion over time,
classifies robot state, records events, saves event frames, and exposes REST,
MJPEG, static image, and WebSocket interfaces for a separate frontend.

Primary runtime stack:

- Python 3.11
- FastAPI / Uvicorn
- SQLAlchemy 2.x with SQLite by default
- Pydantic v2
- OpenCV headless
- ONNX Runtime for YOLO / pose models

The default database is under the backend code root at `data/db/app.db`.
Runtime files are stored under `data/`, model files under `models/`, and logs
under `data/logs/` relative to that backend code root. In the Docker backend
compose, the code root is mounted at `/app`, so these resolve to `/app/data`,
`/app/models`, and `/app/data/logs`.

## Repository Layout

- `backend/app/main.py` wires the FastAPI app, middleware, routers, exception
  handlers, static `/data` mount, database bootstrap, and status WebSocket
  broadcaster.
- `backend/app/api/` contains route handlers. Keep these thin: request parsing,
  dependency injection, and response wrapping belong here.
- `backend/app/services/` contains business logic for cameras, workers, events,
  snapshots, models, frontend adapters, maintenance, status, config, and video
  streaming.
- `backend/app/workers/` contains the RTSP/frame-reading worker orchestration.
  `CameraWorker` should stay focused on the processing loop and delegate logic
  to services, detectors, tracker, and rules.
- `backend/app/detectors/` contains detector implementations:
  `motion`, `aruco`, `yolo`, and `yolo_pose`.
- `backend/app/tracker/` contains trajectory and movement-statistics logic.
- `backend/app/rules/` contains status classification rules such as
  `RobotStopRule`.
- `backend/app/models/` contains SQLAlchemy models for cameras, status, events,
  event frames, and the local model registry.
- `backend/app/schemas/` contains Pydantic request/response schemas.
- `backend/tests/` contains focused tests for logic that does not require real
  RTSP cameras or ONNX inference.

## Important Concepts

- Camera configuration includes `config_version`. When camera settings, ROI, or
  model binding change, increment `config_version` so workers reset tracker/rule
  state and pick up the new runtime configuration.
- Robot stop detection is rule-based, not detector-specific. Detectors output a
  shared `DetectResult`; `SimpleTracker` turns recent motion into a movement
  score; `RobotStopRule` converts that score into `RUNNING`, `IDLE`,
  `STOPPED`, or `UNKNOWN`. `OFFLINE` is assigned by the worker when RTSP frame
  reads fail.
- Rule settings are stored per camera. `motion_threshold` and `stop_seconds`
  live on the `cameras` table; debounce/unknown settings live in
  `camera.detector_config.rule`; tracker scoring settings live in
  `camera.detector_config.tracker`; saved changes must increment
  `config_version`.
- REST APIs generally return the shared shape from `app.core.responses`:
  `{"ok": true, "data": ..., "message": ""}` or the failure equivalent.
- Frontend compatibility routes also support a `code` style response and string
  camera IDs such as `cam_001`; internal database IDs remain numeric.
- Worker output is visible through `/api/system/workers` and
  `/api/cameras/{id}/last-result`.
- Event review is centered on `events` and `event_frames`. Preserve open,
  recover, sampled, and manual frame data when changing event behavior.
- The project favors local/intranet diagnostics. External alert integrations are
  optional and disabled by default with `ALERT_ENABLED=false`.

## Common Commands

Install and run locally:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Windows activation:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

On this Windows workspace, prefer the project virtualenv explicitly when running
commands from automation, because the system `python` may resolve to Anaconda:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Run tests:

```bash
cd backend
pip install -r requirements-dev.txt
pytest -q
```

Docker:

```bash
docker compose -f docker-compose.backend.yml up -d --build
```

Use `docker-compose.backend.yml` for this backend-only repository snapshot.
`docker-compose.yml` references `frontend` and `mediamtx` services and should be
treated as a full-stack skeleton unless those directories are present.

## API Areas

Core backend routes include:

- Cameras: `/api/cameras`
- Camera workers: `/api/cameras/{id}/start`, `/api/cameras/{id}/stop`,
  `/api/cameras/{id}/last-result`
- Debug detection and snapshots: `/api/cameras/{id}/snapshot`,
  `/api/cameras/{id}/debug-detect`, `/api/cameras/{id}/image-detect`
- Video: `/api/cameras/{id}/frame.jpg`, `/api/cameras/{id}/stream.mjpg`
- Status: `/api/status`, `/api/status/{id}`, `/ws/status`
- Events: `/api/events`, `/api/events/summary`, `/api/events/{id}/frames`
- Models: `/api/models`, `/api/models/upload`, `/api/models/register`,
  `/api/models/bind-camera`
- Rules: `/api/cameras/{camera_ref}/rule`,
  `/api/cameras/{camera_ref}/rule/copy`, `/api/rule-templates`
- Config: `/api/config/export`, `/api/config/import`
- System: `/api/system/health`, `/api/system/workers`,
  `/api/system/self-check`, `/api/system/storage`, `/api/system/backup`,
  `/api/system/cleanup`

Frontend compatibility routes include:

- `/api/runtime/status`
- `/stream/cameras/{camera_ref}/snapshot`
- `/stream/cameras/{camera_ref}/mjpeg`
- `/api/cameras/{camera_ref}/roi`
- `/api/settings`
- `/api/debug/keypoints`
- `/api/alarms`
- `/api/tasks`

## Development Guidelines

- Keep route modules thin and put reusable behavior in `services/`.
- Prefer extending existing detector, tracker, rule, and service boundaries over
  adding cross-layer shortcuts.
- When changing camera, ROI, model-binding, or imported configuration behavior,
  verify `config_version` handling.
- Avoid requiring real RTSP cameras in unit tests. Use focused tests for rule,
  tracker, model-service, ROI, and response behavior.
- Preserve existing API compatibility unless a change explicitly requires a
  frontend contract update.
- Do not commit runtime artifacts from `data/`, uploaded models, logs, backups,
  generated snapshots, or local virtual environments.
- Be careful with cleanup and backup code. Destructive cleanup should keep
  dry-run behavior by default.
- ONNX inference and real camera behavior are environment-dependent; document
  manual validation when automated tests cannot cover them.

## Verification Checklist

For backend logic changes, run:

```bash
cd backend
pip install -r requirements-dev.txt
pytest -q
```

On Windows in this workspace, use the explicit venv Python instead:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q
```

For API or routing changes, also start the app and inspect:

```bash
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Then check:

- `http://127.0.0.1:8000/docs`
- `GET /api/system/health`
- `GET /api/system/self-check`

For video, detector, and worker changes, also validate against a real RTSP
camera or a controlled video stream when available.
