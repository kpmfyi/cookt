"""SQLite-backed job queue with a single in-process worker thread.

One worker means model jobs are naturally serialized inside the app; the
`cookt.llm` file lock serializes them against backfill processes too.
Handlers are registered by type with `@handler("type")`.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import traceback
from collections.abc import Callable
from typing import Any

from . import db

log = logging.getLogger("cookt.jobs")

Handler = Callable[[sqlite3.Connection, dict[str, Any]], None]
_handlers: dict[str, Handler] = {}
_wake = threading.Event()


def handler(job_type: str) -> Callable[[Handler], Handler]:
    def register(fn: Handler) -> Handler:
        _handlers[job_type] = fn
        return fn

    return register


def enqueue(conn: sqlite3.Connection, job_type: str, payload: dict[str, Any]) -> str:
    job_id = db.new_id()
    stamp = db.now()
    conn.execute(
        "INSERT INTO jobs(id, type, payload, status, created_at, updated_at) "
        "VALUES (?, ?, ?, 'queued', ?, ?)",
        (job_id, job_type, db.dumps(payload), stamp, stamp),
    )
    _wake.set()
    return job_id


def _claim(conn: sqlite3.Connection) -> sqlite3.Row | None:
    with db.tx(conn):
        row = conn.execute(
            "SELECT * FROM jobs WHERE status = 'queued' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE jobs SET status = 'running', attempts = attempts + 1, updated_at = ? "
            "WHERE id = ?",
            (db.now(), row["id"]),
        )
        return row


def run_one(conn: sqlite3.Connection) -> bool:
    row = _claim(conn)
    if row is None:
        return False
    fn = _handlers.get(row["type"])
    try:
        if fn is None:
            raise RuntimeError(f"no handler for job type {row['type']}")
        fn(conn, db.loads(row["payload"]))
    except Exception as exc:  # noqa: BLE001 - job failures are recorded, never fatal
        log.warning("job %s (%s) failed: %s", row["id"], row["type"], exc)
        conn.execute(
            "UPDATE jobs SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
            (f"{exc}\n{traceback.format_exc()[-2000:]}", db.now(), row["id"]),
        )
    else:
        conn.execute(
            "UPDATE jobs SET status = 'done', updated_at = ? WHERE id = ?", (db.now(), row["id"])
        )
    return True


class Worker(threading.Thread):
    def __init__(self) -> None:
        super().__init__(name="cookt-worker", daemon=True)
        self._stop = threading.Event()

    def run(self) -> None:
        conn = db.connect()
        # Jobs left 'running' by a previous process were interrupted: requeue them.
        conn.execute(
            "UPDATE jobs SET status = 'queued', updated_at = ? WHERE status = 'running'",
            (db.now(),),
        )
        from .imports import recover_queued

        recover_queued(conn)
        while not self._stop.is_set():
            try:
                worked = run_one(conn)
            except Exception:  # noqa: BLE001
                log.exception("worker loop error")
                worked = False
            if not worked:
                _wake.wait(timeout=5)
                _wake.clear()

    def stop(self) -> None:
        self._stop.set()
        _wake.set()


def start_worker() -> Worker:
    # Import handler modules so they register.
    from . import pipeline_jobs  # noqa: F401

    worker = Worker()
    worker.start()
    return worker
