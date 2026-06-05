from __future__ import annotations

import threading
from datetime import datetime

from app.core.logging import get_logger
from app.workers.stream_worker import CameraStreamWorker, TaskRuntime

logger = get_logger(__name__)


class CameraManager:
    def __init__(self):
        self.stream_workers: dict[int, CameraStreamWorker] = {}
        self.stream_threads: dict[int, threading.Thread] = {}
        self.task_runtimes: dict[int, TaskRuntime] = {}
        self.task_to_camera: dict[int, int] = {}
        self.last_task_states: dict[int, dict] = {}
        self._lock = threading.RLock()

    def start_task(self, task_id: int, camera_id: int):
        with self._lock:
            runtime = self.task_runtimes.get(task_id)
            if runtime and runtime.running:
                logger.warning("task already running task_id=%s camera_id=%s", task_id, camera_id)
                return False, "task already running"

            runtime = TaskRuntime(task_id, camera_id)
            self.task_runtimes[task_id] = runtime
            self.task_to_camera[task_id] = camera_id

            thread = self.stream_threads.get(camera_id)
            if thread and thread.is_alive():
                logger.info("stream already running camera_id=%s, task attached task_id=%s", camera_id, task_id)
                return True, "task attached to running stream"

            worker = CameraStreamWorker(camera_id, self.get_runtimes_for_camera)
            thread = threading.Thread(target=worker.run, daemon=True, name=f"camera-stream-{camera_id}")
            self.stream_workers[camera_id] = worker
            self.stream_threads[camera_id] = thread
            thread.start()
            logger.info("stream worker started camera_id=%s first_task_id=%s", camera_id, task_id)
            return True, "stream worker started"

    def stop_task(self, task_id: int):
        with self._lock:
            runtime = self.task_runtimes.pop(task_id, None)
            camera_id = self.task_to_camera.pop(task_id, None)
            if not runtime:
                logger.warning("stop requested but task not running task_id=%s", task_id)
                return False, "task not running"
            runtime.stop()
            self.last_task_states[task_id] = runtime.get_debug_state()
            camera_id = camera_id or runtime.camera_id
            if not self._active_task_ids_for_camera(camera_id):
                worker = self.stream_workers.get(camera_id)
                if worker:
                    worker.stop()
            logger.info("task runtime stopped task_id=%s camera_id=%s", task_id, camera_id)
            return True, "task stopping"

    def stop_camera(self, camera_id: int):
        with self._lock:
            stopped = []
            for task_id, cid in list(self.task_to_camera.items()):
                if cid == camera_id:
                    runtime = self.task_runtimes.pop(task_id, None)
                    self.task_to_camera.pop(task_id, None)
                    if runtime:
                        runtime.stop()
                        self.last_task_states[task_id] = runtime.get_debug_state()
                        stopped.append(task_id)
            worker = self.stream_workers.get(camera_id)
            if worker:
                worker.stop()
            logger.info("camera tasks stopped camera_id=%s tasks=%s", camera_id, stopped)
            return True, "camera tasks stopping"

    def _active_task_ids_for_camera(self, camera_id: int) -> list[int]:
        return [
            task_id
            for task_id, cid in self.task_to_camera.items()
            if cid == camera_id and self.task_runtimes.get(task_id) and self.task_runtimes[task_id].running
        ]

    def get_runtimes_for_camera(self, camera_id: int) -> list[TaskRuntime]:
        with self._lock:
            runtimes = []
            for task_id in list(self._active_task_ids_for_camera(camera_id)):
                runtime = self.task_runtimes.get(task_id)
                if runtime and runtime.running:
                    runtimes.append(runtime)
            return runtimes

    def is_task_running(self, task_id: int) -> bool:
        with self._lock:
            runtime = self.task_runtimes.get(task_id)
            return bool(runtime and runtime.running)

    def list_workers(self):
        with self._lock:
            rows = []
            for cid, thread in self.stream_threads.items():
                worker = self.stream_workers.get(cid)
                state = worker.get_debug_state() if worker else None
                task_ids = self._active_task_ids_for_camera(cid)
                rows.append(
                    {
                        "camera_id": cid,
                        "alive": thread.is_alive(),
                        "thread_name": thread.name,
                        "checked_at": datetime.utcnow(),
                        "task_ids": task_ids,
                        "active_task_count": len(task_ids),
                        "health": state,
                    }
                )
            return rows

    def get_stream_state(self, camera_id: int):
        with self._lock:
            worker = self.stream_workers.get(camera_id)
            if not worker:
                return None
            return worker.get_debug_state()

    def get_task_state(self, task_id: int):
        with self._lock:
            runtime = self.task_runtimes.get(task_id)
            if runtime:
                return runtime.get_debug_state()
            return self.last_task_states.get(task_id)

    def get_first_task_state_for_camera(self, camera_id: int):
        with self._lock:
            for task_id in self._active_task_ids_for_camera(camera_id):
                runtime = self.task_runtimes.get(task_id)
                if runtime:
                    return runtime.get_debug_state()
            return None


camera_manager = CameraManager()
