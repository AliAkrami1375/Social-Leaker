"""Instagram collection engine.

Wraps `instagrapi` when it is installed and configured. When it is not
available (library missing, no credentials, or a login failure) the engine
falls back to a clearly-labelled *sandbox* generator so the panel remains
fully demonstrable without touching Instagram.

Only public profile data is collected, and every request is spaced out by a
randomised delay to respect platform rate limits. This tool is intended for
authorised OSINT, marketing research and analysis of accounts you own or have
permission to study — see DISCLAIMER in the README.
"""
from __future__ import annotations

import hashlib
import json
import random
import time
from dataclasses import dataclass, field
from typing import Callable

from ..config import settings

LogFn = Callable[[str, str], None]  # (level, message)


@dataclass
class ProfileData:
    """Normalised profile record produced by any engine backend."""

    username: str
    full_name: str | None = None
    biography: str | None = None
    followers: int = 0
    following: int = 0
    posts_count: int = 0
    is_private: bool = False
    is_verified: bool = False
    is_business: bool = False
    category: str | None = None
    external_url: str | None = None
    profile_pic_url: str | None = None
    public_email: str | None = None
    public_phone: str | None = None
    engagement_rate: float | None = None
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["raw_json"] = json.dumps(self.raw, ensure_ascii=False)[:20000]
        d.pop("raw", None)
        return d


class BaseEngine:
    sandbox = False

    def __init__(self, log: LogFn | None = None) -> None:
        self._log = log or (lambda level, msg: None)

    def log(self, level: str, msg: str) -> None:
        self._log(level, msg)

    def _throttle(self) -> None:
        delay = random.uniform(settings.scrape_min_delay, settings.scrape_max_delay)
        time.sleep(delay)

    # --- interface ---
    def collect_profile(self, handle: str) -> ProfileData:  # pragma: no cover
        raise NotImplementedError

    def related_handles(self, handle: str, limit: int = 20) -> list[str]:  # pragma: no cover
        raise NotImplementedError

    def search_users(self, query: str, limit: int = 25) -> list[str]:
        """Discover handles by keyword. Default: unsupported (returns none)."""
        return []


class InstagramEngine(BaseEngine):
    """Real backend powered by instagrapi."""

    def __init__(self, log: LogFn | None = None, owner_id: int | None = None) -> None:
        super().__init__(log)
        self._client = None
        self._owner_id = owner_id

    # ---- availability / login -------------------------------------
    @staticmethod
    def library_available() -> bool:
        try:
            import instagrapi  # noqa: F401
            return True
        except Exception:
            return False

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        from instagrapi import Client  # type: ignore

        cl = Client()
        cl.delay_range = [settings.scrape_min_delay, settings.scrape_max_delay]

        # Prefer a per-user session created via the panel's Instagram login
        # (no stored password). Fall back to global credentials, then anonymous.
        import os

        if self._owner_id is not None:
            from .instagram_auth import session_path

            per_user = session_path(self._owner_id)
            if per_user.exists():
                try:
                    cl.load_settings(str(per_user))
                    cl.get_timeline_feed()  # validates the session
                    self.log("info", "Instagram session restored for this account.")
                    self._client = cl
                    return cl
                except Exception as exc:
                    self.log("warn", f"Saved Instagram session invalid ({exc}); "
                                     "reconnect the account in Platforms.")

        session_file = settings.ig_session_file
        if settings.ig_username and settings.ig_password:
            try:
                import os

                if session_file and os.path.exists(session_file):
                    cl.load_settings(session_file)
                    cl.login(settings.ig_username, settings.ig_password)
                    self.log("info", "Instagram session restored from cache.")
                else:
                    cl.login(settings.ig_username, settings.ig_password)
                    if session_file:
                        cl.dump_settings(session_file)
                    self.log("info", "Logged in to Instagram and cached session.")
            except Exception as exc:  # login problems -> surface, let caller fall back
                self.log("error", f"Instagram login failed: {exc}")
                raise
        else:
            self.log(
                "warn",
                "No Instagram credentials configured; running unauthenticated "
                "(most endpoints will be limited).",
            )
        self._client = cl
        return cl

    def collect_profile(self, handle: str) -> ProfileData:
        cl = self._ensure_client()
        self._throttle()
        info = cl.user_info_by_username(handle)  # raises on failure

        followers = int(getattr(info, "follower_count", 0) or 0)
        media = int(getattr(info, "media_count", 0) or 0)
        raw = json.loads(info.model_dump_json()) if hasattr(info, "model_dump_json") else {}

        return ProfileData(
            username=info.username,
            full_name=getattr(info, "full_name", None),
            biography=getattr(info, "biography", None),
            followers=followers,
            following=int(getattr(info, "following_count", 0) or 0),
            posts_count=media,
            is_private=bool(getattr(info, "is_private", False)),
            is_verified=bool(getattr(info, "is_verified", False)),
            is_business=bool(getattr(info, "is_business", False)),
            category=getattr(info, "category", None),
            external_url=str(getattr(info, "external_url", "") or "") or None,
            profile_pic_url=str(getattr(info, "profile_pic_url", "") or "") or None,
            public_email=getattr(info, "public_email", None),
            public_phone=getattr(info, "public_phone_number", None),
            raw=raw,
        )

    def related_handles(self, handle: str, limit: int = 20) -> list[str]:
        cl = self._ensure_client()
        self._throttle()
        try:
            uid = cl.user_id_from_username(handle)
            self._throttle()
            users = cl.user_following(uid, amount=min(limit, settings.scrape_max_items))
            return [u.username for u in users.values()]
        except Exception as exc:
            self.log("warn", f"Could not expand related handles for @{handle}: {exc}")
            return []

    def search_users(self, query: str, limit: int = 25) -> list[str]:
        cl = self._ensure_client()
        self._throttle()
        try:
            users = cl.search_users(query)
            return [u.username for u in users][:limit]
        except Exception as exc:
            self.log("warn", f"Search failed: {exc}")
            return []


class SandboxEngine(BaseEngine):
    """Deterministic placeholder generator used when the real engine is
    unavailable. Every record is explicitly flagged as sandbox data."""

    sandbox = True
    _CATEGORIES = ["Creator", "Public Figure", "Brand", "Blogger", "Photographer", "Musician"]
    _FIRST = ["Aria", "Leo", "Mila", "Noah", "Sara", "Kian", "Nora", "Omid", "Lena", "Reza"]
    _LAST = ["Karimi", "Stone", "Nazari", "Rivera", "Ahmadi", "Vale", "Sadeghi", "Frost"]

    def _seed(self, handle: str) -> random.Random:
        h = int(hashlib.sha256(handle.lower().encode()).hexdigest(), 16)
        return random.Random(h)

    def collect_profile(self, handle: str) -> ProfileData:
        # Small delay so the UI shows realistic progress, without a real network call.
        time.sleep(random.uniform(0.2, 0.6))
        rng = self._seed(handle)
        followers = rng.randint(500, 4_000_000)
        following = rng.randint(80, 4000)
        posts = rng.randint(3, 3500)
        engagement = round(rng.uniform(0.4, 9.5), 2)
        name = f"{rng.choice(self._FIRST)} {rng.choice(self._LAST)}"
        return ProfileData(
            username=handle.lstrip("@"),
            full_name=name,
            biography=f"[SANDBOX] Demo profile for @{handle}. "
            f"{rng.choice(['Travel', 'Food', 'Tech', 'Fashion', 'Fitness'])} content.",
            followers=followers,
            following=following,
            posts_count=posts,
            is_private=rng.random() < 0.2,
            is_verified=followers > 1_000_000 and rng.random() < 0.7,
            is_business=rng.random() < 0.4,
            category=rng.choice(self._CATEGORIES),
            external_url=f"https://example.com/{handle}" if rng.random() < 0.5 else None,
            profile_pic_url=None,
            public_email=f"{handle}@example.com" if rng.random() < 0.3 else None,
            public_phone=None,
            engagement_rate=engagement,
            raw={"sandbox": True, "handle": handle},
        )

    def related_handles(self, handle: str, limit: int = 20) -> list[str]:
        rng = self._seed(handle + "_rel")
        n = min(limit, rng.randint(3, 12))
        return [f"{handle}_rel{i}" for i in range(1, n + 1)]

    def search_users(self, query: str, limit: int = 25) -> list[str]:
        rng = self._seed("search:" + query.lower())
        slug = "".join(c for c in query.lower() if c.isalnum())[:10] or "page"
        n = min(limit, rng.randint(6, 14))
        return [f"{slug}_{i}" for i in range(1, n + 1)]


def _has_instagram_auth(owner_id: int | None) -> bool:
    """True if we have a way to authenticate: a per-user panel session, or
    global credentials in the environment."""
    if settings.ig_username and settings.ig_password:
        return True
    if owner_id is not None:
        from .instagram_auth import session_path
        return session_path(owner_id).exists()
    return False


def build_engine(log: LogFn | None = None, owner_id: int | None = None,
                 platform: str = "instagram") -> BaseEngine:
    """Pick the collection engine for the given platform."""
    platform = (platform or "instagram").lower()

    if platform == "reddit":
        try:
            from .reddit_engine import RedditEngine
            if RedditEngine.available():
                return RedditEngine(log)
        except Exception:
            pass
        return SandboxEngine(log)

    if platform != "instagram":
        # Not implemented yet — use clearly-labelled sandbox rather than wrong data.
        if log:
            log("warn", f"The '{platform}' engine is not implemented yet — using SANDBOX demo data.")
        return SandboxEngine(log)

    # --- Instagram (default) ---
    # 1. Instaloader — real public data, no login (optionally session-boosted).
    # 2. instagrapi  — private API, only when an account is connected.
    # 3. Sandbox     — demo data if nothing else is available.
    try:
        from .instaloader_engine import InstaloaderEngine
        if InstaloaderEngine.available():
            sid = None
            if owner_id is not None:
                from .instagram_auth import read_sessionid
                sid = read_sessionid(owner_id)
            return InstaloaderEngine(log, sessionid=sid)
    except Exception:
        pass

    if InstagramEngine.library_available() and _has_instagram_auth(owner_id):
        return InstagramEngine(log, owner_id=owner_id)

    if log:
        log("warn", "No real Instagram engine available — using SANDBOX demo data.")
    return SandboxEngine(log)
