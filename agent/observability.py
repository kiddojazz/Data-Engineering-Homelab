"""Langfuse tracing (optional): enabled when LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY are set.

Each run becomes one Langfuse trace with every LLM call (tokens, cost), the tool calls (the SQL and
its result) and the graph nodes, tagged with the Slack user and thread as user/session ids.
"""

import logging
import os

log = logging.getLogger(__name__)
_handler = None


def langfuse_enabled() -> bool:
    return bool(os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"))


def tracing_config(user_id: str | None, session_id: str | None, tags: list | None = None) -> dict:
    """Extra RunnableConfig entries (callbacks + metadata) for one agent run."""
    global _handler
    if not langfuse_enabled():
        return {}
    try:
        if _handler is None:
            from langfuse.langchain import CallbackHandler
            _handler = CallbackHandler()
    except Exception as exc:  # noqa: BLE001 - tracing must never break the agent
        log.warning("Langfuse disabled: %s", exc)
        return {}
    return {
        "callbacks": [_handler],
        "metadata": {
            "langfuse_user_id": user_id or "anonymous",
            "langfuse_session_id": session_id or "no-session",
            "langfuse_tags": ["grid-data-agent", *(tags or [])],
        },
    }


def flush() -> None:
    if not langfuse_enabled():
        return
    try:
        from langfuse import get_client
        get_client().flush()
    except Exception:  # noqa: BLE001
        pass
