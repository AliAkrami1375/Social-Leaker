"""Claude Code ACP (Agent Client Protocol) bridge.

Spawns Claude Code as an ACP agent (by default the official Zed adapter,
``@zed-industries/claude-code-acp``) and speaks JSON-RPC 2.0 over its stdio.
The client exposes a small async surface used by the web panel to:

    * initialise + create a session,
    * send a natural-language prompt (the campaign objective), and
    * stream agent output back to the browser over a WebSocket.

Reference: https://agentclientprotocol.com  (newline-delimited JSON-RPC 2.0).
"""
from __future__ import annotations

import asyncio
import json
import os
import shlex
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..config import ROOT_DIR, settings

EventFn = Callable[[dict], Awaitable[None]]


class ACPError(RuntimeError):
    pass


class ACPClient:
    """Minimal async ACP client for driving a single Claude Code agent process."""

    PROTOCOL_VERSION = 1

    def __init__(self, on_event: EventFn, cwd: str | None = None,
                 env: dict | None = None) -> None:
        self._on_event = on_event
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 1
        self._session_id: str | None = None
        self._env = env or {}
        cwd = cwd or settings.acp_cwd or "."
        self._cwd = str((ROOT_DIR / cwd).resolve()) if not os.path.isabs(cwd) else cwd
        self._closed = False

    # ── lifecycle ──────────────────────────────────────────────────
    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    def _build_env(self) -> dict:
        """Child environment for the agent process.

        Strips CLAUDECODE-family variables so the adapter does not trip the
        'nested Claude Code session' guard when the panel is itself launched
        from inside a Claude Code session, and layers on any caller overrides
        (e.g. an ANTHROPIC_API_KEY).
        """
        env = os.environ.copy()
        for key in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT"):
            env.pop(key, None)
        env.update({k: v for k, v in self._env.items() if v is not None})
        return env

    async def start(self) -> None:
        cmd = settings.acp_agent_cmd
        env = self._build_env()
        await self._emit("status", f"Launching ACP agent: {cmd}")
        try:
            if os.name == "nt":
                # On Windows the agent is typically a .cmd shim (npx / npm bin),
                # which cannot be run via create_subprocess_exec — use a shell.
                self._proc = await asyncio.create_subprocess_shell(
                    cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=self._cwd,
                    env=env,
                )
            else:
                parts = shlex.split(cmd)
                self._proc = await asyncio.create_subprocess_exec(
                    *parts,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=self._cwd,
                    env=env,
                )
        except FileNotFoundError as exc:
            raise ACPError(
                f"Could not launch ACP agent ({cmd!r}). "
                "Install Node.js and the adapter "
                "(npm i -g @zed-industries/claude-code-acp), or set ACP_AGENT_CMD."
            ) from exc

        self._reader_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._read_stderr())

    async def initialize(self) -> dict:
        result = await self._request(
            "initialize",
            {
                "protocolVersion": self.PROTOCOL_VERSION,
                "clientCapabilities": {
                    "fs": {"readTextFile": True, "writeTextFile": True},
                },
            },
        )
        await self._emit("status", "ACP session initialised.")
        return result

    async def new_session(self) -> str:
        result = await self._request(
            "session/new",
            {"cwd": self._cwd, "mcpServers": []},
        )
        self._session_id = result.get("sessionId")
        await self._emit("status", f"New agent session: {self._session_id}")
        return self._session_id

    async def authenticate(self, method_id: str = "claude-login") -> dict:
        """Trigger the agent's login flow (Claude Code OAuth). For claude-login
        this opens the browser on the host running the agent and returns once
        the user has completed the OAuth sign-in."""
        await self._emit("status", f"Starting Claude Code login ({method_id})…")
        return await self._request("authenticate", {"methodId": method_id})

    async def prompt(self, text: str) -> dict:
        if not self._session_id:
            raise ACPError("No active ACP session; call new_session() first.")
        return await self._request(
            "session/prompt",
            {
                "sessionId": self._session_id,
                "prompt": [{"type": "text", "text": text}],
            },
        )

    async def cancel(self) -> None:
        if self._session_id and self.running:
            try:
                await self._notify("session/cancel", {"sessionId": self._session_id})
            except Exception:
                pass

    async def stop(self) -> None:
        self._closed = True
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        for task in (self._reader_task, self._stderr_task):
            if task:
                task.cancel()

    # ── JSON-RPC plumbing ──────────────────────────────────────────
    async def _request(self, method: str, params: dict) -> dict:
        if not self._proc or not self._proc.stdin:
            raise ACPError("Agent process is not running.")
        msg_id = self._next_id
        self._next_id += 1
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[msg_id] = fut
        await self._send({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params})
        result = await fut
        if isinstance(result, dict) and "error" in result:
            raise ACPError(f"{method} failed: {result['error']}")
        return result

    async def _notify(self, method: str, params: dict) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    async def _send(self, obj: dict) -> None:
        assert self._proc and self._proc.stdin
        data = (json.dumps(obj) + "\n").encode("utf-8")
        self._proc.stdin.write(data)
        await self._proc.stdin.drain()

    async def _read_loop(self) -> None:
        assert self._proc and self._proc.stdout
        while not self._closed:
            line = await self._proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            await self._dispatch(msg)
        await self._emit("status", "Agent process closed the connection.")

    async def _read_stderr(self) -> None:
        assert self._proc and self._proc.stderr
        while not self._closed:
            line = await self._proc.stderr.readline()
            if not line:
                break
            text = line.decode("utf-8", "replace").rstrip()
            if text:
                await self._emit("stderr", text)

    async def _dispatch(self, msg: dict) -> None:
        # Response to one of our requests.
        if "id" in msg and ("result" in msg or "error" in msg):
            fut = self._pending.pop(msg["id"], None)
            if fut and not fut.done():
                if "error" in msg:
                    fut.set_result({"error": msg["error"]})
                else:
                    fut.set_result(msg.get("result") or {})
            return

        method = msg.get("method")
        params = msg.get("params") or {}

        # Server -> client request (needs a response).
        if "id" in msg and method:
            await self._handle_server_request(msg["id"], method, params)
            return

        # Notification.
        if method == "session/update":
            await self._handle_update(params.get("update") or {})
        elif method:
            await self._emit("notify", {"method": method, "params": params})

    async def _handle_server_request(self, req_id: Any, method: str, params: dict) -> None:
        if method == "session/request_permission":
            await self._answer_permission(req_id, params)
        elif method == "fs/read_text_file":
            await self._answer_read_file(req_id, params)
        elif method == "fs/write_text_file":
            await self._answer_write_file(req_id, params)
        else:
            # Unknown request: reply with method-not-found error.
            await self._send(
                {"jsonrpc": "2.0", "id": req_id,
                 "error": {"code": -32601, "message": f"Method not found: {method}"}}
            )

    async def _answer_permission(self, req_id: Any, params: dict) -> None:
        options = params.get("options") or []
        tool = (params.get("toolCall") or {}).get("title") or "a tool"
        if settings.acp_auto_approve and options:
            # Prefer an "allow once" style option, else the first non-reject one.
            chosen = None
            for opt in options:
                kind = (opt.get("kind") or "").lower()
                if "allow" in kind:
                    chosen = opt
                    break
            chosen = chosen or options[0]
            await self._emit("permission", f"Auto-approved: {tool} ({chosen.get('name')})")
            await self._send(
                {"jsonrpc": "2.0", "id": req_id,
                 "result": {"outcome": {"outcome": "selected", "optionId": chosen.get("optionId")}}}
            )
        else:
            await self._emit("permission", f"Denied (auto): {tool}")
            await self._send(
                {"jsonrpc": "2.0", "id": req_id,
                 "result": {"outcome": {"outcome": "cancelled"}}}
            )

    async def _answer_read_file(self, req_id: Any, params: dict) -> None:
        path = params.get("path", "")
        try:
            content = Path(path).read_text(encoding="utf-8", errors="replace")
            line = params.get("line")
            limit = params.get("limit")
            if line is not None:
                lines = content.splitlines()
                start = max(0, int(line) - 1)
                end = start + int(limit) if limit else len(lines)
                content = "\n".join(lines[start:end])
            await self._send({"jsonrpc": "2.0", "id": req_id, "result": {"content": content}})
        except Exception as exc:
            await self._send(
                {"jsonrpc": "2.0", "id": req_id,
                 "error": {"code": -32000, "message": str(exc)}}
            )

    async def _answer_write_file(self, req_id: Any, params: dict) -> None:
        path = params.get("path", "")
        content = params.get("content", "")
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            await self._send({"jsonrpc": "2.0", "id": req_id, "result": None})
        except Exception as exc:
            await self._send(
                {"jsonrpc": "2.0", "id": req_id,
                 "error": {"code": -32000, "message": str(exc)}}
            )

    async def _handle_update(self, update: dict) -> None:
        kind = update.get("sessionUpdate")
        if kind in ("agent_message_chunk", "user_message_chunk", "agent_thought_chunk"):
            content = update.get("content") or {}
            text = content.get("text", "") if isinstance(content, dict) else str(content)
            role = "thought" if kind == "agent_thought_chunk" else (
                "user" if kind == "user_message_chunk" else "agent")
            await self._emit("message", {"role": role, "text": text})
        elif kind == "tool_call":
            await self._emit("tool", {
                "status": update.get("status", "pending"),
                "title": update.get("title", ""),
                "kind": update.get("kind", ""),
                "id": update.get("toolCallId"),
            })
        elif kind == "tool_call_update":
            await self._emit("tool_update", {
                "status": update.get("status"),
                "id": update.get("toolCallId"),
                "title": update.get("title", ""),
            })
        elif kind == "plan":
            await self._emit("plan", {"entries": update.get("entries", [])})
        else:
            await self._emit("update", update)

    # ── event emission ─────────────────────────────────────────────
    async def _emit(self, event_type: str, data: Any) -> None:
        try:
            await self._on_event({"type": event_type, "data": data})
        except Exception:
            pass
