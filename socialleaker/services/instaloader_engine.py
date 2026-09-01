"""Instagram collection via Instaloader.

Instaloader belongs to the "logged-out" family: it reads **public** profile
metadata without an account, which sidesteps Instagram's fragile private-API
login (the 'out of date' / Bloks-2FA blocks). An optional ``sessionid`` cookie
raises rate limits and reliability, but is not required for public profiles.

Implements the same ``BaseEngine`` interface as the instagrapi backend, so the
task loop can use either transparently.
"""
from __future__ import annotations

import json

from ..config import settings
from .instagram import BaseEngine, LogFn, ProfileData


class InstaloaderEngine(BaseEngine):
    sandbox = False

    def __init__(self, log: LogFn | None = None, sessionid: str | None = None) -> None:
        super().__init__(log)
        self._sessionid = sessionid
        self._L = None

    @staticmethod
    def available() -> bool:
        try:
            import instaloader  # noqa: F401
            return True
        except Exception:
            return False

    def _loader(self):
        if self._L is not None:
            return self._L
        import instaloader

        L = instaloader.Instaloader(
            quiet=True,
            download_pictures=False, download_videos=False,
            download_video_thumbnails=False, download_comments=False,
            save_metadata=False, compress_json=False,
            max_connection_attempts=2,
        )
        if self._sessionid:
            try:
                L.context._session.cookies.set(
                    "sessionid", self._sessionid, domain=".instagram.com")
                who = None
                try:
                    who = L.test_login()
                except Exception:
                    who = None
                self.log("info", f"Instaloader using saved session"
                                 + (f" (@{who})" if who else "") + ".")
            except Exception:
                self.log("warn", "Could not apply saved session; continuing in public mode.")
        else:
            self.log("info", "Instaloader running in public (no-login) mode.")
        self._L = L
        return L

    def collect_profile(self, handle: str) -> ProfileData:
        import instaloader

        L = self._loader()
        self._throttle()
        try:
            p = instaloader.Profile.from_username(L.context, handle.lstrip("@"))
        except instaloader.exceptions.ProfileNotExistsException as exc:
            raise RuntimeError(f"profile not found") from exc
        except Exception as exc:  # rate limits, 401, connection issues
            raise RuntimeError(str(exc)) from exc

        raw = {
            "userid": getattr(p, "userid", None),
            "is_business_account": bool(getattr(p, "is_business_account", False)),
        }
        return ProfileData(
            username=p.username,
            full_name=getattr(p, "full_name", None),
            biography=getattr(p, "biography", None),
            followers=int(getattr(p, "followers", 0) or 0),
            following=int(getattr(p, "followees", 0) or 0),
            posts_count=int(getattr(p, "mediacount", 0) or 0),
            is_private=bool(getattr(p, "is_private", False)),
            is_verified=bool(getattr(p, "is_verified", False)),
            is_business=bool(getattr(p, "is_business_account", False)),
            category=getattr(p, "business_category_name", None),
            external_url=getattr(p, "external_url", None),
            profile_pic_url=getattr(p, "profile_pic_url", None),
            public_email=getattr(p, "business_email", None) if hasattr(p, "business_email") else None,
            raw=raw,
        )

    def related_handles(self, handle: str, limit: int = 20) -> list[str]:
        """Discovering related accounts needs the private API / login; in public
        mode we do not expand automatically (avoids risky follower scraping)."""
        return []

    def search_users(self, query: str, limit: int = 25) -> list[str]:
        """Keyword discovery. Instagram's search endpoint requires a login, so
        this works only when a sessionid is connected (Platforms → Session ID)."""
        import instaloader

        L = self._loader()
        self._throttle()
        try:
            res = instaloader.TopSearchResults(L.context, query)
            out: list[str] = []
            for prof in res.get_profiles():
                out.append(prof.username)
                if len(out) >= limit:
                    break
            return out
        except Exception as exc:  # 401 when not logged in
            self.log("warn", f"Search needs a connected account (Session ID). Details: {exc}")
            return []
