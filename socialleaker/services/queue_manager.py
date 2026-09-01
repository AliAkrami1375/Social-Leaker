"""Sequential task queue.

A single background worker processes queued tasks one at a time (FIFO). New
tasks and follow-ups are appended to the queue; when the current task finishes,
the next one starts automatically. This gives the panel a professional, ordered
control flow instead of firing every task concurrently.
"""
from __future__ import annotations

import queue
import threading

from ..database import SessionLocal
from ..models import Task, TaskStatus
from .task_runner import run_task

_Q: "queue.Queue[int]" = queue.Queue()
_worker: threading.Thread | None = None
_current: dict[str, int | None] = {"task_id": None}
_order: list[int] = []          # mirror of queued ids for position reporting
_lock = threading.RLock()       # reentrant: enqueue() calls position() while held


def enqueue(task_id: int) -> int:
    """Add a task to the queue; returns its 1-based position (0 = running now)."""
    with _lock:
        if task_id not in _order and _current["task_id"] != task_id:
            _order.append(task_id)
            _Q.put(task_id)
        return position(task_id)


def position(task_id: int) -> int:
    with _lock:
        if _current["task_id"] == task_id:
            return 0
        return (_order.index(task_id) + 1) if task_id in _order else -1


def snapshot() -> dict:
    with _lock:
        return {"current": _current["task_id"], "queued": list(_order)}


def _pop_from_order(task_id: int) -> None:
    with _lock:
        if task_id in _order:
            _order.remove(task_id)


def dequeue(task_id: int) -> None:
    """Remove a not-yet-started task from the pending order.

    The id may still sit in the internal Queue, but the worker skips any task
    whose status is no longer 'queued', so marking it stopped is sufficient.
    """
    _pop_from_order(task_id)


def _loop() -> None:
    while True:
        task_id = _Q.get()
        try:
            db = SessionLocal()
            task = db.get(Task, task_id)
            valid = bool(task and task.status == TaskStatus.queued)
            db.close()
            _pop_from_order(task_id)
            if not valid:
                continue
            with _lock:
                _current["task_id"] = task_id
            run_task(task_id)          # synchronous; updates status/steps itself
        except Exception:
            pass
        finally:
            with _lock:
                _current["task_id"] = None
            _Q.task_done()


def start_worker() -> None:
    """Start the worker (idempotent) and re-queue any tasks left as 'queued'."""
    global _worker
    if _worker and _worker.is_alive():
        return
    _worker = threading.Thread(target=_loop, name="task-queue-worker", daemon=True)
    _worker.start()

    # Resume tasks interrupted by a restart: anything left 'queued' (never
    # started) or 'running' (killed mid-collection). The task loop itself is
    # idempotent — it re-reads already-collected handles and continues.
    db = SessionLocal()
    try:
        pending = (
            db.query(Task)
            .filter(Task.status.in_([TaskStatus.queued, TaskStatus.running]))
            .order_by(Task.id)
            .all()
        )
        for t in pending:
            t.status = TaskStatus.queued
        db.commit()
        for t in pending:
            enqueue(t.id)
    finally:
        db.close()
