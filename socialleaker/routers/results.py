"""Collected-profile listing, export and dashboard reporting."""
from __future__ import annotations

import csv
import io
import json

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import CollectedProfile, Integration, Task, TaskStatus, User
from ..schemas import ProfileOut

router = APIRouter(prefix="/api", tags=["results"])


@router.get("/results", response_model=list[ProfileOut])
def list_results(
    task_id: int | None = None,
    q: str | None = None,
    verified: bool | None = None,
    sort: str = Query("collected_at", pattern="^(collected_at|followers|posts_count|username)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = 300,
    offset: int = 0,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    query = db.query(CollectedProfile)
    if task_id:
        query = query.filter(CollectedProfile.task_id == task_id)
    if q:
        like = f"%{q}%"
        query = query.filter(
            (CollectedProfile.username.ilike(like)) | (CollectedProfile.full_name.ilike(like))
        )
    if verified is not None:
        query = query.filter(CollectedProfile.is_verified == verified)
    col = getattr(CollectedProfile, sort)
    col = col.desc() if order == "desc" else col.asc()
    return query.order_by(col).offset(offset).limit(limit).all()


@router.get("/results/export")
def export_results(
    task_id: int | None = None,
    fmt: str = Query("csv", pattern="^(csv|json)$"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    query = db.query(CollectedProfile)
    if task_id:
        query = query.filter(CollectedProfile.task_id == task_id)
    rows = query.order_by(CollectedProfile.collected_at.desc()).all()

    fields = [
        "id", "task_id", "platform", "username", "full_name", "followers",
        "following", "posts_count", "is_private", "is_verified", "is_business",
        "category", "external_url", "public_email", "public_phone",
        "engagement_rate", "collected_at",
    ]

    if fmt == "json":
        data = [{f: getattr(r, f) for f in fields} for r in rows]
        payload = json.dumps(data, ensure_ascii=False, default=str, indent=2)
        return StreamingResponse(
            iter([payload]), media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=social_leaker_export.json"},
        )

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({f: getattr(r, f) for f in fields})
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=social_leaker_export.csv"},
    )


@router.get("/report")
def full_report(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """A complete JSON report: every task with its steps and collected profiles."""
    from datetime import datetime, timezone

    from ..models import Integration, Task, TaskStep

    def prof(r):
        return {c.name: getattr(r, c.name) for c in CollectedProfile.__table__.columns
                if c.name != "raw_json"}

    tasks = db.query(Task).order_by(Task.created_at.desc()).all()
    tasks_out = []
    for t in tasks:
        steps = db.query(TaskStep).filter(TaskStep.task_id == t.id).order_by(TaskStep.seq).all()
        results = db.query(CollectedProfile).filter(CollectedProfile.task_id == t.id).all()
        tasks_out.append({
            "id": t.id, "title": t.title, "prompt": t.prompt, "platform": t.platform,
            "status": t.status.value, "goal_target": t.goal_target, "iterations": t.iterations,
            "collected_count": t.collected_count, "requests_count": t.requests_count,
            "tokens_est": t.tokens_est, "created_at": t.created_at.isoformat(),
            "started_at": t.started_at.isoformat() if t.started_at else None,
            "finished_at": t.finished_at.isoformat() if t.finished_at else None,
            "steps": [{"seq": s.seq, "phase": s.phase, "title": s.title, "detail": s.detail,
                       "status": s.status.value, "at": s.created_at.isoformat()} for s in steps],
            "results": [prof(r) for r in results],
        })

    integ = db.query(Integration).filter(Integration.owner_id == user.id).all()
    payload = {
        "report": "Social Leaker — full export",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_by": user.username,
        "totals": {
            "tasks": len(tasks_out),
            "profiles": db.query(func.count(CollectedProfile.id)).scalar() or 0,
        },
        "integrations": [{"provider": i.provider, "category": i.category, "status": i.status,
                          "label": i.label} for i in integ],
        "tasks": tasks_out,
    }
    return StreamingResponse(
        iter([json.dumps(payload, ensure_ascii=False, default=str, indent=2)]),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=social_leaker_full_report.json"},
    )


@router.get("/stats")
def dashboard_stats(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    total_profiles = db.query(func.count(CollectedProfile.id)).scalar() or 0
    total_reach = db.query(func.coalesce(func.sum(CollectedProfile.followers), 0)).scalar() or 0
    verified = (
        db.query(func.count(CollectedProfile.id))
        .filter(CollectedProfile.is_verified.is_(True)).scalar() or 0
    )

    total_tasks = db.query(func.count(Task.id)).scalar() or 0
    completed = db.query(func.count(Task.id)).filter(Task.status == TaskStatus.completed).scalar() or 0
    running = (
        db.query(func.count(Task.id))
        .filter(Task.status.in_([TaskStatus.running, TaskStatus.queued])).scalar() or 0
    )
    total_requests = db.query(func.coalesce(func.sum(Task.requests_count), 0)).scalar() or 0
    total_tokens = db.query(func.coalesce(func.sum(Task.tokens_est), 0)).scalar() or 0
    total_iterations = db.query(func.coalesce(func.sum(Task.iterations), 0)).scalar() or 0

    # Connected integrations for this user
    integ = db.query(Integration).filter(Integration.owner_id == user.id).all()
    platforms = [
        {"provider": i.provider, "label": i.label, "status": i.status,
         "connected_at": i.connected_at.isoformat() if i.connected_at else None}
        for i in integ if i.category == "platform"
    ]
    claude = next((i for i in integ if i.provider == "claude"), None)

    recent = db.query(Task).order_by(Task.created_at.desc()).limit(6).all()
    recent_out = [
        {"id": t.id, "title": t.title, "status": t.status.value,
         "collected_count": t.collected_count, "goal_target": t.goal_target,
         "iterations": t.iterations, "created_at": t.created_at.isoformat()}
        for t in recent
    ]

    status_rows = db.query(Task.status, func.count(Task.id)).group_by(Task.status).all()
    breakdown = {s.value: c for s, c in status_rows}

    return {
        "profiles": {"total": total_profiles, "verified": verified, "reach": int(total_reach)},
        "tasks": {"total": total_tasks, "completed": completed, "running": running,
                  "breakdown": breakdown},
        "usage": {"requests": int(total_requests), "tokens_est": int(total_tokens),
                  "iterations": int(total_iterations)},
        "connections": {
            "platforms": platforms,
            "platforms_connected": sum(1 for p in platforms if p["status"] == "connected"),
            "claude_connected": bool(claude and claude.status == "connected"),
            "claude_status": claude.status if claude else "disconnected",
        },
        "recent_tasks": recent_out,
    }
