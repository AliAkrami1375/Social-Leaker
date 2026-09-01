"""Task CRUD + lifecycle + queue + step timeline + reporting."""
from __future__ import annotations

import json

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user, require_operator
from ..models import CollectedProfile, Task, TaskStatus, TaskStep, User
from ..schemas import StepOut, TaskIn, TaskOut
from ..services import queue_manager

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _default_title(prompt: str) -> str:
    words = prompt.strip().split()
    return (" ".join(words[:7]) + ("…" if len(words) > 7 else "")) or "Untitled task"


# ── Queue (declared before /{task_id} so it is not treated as an id) ─
@router.get("/queue/status")
def queue_status(_: User = Depends(get_current_user)):
    return queue_manager.snapshot()


@router.get("", response_model=list[TaskOut])
def list_tasks(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return db.query(Task).order_by(Task.created_at.desc()).all()


@router.post("", response_model=TaskOut)
def create_task(payload: TaskIn, db: Session = Depends(get_db), user: User = Depends(require_operator)):
    task = Task(
        title=(payload.title or _default_title(payload.prompt)),
        prompt=payload.prompt,
        platform=payload.platform,
        goal_target=max(0, min(payload.goal_target, 2000)),
        max_iterations=max(1, min(payload.max_iterations, 50)),
        owner_id=user.id,
        status=TaskStatus.draft,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


@router.get("/{task_id}", response_model=TaskOut)
def get_task(task_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task


@router.post("/{task_id}/start")
def start_task(task_id: int, db: Session = Depends(get_db), _: User = Depends(require_operator)):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if queue_manager.position(task_id) >= 0:
        raise HTTPException(409, "Task is already queued or running")
    task.status = TaskStatus.queued
    db.commit()
    pos = queue_manager.enqueue(task_id)
    db.refresh(task)
    return {"task": TaskOut.model_validate(task).model_dump(mode="json"), "queue_position": pos}


@router.post("/{task_id}/followup", response_model=TaskOut)
def followup_task(task_id: int, text: str = Body(..., embed=True),
                  db: Session = Depends(get_db), _: User = Depends(require_operator)):
    """Append an additional instruction and re-queue the task to run again."""
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    text = (text or "").strip()
    if text:
        task.prompt = (task.prompt or "") + "\n\n[Follow-up] " + text
    task.status = TaskStatus.queued
    db.commit()
    queue_manager.enqueue(task_id)
    db.refresh(task)
    return task


@router.post("/{task_id}/stop", response_model=TaskOut)
def stop_task(task_id: int, db: Session = Depends(get_db), _: User = Depends(require_operator)):
    from ..services import task_runner

    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    # If it is running, signal cancellation; if it is only queued, drop it now.
    if task_runner.is_running(task_id):
        task_runner.request_cancel(task_id)
    elif task.status == TaskStatus.queued:
        queue_manager.dequeue(task_id)
        task.status = TaskStatus.stopped
        db.commit()
        db.refresh(task)
    return task


@router.delete("/{task_id}")
def delete_task(task_id: int, db: Session = Depends(get_db), _: User = Depends(require_operator)):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    db.delete(task)
    db.commit()
    return {"ok": True}


@router.get("/{task_id}/steps", response_model=list[StepOut])
def task_steps(task_id: int, after_seq: int = 0, db: Session = Depends(get_db),
               _: User = Depends(get_current_user)):
    return (
        db.query(TaskStep)
        .filter(TaskStep.task_id == task_id, TaskStep.seq > after_seq)
        .order_by(TaskStep.seq.asc(), TaskStep.id.asc())
        .all()
    )


@router.get("/{task_id}/report")
def task_report(task_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    """Full JSON report for a task: definition, every step, every result."""
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    steps = db.query(TaskStep).filter(TaskStep.task_id == task_id).order_by(TaskStep.seq).all()
    results = db.query(CollectedProfile).filter(CollectedProfile.task_id == task_id).all()

    def prof(r):
        return {c.name: getattr(r, c.name) for c in CollectedProfile.__table__.columns
                if c.name != "raw_json"}

    return {
        "task": TaskOut.model_validate(task).model_dump(mode="json"),
        "queue_position": queue_manager.position(task_id),
        "steps": [StepOut.model_validate(s).model_dump(mode="json") for s in steps],
        "results": [prof(r) for r in results],
        "summary": {
            "profiles": len(results),
            "verified": sum(1 for r in results if r.is_verified),
            "total_reach": sum(r.followers or 0 for r in results),
        },
    }
