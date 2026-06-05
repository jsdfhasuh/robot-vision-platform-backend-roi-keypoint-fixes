from __future__ import annotations

from sqlalchemy import text

from app.core.logging import get_logger

logger = get_logger(__name__)


def _add_column(conn, table: str, existing: set[str], column: str, ddl: str) -> None:
    if column not in existing:
        logger.info("migrate sqlite: add %s.%s", table, column)
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
        existing.add(column)


def ensure_sqlite_columns(engine) -> None:
    """轻量兼容旧 SQLite 数据库。

    MVP 阶段不引入 Alembic。这里仅用于给旧数据库补充新增字段，避免升级包后启动报错。
    PostgreSQL/正式环境建议改用 Alembic 迁移。
    """
    if not str(engine.url).startswith("sqlite"):
        return
    with engine.begin() as conn:
        camera_rows = conn.execute(text("PRAGMA table_info(cameras)")).fetchall()
        camera_columns = {row[1] for row in camera_rows}
        _add_column(conn, "cameras", camera_columns, "config_version", "INTEGER DEFAULT 1")

        event_rows = conn.execute(text("PRAGMA table_info(events)")).fetchall()
        event_columns = {row[1] for row in event_rows}
        _add_column(conn, "events", event_columns, "annotated_snapshot_path", "VARCHAR(1024)")
        _add_column(conn, "events", event_columns, "recovery_snapshot_path", "VARCHAR(1024)")
        _add_column(conn, "events", event_columns, "recovery_annotated_path", "VARCHAR(1024)")
        _add_column(conn, "events", event_columns, "reason", "VARCHAR(1024) DEFAULT ''")
        _add_column(conn, "events", event_columns, "rule_detail", "JSON")
        _add_column(conn, "events", event_columns, "detector_type", "VARCHAR(64) DEFAULT ''")
        _add_column(conn, "events", event_columns, "false_alarm", "BOOLEAN DEFAULT 0")
        _add_column(conn, "events", event_columns, "task_id", "INTEGER")

        event_frame_rows = conn.execute(text("PRAGMA table_info(event_frames)")).fetchall()
        event_frame_columns = {row[1] for row in event_frame_rows}
        _add_column(conn, "event_frames", event_frame_columns, "task_id", "INTEGER")


def ensure_sqlite_indexes(engine) -> None:
    if not str(engine.url).startswith("sqlite"):
        return
    with engine.begin() as conn:
        # create_all 会创建新表；这里补索引/兼容旧库时的轻量兜底。
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_event_frames_event_id ON event_frames(event_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_event_frames_camera_id ON event_frames(camera_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_event_frames_task_id ON event_frames(task_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_events_task_id ON events(task_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_model_registry_file_name ON model_registry(file_name)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_detection_tasks_camera_id ON detection_tasks(camera_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_detection_tasks_rule_id ON detection_tasks(rule_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_task_status_camera_id ON task_status(camera_id)"))
