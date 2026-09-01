"""Reddit collection via Reddit's public JSON endpoints.

Reddit exposes user and subreddit data as public JSON (``/user/<name>/about.json``)
with no login required, which makes it a clean second platform for the same
task loop. Only public data is read, with a descriptive User-Agent as Reddit asks.
"""
from __future__ import annotations

import json

from ..config import settings
from .instagram import BaseEngine, LogFn, ProfileData

_UA = "social-leaker/1.0 (research; +https://dibachain.ir)"
_BASE = "https://www.reddit.com"


class RedditEngine(BaseEngine):
    sandbox = False

    def __init__(self, log: LogFn | None = None) -> None:
        super().__init__(log)
        self._client = None

    @staticmethod
    def available() -> bool:
        try:
            import httpx  # noqa: F401
            return True
        except Exception:
            return False

    def _http(self):
        if self._client is None:
            import httpx
            self._client = httpx.Client(headers={"User-Agent": _UA}, timeout=15,
                                        follow_redirects=True)
        return self._client

    def collect_profile(self, handle: str) -> ProfileData:
        import httpx

        handle = handle.lstrip("@").lstrip("u/").strip("/")
        client = self._http()
        self._throttle()
        r = client.get(f"{_BASE}/user/{handle}/about.json")
        if r.status_code == 404:
            raise RuntimeError("user not found")
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        d = (r.json() or {}).get("data") or {}
        sub = d.get("subreddit") or {}
        followers = int(sub.get("subscribers") or 0) or int(d.get("total_karma") or 0)
        return ProfileData(
            username=d.get("name", handle),
            full_name=sub.get("title") or d.get("name"),
            biography=sub.get("public_description") or None,
            followers=followers,
            following=0,
            posts_count=int(d.get("link_karma") or 0),
            is_private=(sub.get("subreddit_type") == "private"),
            is_verified=bool(d.get("verified")),
            is_business=bool(d.get("is_employee")),
            category="Reddit user",
            external_url=None,
            profile_pic_url=(d.get("icon_img") or "").split("?")[0] or None,
            raw={"total_karma": d.get("total_karma"), "comment_karma": d.get("comment_karma"),
                 "created_utc": d.get("created_utc"), "is_gold": d.get("is_gold")},
        )

    def related_handles(self, handle: str, limit: int = 20) -> list[str]:
        return []

    def search_users(self, query: str, limit: int = 25) -> list[str]:
        client = self._http()
        self._throttle()
        try:
            r = client.get(f"{_BASE}/search.json",
                           params={"q": query, "type": "user", "limit": min(limit, 25)})
            if r.status_code != 200:
                self.log("warn", f"Reddit search HTTP {r.status_code}")
                return []
            children = ((r.json() or {}).get("data") or {}).get("children") or []
            out = []
            for c in children:
                name = (c.get("data") or {}).get("name")
                if name:
                    out.append(name)
                if len(out) >= limit:
                    break
            return out
        except Exception as exc:  # noqa: BLE001
            self.log("warn", f"Reddit search failed: {exc}")
            return []
