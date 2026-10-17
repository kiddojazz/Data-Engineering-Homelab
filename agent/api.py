"""GridIQ web service: Slack Events API + a JSON /ask endpoint, backed by the LangGraph agent.

    GET  /health          liveness + what's configured (memory, Slack, Langfuse)
    POST /ask             {"question", "session_id"?, "user_id"?} -> answer, verified, sql_queries
    POST /slack/events    Slack Events API (app_mention + direct messages); also /api/slack/events

Slack handling:
  - every request's signature is verified (requests are refused if SLACK_SIGNING_SECRET is unset)
  - url_verification is answered; Slack retries and duplicate event ids are ignored
  - Slack gets 200 immediately; the answer is produced in the background and posted in the thread
  - one Slack thread = one conversation (session_id = slack-<channel>-<thread_ts>), kept in Postgres

Run locally:  uvicorn agent.api:app --port 8010      In Docker: the `agent-api` service.
"""

import json
import logging
import os
import re
import time
from collections import OrderedDict
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from .graph import ask
from .observability import langfuse_enabled
from .slack_format import answer_blocks

log = logging.getLogger("agent.api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

MENTION = re.compile(r"<@[A-Z0-9]+>")
THINKING = "_Looking into the datamart…_"
FAILED = ("Sorry, something went wrong while answering, and I'd rather not guess. "
          "Please try again in a minute.")


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    session_id: str = Field(default="api", max_length=200)
    user_id: str = Field(default="api", max_length=200)


class _SeenEvents:
    """Remembers recent Slack event ids so an event is never answered twice."""

    def __init__(self, ttl_s: int = 600, size: int = 2000):
        self.ttl_s, self.size, self._seen = ttl_s, size, OrderedDict()

    def first_time(self, event_id: str | None) -> bool:
        if not event_id:
            return True
        now = time.time()
        while self._seen and (now - next(iter(self._seen.values())) > self.ttl_s or len(self._seen) > self.size):
            self._seen.popitem(last=False)
        if event_id in self._seen:
            return False
        self._seen[event_id] = now
        return True


def create_app(graph=None, slack_client=None, signing_secret: str | None = None) -> FastAPI:
    """Builds the app. `graph`, `slack_client` and `signing_secret` are injectable for tests."""
    state = {"graph": graph, "slack": slack_client, "memory": "injected" if graph else None, "pool": None}
    secret = signing_secret if signing_secret is not None else os.environ.get("SLACK_SIGNING_SECRET", "")
    seen = _SeenEvents()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if state["graph"] is None:
            from .config import get_settings
            from .graph import build_graph
            from .memory import postgres_checkpointer

            settings = get_settings()
            checkpointer, pool = postgres_checkpointer()
            state["memory"] = "postgres" if checkpointer else "in-memory (lost on restart)"
            state["pool"] = pool
            state["graph"] = build_graph(settings, checkpointer=checkpointer)
            log.info("GridIQ ready | memory: %s | Langfuse: %s", state["memory"], langfuse_enabled())
        if state["slack"] is None and os.environ.get("SLACK_BOT_TOKEN"):
            from slack_sdk import WebClient
            state["slack"] = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
        yield
        if state["pool"] is not None:
            state["pool"].close()

    app = FastAPI(title="GridIQ agent API", version="1.0.0", lifespan=lifespan)

    @app.get("/health")
    def health():
        return {"status": "ok" if state["graph"] is not None else "starting", "memory": state["memory"],
                "slack": {"bot_token": state["slack"] is not None, "signing_secret": bool(secret)},
                "langfuse": langfuse_enabled()}

    @app.post("/ask")
    async def ask_endpoint(body: AskRequest):
        return await run_in_threadpool(ask, body.question, body.session_id, body.user_id, state["graph"])

    # ------------------------------------------------------------------ Slack ----
    def answer_in_thread(channel: str, message_ts: str, thread_ts: str, user: str, question: str) -> None:
        client = state["slack"]
        try:
            client.reactions_add(channel=channel, timestamp=message_ts, name="eyes")
        except Exception:  # noqa: BLE001 - reactions are cosmetic
            pass
        placeholder = client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=THINKING)
        try:
            result = ask(question, session_id=f"slack-{channel}-{thread_ts}", user_id=f"slack:{user}",
                         graph=state["graph"])
            text, blocks = answer_blocks(result, question)
            client.chat_update(channel=channel, ts=placeholder["ts"], text=text, blocks=blocks)
            done = "white_check_mark" if result.get("verified") else "warning"
        except Exception:  # noqa: BLE001 - always replace the placeholder with something honest
            log.exception("Failed to answer Slack question")
            client.chat_update(channel=channel, ts=placeholder["ts"], text=FAILED)
            done = "x"
        try:
            client.reactions_remove(channel=channel, timestamp=message_ts, name="eyes")
            client.reactions_add(channel=channel, timestamp=message_ts, name=done)
        except Exception:  # noqa: BLE001
            pass

    @app.post("/slack/events")
    @app.post("/api/slack/events")      # eagle-bot's existing Request URL path (from the earlier Argus project)
    async def slack_events(request: Request, background: BackgroundTasks):
        body = await request.body()
        if not secret:
            raise HTTPException(status_code=503, detail="SLACK_SIGNING_SECRET is not configured")
        from slack_sdk.signature import SignatureVerifier
        if not SignatureVerifier(secret).is_valid_request(body, dict(request.headers)):
            raise HTTPException(status_code=401, detail="invalid Slack signature")

        payload = json.loads(body or b"{}")
        if payload.get("type") == "url_verification":
            return {"challenge": payload.get("challenge")}
        if request.headers.get("x-slack-retry-num"):
            return {"ok": True}                         # Slack retried because we were slow; already handled
        if not seen.first_time(payload.get("event_id")):
            return {"ok": True}

        event = payload.get("event") or {}
        if event.get("bot_id") or event.get("subtype"):
            return {"ok": True}                         # ignore bots (incl. ourselves) and edits/joins
        is_mention = event.get("type") == "app_mention"
        is_dm = event.get("type") == "message" and event.get("channel_type") == "im"
        if not (is_mention or is_dm):
            return {"ok": True}
        question = MENTION.sub("", event.get("text", "")).strip()
        if not question or state["slack"] is None:
            return {"ok": True}

        message_ts = event["ts"]
        thread_ts = event.get("thread_ts") or message_ts
        background.add_task(answer_in_thread, event["channel"], message_ts, thread_ts, event.get("user", "?"), question)
        return {"ok": True}

    return app


app = create_app()
