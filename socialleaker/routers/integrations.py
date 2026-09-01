"""Integrations: connect platforms (Instagram) and the Claude agent account."""
from __future__ import annotations

import asyncio
import json
import shutil
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import get_current_user, require_operator
from ..models import Integration, User
from ..schemas import IntegrationOut, PlatformConnectIn

router = APIRouter(prefix="/api/integrations", tags=["integrations"])

# Catalogue of platforms the panel knows about.
#   available=True  → a real collection engine exists.
#   needs_auth      → collection/search needs a connected session.
PLATFORM_CATALOG = [
    {"provider": "instagram", "name": "Instagram", "available": True, "auth": "session",
     "note": "Public profiles (no login); search needs a session."},
    {"provider": "reddit", "name": "Reddit", "available": True, "auth": "none",
     "note": "Public user & subreddit data via Reddit's open JSON."},
    {"provider": "youtube", "name": "YouTube", "available": False, "auth": "none",
     "note": "Channel stats — planned."},
    {"provider": "tiktok", "name": "TikTok", "available": False, "auth": "session",
     "note": "Creator metrics — in progress."},
    {"provider": "x", "name": "X · Twitter", "available": False, "auth": "session",
     "note": "Accounts & reach — in progress."},
    {"provider": "linkedin", "name": "LinkedIn", "available": False, "auth": "session",
     "note": "Companies & people — planned."},
    {"provider": "facebook", "name": "Facebook", "available": False, "auth": "session",
     "note": "Pages & groups — planned."},
    {"provider": "threads", "name": "Threads", "available": False, "auth": "session",
     "note": "Profiles — planned."},
    {"provider": "telegram", "name": "Telegram", "available": False, "auth": "none",
     "note": "Public channels — planned."},
    {"provider": "pinterest", "name": "Pinterest", "available": False, "auth": "none",
     "note": "Boards & pins — planned."},
    {"provider": "mastodon", "name": "Mastodon", "available": False, "auth": "none",
     "note": "Fediverse — planned."},
    {"provider": "snapchat", "name": "Snapchat", "available": False, "auth": "session",
     "note": "Spotlight — planned."},
]

# Where "Connect with Claude" sends the user to obtain an access token.
CLAUDE_LOGIN_URL = "https://claude.ai/login"
CLAUDE_TOKEN_URL = "https://console.anthropic.com/settings/keys"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _token_path(user_id: int):
    from ..config import ROOT_DIR
    p = ROOT_DIR / "data" / f"claude_token_{user_id}.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _store_token(user_id: int, token: str) -> None:
    try:
        _token_path(user_id).write_text(token, encoding="utf-8")
    except Exception:
        pass


def _clear_token(user_id: int) -> None:
    try:
        _token_path(user_id).unlink(missing_ok=True)
    except Exception:
        pass


async def _verify_claude_token(token: str) -> tuple[bool, dict]:
    """Verify a token against the Anthropic API (works for API keys and OAuth tokens)."""
    import httpx

    variants = [
        {"x-api-key": token, "anthropic-version": "2023-06-01"},
        {"authorization": f"Bearer {token}", "anthropic-version": "2023-06-01"},
    ]
    last = "verification failed"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            for headers in variants:
                try:
                    r = await client.get("https://api.anthropic.com/v1/models", headers=headers)
                    if r.status_code == 200:
                        data = r.json()
                        return True, {"models": len(data.get("data", []))}
                    last = f"HTTP {r.status_code}"
                except Exception as exc:  # noqa: BLE001
                    last = str(exc)
    except Exception as exc:  # noqa: BLE001
        last = str(exc)
    return False, {"error": last}


def _get(db: Session, user: User, provider: str) -> Integration | None:
    return (
        db.query(Integration)
        .filter(Integration.owner_id == user.id, Integration.provider == provider)
        .first()
    )


@router.get("", response_model=list[IntegrationOut])
def list_integrations(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return db.query(Integration).filter(Integration.owner_id == user.id).all()


@router.get("/catalog")
def catalog(user: User = Depends(get_current_user)):
    return {"platforms": PLATFORM_CATALOG}


# ── Platform connections ────────────────────────────────────────────
def _upsert(db: Session, user: User, provider: str) -> Integration:
    row = _get(db, user, provider)
    if not row:
        row = Integration(owner_id=user.id, category="platform", provider=provider)
        db.add(row)
    return row


@router.post("/platform/connect")
def connect_platform(payload: PlatformConnectIn, db: Session = Depends(get_db),
                     user: User = Depends(require_operator)):
    """Connect a platform. For Instagram this performs a real, 2FA-aware login."""
    known = {p["provider"]: p for p in PLATFORM_CATALOG}
    meta = known.get(payload.provider)
    if not meta:
        raise HTTPException(400, "Unknown platform")
    if not meta["available"]:
        raise HTTPException(400, f"{meta['name']} is not available yet")

    row = _upsert(db, user, payload.provider)

    # Public mode (no credentials): limited, but works without login.
    if not payload.username or not payload.password:
        row.status = "connected"
        row.label = meta["name"] + " (public)"
        row.secret_masked = None
        row.meta_json = json.dumps({"mode": "public"})
        row.connected_at = _utcnow()
        db.commit()
        return {"status": "connected", "mode": "public"}

    if payload.provider == "instagram":
        from ..services import instagram_auth
        result = instagram_auth.start_login(user.id, payload.username, payload.password)
        status = result.get("status")
        row.label = f"@{payload.username}"
        row.secret_masked = "•" * 6 + payload.password[-2:]
        if status == "connected":
            row.status = "connected"
            row.meta_json = json.dumps({"mode": "authenticated", "username": payload.username})
            row.connected_at = _utcnow()
        elif status in ("2fa_required", "challenge_required"):
            row.status = "pending"
            row.meta_json = json.dumps({"username": payload.username, "await": status})
        else:
            row.status = "disconnected"
            row.meta_json = json.dumps({"username": payload.username, "error": result.get("message")})
        db.commit()
        return result

    # Other authenticated platforms: not implemented yet.
    raise HTTPException(400, "Authenticated login for this platform is not available yet")


@router.post("/platform/instagram/session")
def instagram_session(sessionid: str = Body(..., embed=True), db: Session = Depends(get_db),
                      user: User = Depends(require_operator)):
    """Connect Instagram by reusing a browser sessionid cookie (most reliable)."""
    from ..services import instagram_auth

    result = instagram_auth.login_by_session(user.id, sessionid)
    row = _upsert(db, user, "instagram")
    if result.get("status") == "connected":
        uname = result.get("username") or "account"
        row.status = "connected"
        row.label = f"@{uname}"
        row.secret_masked = "session"
        row.meta_json = json.dumps({"mode": "session", "username": uname})
        row.connected_at = _utcnow()
    db.commit()
    return result


@router.post("/platform/instagram/verify")
def instagram_verify(code: str = Body(..., embed=True), db: Session = Depends(get_db),
                     user: User = Depends(require_operator)):
    """Submit the 2FA / challenge code to finish an Instagram login."""
    from ..services import instagram_auth

    row = _get(db, user, "instagram")
    meta = json.loads(row.meta_json) if row and row.meta_json else {}
    username = meta.get("username")
    if not row or not username:
        raise HTTPException(400, "No pending Instagram login. Start again.")

    result = instagram_auth.submit_code(user.id, username, code)
    if result.get("status") == "connected":
        row.status = "connected"
        row.meta_json = json.dumps({"mode": "authenticated", "username": username})
        row.connected_at = _utcnow()
    db.commit()
    return result


# ── Claude agent connection (real ACP handshake) ────────────────────
@router.get("/claude/status")
def claude_status(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from ..services import claude_agent

    row = _get(db, user, "claude")
    meta = json.loads(row.meta_json) if row and row.meta_json else {}
    return {
        "connected": bool(row and row.status == "connected"),
        "status": row.status if row else "disconnected",
        "label": row.label if row else None,
        "method": meta.get("method"),
        "connected_at": row.connected_at.isoformat() if row and row.connected_at else None,
        "sdk_available": claude_agent.available(),
        "sdk_version": claude_agent.sdk_version(),
        "last_error": meta.get("last_error"),
    }


@router.post("/claude/login")
async def claude_login(db: Session = Depends(get_db), user: User = Depends(require_operator)):
    """Log in with Claude Code (OAuth) via the official Claude Agent SDK.

    Runs one minimal query through the bundled Claude Code CLI, which uses your
    Claude Code login credentials — no API key or pasted token needed.
    """
    from ..services import claude_agent

    result = await claude_agent.verify_connection()

    row = _get(db, user, "claude")
    if not row:
        row = Integration(owner_id=user.id, category="agent", provider="claude")
        db.add(row)

    if result.get("ok"):
        row.status = "connected"
        row.label = "Claude Code (OAuth login)"
        row.connected_at = _utcnow()
        row.meta_json = json.dumps({"method": "claude-code-oauth", "sdk": result.get("sdk")})
    else:
        row.status = "disconnected"
        row.meta_json = json.dumps({"method": "claude-code-oauth",
                                    "last_error": result.get("error")})
    db.commit()
    return result


@router.post("/{provider}/disconnect")
def disconnect(provider: str, db: Session = Depends(get_db), user: User = Depends(require_operator)):
    row = _get(db, user, provider)
    if not row:
        raise HTTPException(404, "Not connected")
    row.status = "disconnected"
    row.connected_at = None
    db.commit()
    if provider == "instagram":
        from ..services import instagram_auth
        instagram_auth.clear_session(user.id)
    if provider == "claude":
        _clear_token(user.id)
    return {"ok": True}
