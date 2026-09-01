"""Claude Code integration via the official Claude Agent SDK.

Uses ``claude-agent-sdk`` (which bundles the Claude Code CLI) so the panel
authenticates through the machine's **Claude Code login** (OAuth) — the same
credentials the ``claude`` CLI uses — rather than a pasted API token.

``verify_connection()`` runs one tiny, benign query to confirm the login works
end-to-end and returns a structured result the panel can act on.
"""
from __future__ import annotations

import os

# We may be launched from inside a Claude Code session (e.g. during development).
# The bundled CLI refuses to start nested sessions unless CLAUDECODE is unset.
for _k in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT"):
    os.environ.pop(_k, None)


def available() -> bool:
    try:
        import claude_agent_sdk  # noqa: F401
        return True
    except Exception:
        return False


def sdk_version() -> str | None:
    try:
        import claude_agent_sdk
        return getattr(claude_agent_sdk, "__version__", None)
    except Exception:
        return None


def _looks_like_auth_error(text: str) -> bool:
    t = (text or "").lower()
    return any(w in t for w in ("login", "log in", "unauthor", "authenticat",
                                "credential", "api key", "401", "403", "not signed in"))


async def verify_connection(cwd: str | None = None) -> dict:
    """Run a minimal query through Claude Code. Returns a structured result:

        {"ok": True,  "reply": "...", "sdk": "0.2.x"}
        {"ok": False, "error": "...", "need_login": bool, "need_install": bool}
    """
    if not available():
        return {"ok": False, "need_install": True,
                "error": "claude-agent-sdk is not installed (pip install claude-agent-sdk)."}

    from claude_agent_sdk import (  # type: ignore
        AssistantMessage, ClaudeAgentOptions, TextBlock, query,
    )
    try:
        from claude_agent_sdk import CLINotFoundError  # type: ignore
    except Exception:  # pragma: no cover
        CLINotFoundError = Exception  # type: ignore

    cwd = cwd or os.path.expanduser("~")
    texts: list[str] = []
    try:
        options = ClaudeAgentOptions(max_turns=1, cwd=cwd)
        async for message in query(prompt="Reply with exactly: CONNECTION_OK", options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        texts.append(block.text)
        reply = "".join(texts).strip()
        if reply:
            return {"ok": True, "reply": reply[:200], "sdk": sdk_version()}
        return {"ok": False, "error": "No response from Claude Code.", "need_login": False}
    except CLINotFoundError as exc:  # type: ignore
        return {"ok": False, "need_install": True, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        return {"ok": False, "error": msg[:400], "need_login": _looks_like_auth_error(msg)}


def ask_sync(prompt: str, cwd: str | None = None, max_turns: int = 4) -> dict:
    """Blocking wrapper around ask(), for use from the worker-thread task loop."""
    import asyncio

    try:
        return asyncio.run(ask(prompt, cwd=cwd, max_turns=max_turns))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:400]}


async def ask(prompt: str, cwd: str | None = None, max_turns: int = 4) -> dict:
    """Run a full agent turn and collect the text response (used for legitimate
    assistance such as summarising or structuring collected data)."""
    if not available():
        return {"ok": False, "error": "claude-agent-sdk not installed."}

    from claude_agent_sdk import (  # type: ignore
        AssistantMessage, ClaudeAgentOptions, TextBlock, query,
    )
    texts: list[str] = []
    try:
        options = ClaudeAgentOptions(max_turns=max_turns, cwd=cwd or os.path.expanduser("~"))
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        texts.append(block.text)
        return {"ok": True, "text": "".join(texts).strip()}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:400]}
