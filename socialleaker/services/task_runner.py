"""Managed, loop-based task execution.

A task carries a natural-language *prompt* describing a goal. The runner works
the goal in a managed loop — plan → collect → observe → reflect → (repeat) —
and does not stop until the goal target is reached, the leads are exhausted, or
the iteration budget runs out. Every step is written to ``task_steps`` so the UI
can render live, step-by-step progress for each task.
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timezone

from ..config import settings
from ..database import SessionLocal
from ..models import CollectedProfile, StepStatus, Task, TaskStatus, TaskStep
from .instagram import ProfileData, build_engine

_CANCEL: dict[int, threading.Event] = {}
_RUNNING: set[int] = set()
_LOCK = threading.Lock()

_STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "from", "into", "list", "full",
    "find", "search", "them", "then", "their", "these", "those", "page", "pages",
    "profile", "profiles", "account", "accounts", "collect", "gather", "please",
    "want", "need", "give", "get", "all", "about", "data", "info", "instagram",
    "follow", "followers", "following", "top", "best", "some", "each", "every",
    "should", "would", "could", "your", "you", "our", "who", "have", "like",
}


def is_running(task_id: int) -> bool:
    with _LOCK:
        return task_id in _RUNNING


def request_cancel(task_id: int) -> bool:
    with _LOCK:
        ev = _CANCEL.get(task_id)
    if ev:
        ev.set()
        return True
    return False


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class _Stepper:
    """Helper that appends ordered TaskStep rows and bumps usage counters."""

    def __init__(self, db, task: Task) -> None:
        self.db = db
        self.task = task
        self.seq = task.iterations  # continue numbering if re-run

    def add(self, phase: str, title: str, detail: str = "",
            status: StepStatus = StepStatus.done, tokens: int = 0) -> TaskStep:
        self.seq += 1
        step = TaskStep(
            task_id=self.task.id, seq=self.seq, phase=phase,
            title=title, detail=detail or None, status=status,
        )
        self.db.add(step)
        if tokens:
            self.task.tokens_est = (self.task.tokens_est or 0) + tokens
        self.db.commit()
        return step

    def finish(self, step: TaskStep, status: StepStatus, detail: str | None = None) -> None:
        step.status = status
        if detail is not None:
            step.detail = detail
        self.db.commit()


def parse_prompt(prompt: str) -> tuple[list[str], int | None]:
    """Extract seed handles and an optional numeric goal from the prompt."""
    # @mentions and instagram.com/<handle> URLs are the strongest signals.
    handles = re.findall(r"@([A-Za-z0-9._]{2,30})", prompt)
    handles += re.findall(r"instagram\.com/([A-Za-z0-9._]{2,30})", prompt, re.I)
    if not handles:
        # Fall back to Latin handle-like tokens that are not common words.
        words = re.findall(r"\b([A-Za-z][A-Za-z0-9._]{2,29})\b", prompt)
        handles = [w for w in words if w.lower() not in _STOPWORDS]
    # de-dup preserving order
    seen: set[str] = set()
    ordered = []
    for h in handles:
        k = h.lower()
        if k not in seen:
            seen.add(k)
            ordered.append(h.lstrip("@"))
    target = None
    m = re.search(r"\b(\d{1,4})\b", prompt)
    if m:
        target = int(m.group(1))
    return ordered[:15], target


# Filler words to drop when turning a free-text prompt into a search query
# (English + common Persian command/filler words).
_QUERY_STOP = {
    "all", "the", "pages", "page", "list", "full", "collect", "find", "get",
    "gather", "of", "in", "on", "for", "and", "profiles", "profile", "please",
    "accounts", "account", "top", "best", "about", "instagram",
    "تمامی", "همه", "پیج", "پیج‌ها", "های", "ها", "رو", "را", "یه", "یک", "این",
    "اون", "لیست", "کامل", "جمع", "کن", "کنید", "بده", "بیار", "برو", "دنبال",
    "بگرد", "تو", "از", "به", "با", "که", "و", "می", "خوام", "میخوام", "کنه",
}


def build_search_query(prompt: str) -> str:
    """Turn a free-text prompt into a concise Instagram search query."""
    text = re.sub(r"https?://\S+", " ", prompt)
    text = re.sub(r"@[\w.]+", " ", text)
    tokens = re.findall(r"\w+", text, re.UNICODE)
    kept = [t for t in tokens if t.lower() not in _QUERY_STOP and len(t) > 1 and not t.isdigit()]
    query = " ".join(kept[:6]).strip()
    return query or prompt.strip()[:60]


def _claude_connected(db, owner_id: int | None) -> bool:
    if owner_id is None:
        return False
    from ..models import Integration
    row = (db.query(Integration)
           .filter(Integration.owner_id == owner_id, Integration.provider == "claude").first())
    return bool(row and row.status == "connected")


def _parse_handle_list(text: str) -> list[str]:
    """Pull a handle list out of Claude's answer (JSON array preferred)."""
    text = (text or "").strip()
    m = re.search(r"\[.*\]", text, re.S)
    if m:
        try:
            arr = json.loads(m.group(0))
            out = [str(x).strip().lstrip("@") for x in arr if str(x).strip()]
            if out:
                return out
        except Exception:
            pass
    return re.findall(r"@?([A-Za-z0-9._]{2,30})", text)


def _parse_discovery(text: str) -> tuple[list[str], list[str]]:
    """Parse Claude's {"handles": [...], "queries": [...]} discovery object."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if m:
        try:
            obj = json.loads(m.group(0))
            handles = [str(x).lstrip("@").strip() for x in (obj.get("handles") or []) if str(x).strip()]
            queries = [str(x).strip() for x in (obj.get("queries") or []) if str(x).strip()]
            if handles or queries:
                return list(dict.fromkeys(handles)), list(dict.fromkeys(queries))
        except Exception:
            pass
    return list(dict.fromkeys(_parse_handle_list(text))), []


def claude_discover(topic: str, platform: str, step, db, owner_id) -> dict:
    """Ask the connected Claude account for public pages AND search partitions.

    Returns {"handles": [...], "queries": [...]} — the queries are sub-topics /
    keyword variations / hashtags used to partition the search so collection is
    comprehensive rather than capped at a single number. Scoped to public
    organisations, brands, media and communities — not private individuals.
    """
    from . import claude_agent
    empty = {"handles": [], "queries": []}
    if not (_claude_connected(db, owner_id) and claude_agent.available()):
        return empty
    ds = step.add("plan", "Discovering with Claude",
                  f"Asking Claude to map public {platform} pages for “{topic}”.",
                  status=StepStatus.running, tokens=1800)
    prompt = (
        f'For the topic "{topic}" on {platform}, help build an authorised, PUBLIC-only '
        f'research list. Return ONLY a JSON object of the form '
        f'{{"handles": [up to 60 public {platform} usernames without @], '
        f'"queries": [10-14 search phrases, sub-topics or hashtags that would surface MORE '
        f'public accounts on this topic]}}. Public organisations, brands, media outlets and '
        f'communities only — no private individuals, no commentary.'
    )
    res = _run_with_timeout(lambda: claude_agent.ask_sync(prompt, timeout=100), 120,
                            {"ok": False, "error": "Claude discovery timed out."})
    if not res.get("ok"):
        step.finish(ds, StepStatus.failed, f"Claude discovery unavailable: {res.get('error')}")
        return empty
    handles, queries = _parse_discovery(res.get("text", ""))
    step.finish(ds, StepStatus.done,
                f"Claude proposed {len(handles)} handle(s) and {len(queries)} search partition(s).")
    return {"handles": handles, "queries": queries}


def _persist(db, task: Task, data: ProfileData) -> None:
    d = data.to_dict()
    profile = CollectedProfile(
        task_id=task.id, platform=task.platform,
        **{k: v for k, v in d.items() if k != "raw_json"},
        raw_json=d.get("raw_json"),
    )
    db.add(profile)
    task.collected_count = (task.collected_count or 0) + 1
    db.commit()


_RATE_LIMIT_MARKERS = (
    "429", "rate limit", "please wait", "wait a few", "too many", "throttl",
    "temporarily", "feedback_required", "try again", "slow down",
)


def _is_rate_limited(exc: Exception) -> bool:
    m = str(exc).lower()
    return any(k in m for k in _RATE_LIMIT_MARKERS)


def _sleep_cancellable(seconds: float, cancel: threading.Event) -> None:
    """Sleep in short slices so a cancel request is honoured promptly."""
    end = time.time() + seconds
    while not cancel.is_set():
        remaining = end - time.time()
        if remaining <= 0:
            break
        time.sleep(min(1.0, remaining))


def _collect_with_timeout(engine, handle: str, timeout: float = 45.0) -> ProfileData:
    """Run a single collection with a hard timeout so a hung network call can
    never block the worker (and therefore the whole task queue)."""
    result: dict = {}

    def work() -> None:
        try:
            result["data"] = engine.collect_profile(handle)
        except Exception as exc:  # noqa: BLE001
            result["err"] = exc

    th = threading.Thread(target=work, daemon=True)
    th.start()
    th.join(timeout)
    if th.is_alive():
        raise TimeoutError(f"collection timed out after {int(timeout)}s")
    if "err" in result:
        raise result["err"]
    return result["data"]


def _run_with_timeout(fn, timeout: float, default):
    """Run fn() in a daemon thread and return its result, or `default` if it does
    not finish in time (the thread is abandoned, so it never blocks the queue)."""
    box = {"v": default}

    def work() -> None:
        try:
            box["v"] = fn()
        except Exception:  # noqa: BLE001
            box["v"] = default

    th = threading.Thread(target=work, daemon=True)
    th.start()
    th.join(timeout)
    return box["v"]


def _collect_frontier(db, task: Task, step: "_Stepper", engine, frontier: list[str],
                      already: set[str], cancel: threading.Event,
                      max_items: int | None = None) -> None:
    """Collect every handle in the frontier, paced and rate-limit aware.

    Resumable: handles already present in ``already`` are skipped, so a task that
    was interrupted and re-queued continues where it stopped. On a throttle the
    loop backs off (exponentially) and retries rather than failing the task.
    """
    seen = {h.lower() for h in already}
    pending = [h for h in frontier if h.lower() not in seen]
    total = len(frontier)
    if not pending:
        step.add("reflect", "Already complete",
                 f"All {total} discovered page(s) are already collected.",
                 status=StepStatus.done)
        return

    cap = max_items or settings.scrape_max_items
    backoff = settings.scrape_backoff_base
    batch = 5
    while pending and not cancel.is_set() and task.collected_count < cap:
        chunk = [pending.pop(0) for _ in range(min(batch, len(pending)))]
        task.iterations = (task.iterations or 0) + 1
        db.commit()
        act = step.add("collect", f"Collecting… {task.collected_count}/{total} done",
                       status=StepStatus.running, tokens=150)
        details: list[str] = []
        for handle in chunk:
            if cancel.is_set():
                break
            if handle.lower() in seen:
                continue
            retries = 0
            while not cancel.is_set():
                try:
                    data = _collect_with_timeout(engine, handle)
                    _persist(db, task, data)
                    task.requests_count += 1
                    db.commit()
                    seen.add(handle.lower())
                    details.append(f"@{data.username} — {data.followers:,} followers"
                                   + (" ✓" if data.is_verified else ""))
                    backoff = settings.scrape_backoff_base  # reset after a success
                    break
                except Exception as exc:  # noqa: BLE001
                    task.requests_count += 1
                    db.commit()
                    if _is_rate_limited(exc) and retries < settings.scrape_retries:
                        retries += 1
                        wait = min(backoff, settings.scrape_backoff_max)
                        step.add("reflect", "Rate limited — pacing",
                                 f"The platform is throttling. Pausing {int(wait)}s, then "
                                 f"retry {retries}/{settings.scrape_retries} for @{handle}.",
                                 status=StepStatus.done)
                        _sleep_cancellable(wait, cancel)
                        backoff = min(backoff * 2, settings.scrape_backoff_max)
                        continue
                    seen.add(handle.lower())  # give up on this handle, keep going
                    details.append(f"@{handle} — skipped: {str(exc)[:80]}")
                    break
        step.finish(act, StepStatus.done,
                    f"{task.collected_count}/{total} collected.\n" + "\n".join(details))


def run_task(task_id: int) -> None:
    """Execute a task's managed loop (intended for a worker thread)."""
    cancel = threading.Event()
    with _LOCK:
        if task_id in _RUNNING:
            return
        _RUNNING.add(task_id)
        _CANCEL[task_id] = cancel

    db = SessionLocal()
    try:
        task = db.get(Task, task_id)
        if not task:
            return
        task.status = TaskStatus.running
        task.started_at = _utcnow()
        task.error_message = None
        db.commit()

        step = _Stepper(db, task)
        engine = build_engine(
            log=lambda lvl, msg: step.add("reflect", "Engine", msg, status=StepStatus.done),
            owner_id=task.owner_id,
            platform=task.platform,
        )

        # ── RESUME STATE ────────────────────────────────────────
        # Which handles are already collected? (idempotent resume after a restart)
        already = {
            (u or "").lower()
            for (u,) in db.query(CollectedProfile.username)
            .filter(CollectedProfile.task_id == task.id).all()
        }
        task.collected_count = len(already)
        db.commit()

        # ── FRONTIER: resume saved plan, else discover ──────────
        frontier: list[str] = []
        if task.frontier_json:
            try:
                frontier = [h.lstrip("@") for h in json.loads(task.frontier_json) if h]
            except Exception:
                frontier = []
            if frontier:
                remaining = [h for h in frontier if h.lower() not in already]
                step.add("plan", "Resuming task",
                         f"Continuing after a restart — {len(already)} collected, "
                         f"{len(remaining)} of {len(frontier)} remaining.",
                         status=StepStatus.done)

        if not frontier:
            s = step.add("plan", "Interpreting the goal", status=StepStatus.running, tokens=420)
            handles, target = parse_prompt(task.prompt)
            # Optional cap only. 0 / blank means "collect everything discovered".
            cap = int(target or task.goal_target or 0)
            if engine.sandbox:
                step.add("plan", "Sandbox mode",
                         "No live credentials — using demo data marked [SANDBOX].",
                         status=StepStatus.done)
            if not handles:
                topic = build_search_query(task.prompt) or task.prompt.strip()
                discovered: list[str] = []

                # 1) Claude proposes handles + search partitions (sub-topics/hashtags).
                disc = claude_discover(topic, task.platform, step, db, task.owner_id)
                discovered += disc.get("handles", [])

                # 2) Partitioned search: the base topic plus each Claude sub-query.
                queries = list(dict.fromkeys([topic] + disc.get("queries", [])))[:14]
                ss = step.add("plan", "Partitioned search",
                              f"Searching {len(queries)} segment(s) on {task.platform} "
                              "to cover the whole topic.", status=StepStatus.running, tokens=200)
                seg_total = 0
                for q in queries:
                    if cancel.is_set():
                        break
                    found = _run_with_timeout(lambda q=q: engine.search_users(q, limit=50), 40, [])
                    if found:
                        discovered += found
                        seg_total += len(found)
                step.finish(ss, StepStatus.done,
                            f"{len(queries)} segment(s) searched — {seg_total} result(s) merged.")

                handles = list(dict.fromkeys(h.lstrip("@") for h in discovered if h.strip()))
                if not handles:
                    fail = ("Could not discover pages. Connect Claude (Settings) for AI-assisted "
                            "discovery, connect an account (Session ID) for keyword search, or add "
                            "seed handles to the task.")
                    step.finish(s, StepStatus.failed, fail)
                    task.status = TaskStatus.failed
                    task.error_message = fail
                    task.finished_at = _utcnow()
                    db.commit()
                    return
            frontier = list(dict.fromkeys(h.lstrip("@") for h in handles if h.strip()))
            task.goal_target = cap  # store the optional cap (0 = all)
            task.frontier_json = json.dumps(frontier, ensure_ascii=False)
            db.commit()
            step.finish(s, StepStatus.done,
                        f"Planned {len(frontier)} page(s)"
                        + (f", cap {cap}" if cap else ", no cap — collect all")
                        + ": " + ", ".join("@" + h for h in frontier[:12])
                        + ("…" if len(frontier) > 12 else ""))

        # ── COLLECT (paced, rate-limit aware, resumable) ────────
        cap = int(task.goal_target or 0)
        max_items = min(cap, settings.scrape_max_items) if cap > 0 else settings.scrape_max_items
        _collect_frontier(db, task, step, engine, frontier, already, cancel, max_items)

        # ── DONE ────────────────────────────────────────────────
        if cancel.is_set():
            task.status = TaskStatus.stopped
            step.add("done", "Stopped by operator",
                     f"Final: {task.collected_count} of {len(frontier)} collected.",
                     status=StepStatus.done)
        else:
            task.status = TaskStatus.completed
            step.add("done", "Task complete ✓",
                     f"Collected {task.collected_count} of {len(frontier)} discovered page(s).",
                     status=StepStatus.done, tokens=120)
        task.finished_at = _utcnow()
        db.commit()

    except Exception as exc:
        db.rollback()
        task = db.get(Task, task_id)
        if task:
            task.status = TaskStatus.failed
            task.error_message = str(exc)
            task.finished_at = _utcnow()
            db.add(TaskStep(task_id=task.id, seq=(task.iterations or 0) + 99,
                            phase="error", title="Task crashed", detail=str(exc),
                            status=StepStatus.failed))
            db.commit()
    finally:
        db.close()
        with _LOCK:
            _RUNNING.discard(task_id)
            _CANCEL.pop(task_id, None)


def start_task_thread(task_id: int) -> None:
    threading.Thread(target=run_task, args=(task_id,), daemon=True).start()
