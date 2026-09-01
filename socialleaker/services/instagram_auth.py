"""Instagram authentication with two-factor and security-challenge handling.

Instagram distinguishes two kinds of extra verification:

  * **Two-factor (2FA)** — only if the account has 2FA enabled. instagrapi
    raises ``TwoFactorRequired``; the login is completed by re-calling
    ``login(..., verification_code=code)``.

  * **Security challenge** — a checkpoint Instagram triggers for *new or
    unfamiliar* logins (new device / IP / VPN), **even without 2FA**. instagrapi
    raises ``ChallengeRequired`` and a one-time code is sent to email / SMS. It
    is resolved through instagrapi's ``challenge_code_handler``, which blocks
    until the code is supplied — so we run it on a background thread and feed the
    code in when the user submits it.

On success the session is written to ``data/ig_session_<user_id>.json`` (no
password stored). Pending logins live in memory keyed by (user_id, username).
Requires the optional ``instagrapi`` dependency (bundled in the Docker image).
"""
from __future__ import annotations

import queue
import threading
import time
from pathlib import Path

from ..config import ROOT_DIR, settings

_LOCK = threading.Lock()
_TTL = 300  # seconds a pending login stays valid


class _Pending:
    def __init__(self, cl, username: str, password: str, kind: str) -> None:
        self.cl = cl
        self.username = username
        self.password = password
        self.kind = kind                       # "2fa" | "challenge"
        self.code_q: "queue.Queue[str]" = queue.Queue(maxsize=1)
        self.thread: threading.Thread | None = None
        self.done = threading.Event()
        self.error: str | None = None
        self.ts = time.time()


_PENDING: dict[tuple[int, str], _Pending] = {}


def _client_cls():
    try:
        from instagrapi import Client  # type: ignore
        return Client
    except Exception:
        return None


def available() -> bool:
    return _client_cls() is not None


def session_path(user_id: int) -> Path:
    p = ROOT_DIR / "data" / f"ig_session_{user_id}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def sessionid_path(user_id: int) -> Path:
    p = ROOT_DIR / "data" / f"ig_sessionid_{user_id}.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def save_sessionid(user_id: int, sessionid: str) -> None:
    try:
        sessionid_path(user_id).write_text(sessionid, encoding="utf-8")
    except Exception:
        pass


def read_sessionid(user_id: int) -> str | None:
    # 1. Raw sessionid file written at connect time.
    try:
        p = sessionid_path(user_id)
        if p.exists():
            v = p.read_text(encoding="utf-8").strip()
            if v:
                return v
    except Exception:
        pass
    # 2. Fall back to the sessionid embedded in a stored instagrapi session,
    #    so an account connected via the private-API path is still usable by
    #    the Instaloader engine. Cache it to the raw file for next time.
    try:
        import json

        sp = session_path(user_id)
        if sp.exists():
            data = json.loads(sp.read_text(encoding="utf-8"))
            sid = (data.get("authorization_data") or {}).get("sessionid")
            if sid:
                save_sessionid(user_id, sid)
                return sid
    except Exception:
        pass
    return None


def _new_client():
    Client = _client_cls()
    cl = Client()
    cl.delay_range = [settings.scrape_min_delay, settings.scrape_max_delay]
    return cl


def _persist(user_id: int, cl) -> None:
    try:
        cl.dump_settings(str(session_path(user_id)))
    except Exception:
        pass


def _is_logged_in(cl) -> bool:
    try:
        return bool(getattr(cl, "user_id", None))
    except Exception:
        return False


def _put(user_id: int, username: str, p: _Pending) -> None:
    with _LOCK:
        _PENDING[(user_id, username)] = p


def _get(user_id: int, username: str) -> _Pending | None:
    with _LOCK:
        return _PENDING.get((user_id, username))


def _pop(user_id: int, username: str) -> None:
    with _LOCK:
        _PENDING.pop((user_id, username), None)


def login_by_session(user_id: int, sessionid: str) -> dict:
    """Authenticate by reusing a browser ``sessionid`` cookie.

    This is the most reliable method: it reuses an already-authenticated
    Instagram web session, so it bypasses the username/password login flow that
    Instagram increasingly blocks with 'out of date' / challenge responses.
    """
    if not available():
        return {"status": "unavailable",
                "message": "instagrapi is not installed. Use the Docker image or "
                           "`pip install instagrapi`."}
    sessionid = (sessionid or "").strip().strip('"')
    # Accept a raw cookie value or a full "sessionid=...;" string.
    if "sessionid=" in sessionid:
        try:
            part = [p for p in sessionid.split(";") if "sessionid=" in p][0]
            sessionid = part.split("sessionid=", 1)[1].strip()
        except Exception:
            pass
    if len(sessionid) < 15:
        return {"status": "error", "message": "That does not look like a valid sessionid."}

    # Preferred: validate with Instaloader (test_login returns the username).
    try:
        import instaloader

        L = instaloader.Instaloader(quiet=True, max_connection_attempts=1)
        L.context._session.cookies.set("sessionid", sessionid, domain=".instagram.com")
        who = L.test_login()
        if who:
            save_sessionid(user_id, sessionid)
            return {"status": "connected", "username": who}
    except Exception:
        pass

    # Fallback: validate with instagrapi and cache its session too.
    if available():
        cl = _new_client()
        try:
            if cl.login_by_sessionid(sessionid) and _is_logged_in(cl):
                _persist(user_id, cl)
                save_sessionid(user_id, sessionid)
                return {"status": "connected", "username": getattr(cl, "username", "") or ""}
        except Exception:
            pass

    return {"status": "error",
            "message": "The sessionid did not authenticate. Copy it fresh from your "
                       "browser while logged in to instagram.com (Application → Cookies → "
                       "sessionid)."}


def start_login(user_id: int, username: str, password: str) -> dict:
    """Begin a login. May require a follow-up code (2FA or security challenge)."""
    if not available():
        return {"status": "unavailable",
                "message": "instagrapi is not installed. Use the Docker image or "
                           "`pip install instagrapi` for real Instagram login."}
    try:
        from instagrapi.exceptions import (  # type: ignore
            TwoFactorRequired, ChallengeRequired, BadPassword,
        )
    except Exception:  # pragma: no cover
        TwoFactorRequired = ChallengeRequired = BadPassword = Exception  # type: ignore

    cl = _new_client()
    try:
        if cl.login(username, password) and _is_logged_in(cl):
            _persist(user_id, cl)
            return {"status": "connected", "username": username}
        return {"status": "error", "message": "Login did not complete."}

    except TwoFactorRequired:
        _put(user_id, username, _Pending(cl, username, password, "2fa"))
        return {"status": "2fa_required",
                "message": "This account has two-factor enabled. Enter the 6-digit "
                           "code from your authenticator app or SMS."}

    except ChallengeRequired:
        # New/unfamiliar login → Instagram sends a security code (email/SMS).
        # Resolve on a background thread that blocks for the code.
        pending = _Pending(cl, username, password, "challenge")

        def code_handler(_username: str, _choice) -> str:
            try:
                return pending.code_q.get(timeout=_TTL)
            except Exception as exc:  # timeout
                raise RuntimeError("No security code was provided in time.") from exc

        cl.challenge_code_handler = code_handler
        try:
            cl.change_password_handler = lambda _u: None  # never rotate the password
        except Exception:
            pass

        def resolve() -> None:
            try:
                cl.challenge_resolve(cl.last_json)
                if not _is_logged_in(cl):
                    try:
                        cl.login(username, password)
                    except Exception:
                        pass
            except Exception as exc:  # noqa: BLE001
                pending.error = str(exc)
            finally:
                pending.done.set()

        t = threading.Thread(target=resolve, name=f"ig-challenge-{user_id}", daemon=True)
        pending.thread = t
        t.start()
        _put(user_id, username, pending)
        return {"status": "challenge_required",
                "message": "Instagram asked for a security code (sent to your email or "
                           "SMS) because this is a new login — this is NOT two-factor. "
                           "Enter that code to continue."}

    except BadPassword:
        return {"status": "error", "message": "Incorrect username or password."}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "message": str(exc)}


def submit_code(user_id: int, username: str, code: str) -> dict:
    """Complete a pending 2FA / challenge login with the received code."""
    pending = _get(user_id, username)
    if not pending:
        return {"status": "error", "message": "No pending login — start again."}
    if time.time() - pending.ts > _TTL:
        _pop(user_id, username)
        return {"status": "error", "message": "The login attempt expired — start again."}

    code = str(code).strip()
    cl = pending.cl

    try:
        if pending.kind == "2fa":
            try:
                cl.login(username, pending.password, verification_code=code)
            except Exception as exc:  # noqa: BLE001
                return {"status": "error", "message": f"Two-factor code rejected: {exc}"}
            if _is_logged_in(cl):
                _persist(user_id, cl)
                _pop(user_id, username)
                return {"status": "connected", "username": username}
            return {"status": "error", "message": "The two-factor code was not accepted."}

        # challenge: hand the code to the waiting background thread.
        try:
            pending.code_q.put_nowait(code)
        except queue.Full:
            pass
        pending.done.wait(timeout=90)

        if _is_logged_in(cl):
            _persist(user_id, cl)
            _pop(user_id, username)
            return {"status": "connected", "username": username}
        return {"status": "error",
                "message": pending.error or "The security code was not accepted. "
                                            "Request a new one and try again."}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "message": str(exc)}


def has_session(user_id: int) -> bool:
    return session_path(user_id).exists()


def clear_session(user_id: int) -> None:
    try:
        session_path(user_id).unlink(missing_ok=True)
        sessionid_path(user_id).unlink(missing_ok=True)
    except Exception:
        pass
    with _LOCK:
        for k in list(_PENDING):
            if k[0] == user_id:
                _PENDING.pop(k, None)
