"""agent-api tests: Slack signature checks, url_verification, retries/duplicates, and /ask (no network)."""

import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from agent import guardrails
from agent.api import create_app
from agent.config import get_settings
from agent.graph import build_graph
from agent.slack_format import answer_blocks, to_mrkdwn
from agent.tests.test_graph import ScriptedChat

SECRET = "test-signing-secret"


def signed(body: dict, secret: str = SECRET, ts: int | None = None) -> tuple:
    raw = json.dumps(body).encode()
    ts = str(ts or int(time.time()))
    sig = "v0=" + hmac.new(secret.encode(), f"v0:{ts}:".encode() + raw, hashlib.sha256).hexdigest()
    return raw, {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig, "Content-Type": "application/json"}


class FakeSlack:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def record(**kwargs):
            self.calls.append((name, kwargs))
            return {"ok": True, "ts": "999.000"}
        return record


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(guardrails, "classify_input",
                        lambda llm, q, h: guardrails.InputDecision(category="greeting_or_help", reason="t"))
    graph = build_graph(get_settings(), chat_llm=ScriptedChat(responses=[]), schema_card_fn=lambda: "(card)")
    slack = FakeSlack()
    client = TestClient(create_app(graph=graph, slack_client=slack, signing_secret=SECRET))
    return client, slack


def test_health(setup):
    client, _ = setup
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["slack"]["signing_secret"] is True


def test_ask_endpoint(setup):
    client, _ = setup
    result = client.post("/ask", json={"question": "hi", "session_id": "s1"}).json()
    assert "GridIQ" in result["answer"] and result["category"] == "greeting_or_help"


def test_rejects_bad_signature(setup):
    client, _ = setup
    raw, headers = signed({"type": "event_callback"}, secret="wrong-secret")
    assert client.post("/slack/events", content=raw, headers=headers).status_code == 401


def test_rejects_stale_timestamp(setup):
    client, _ = setup
    raw, headers = signed({"type": "event_callback"}, ts=int(time.time()) - 3600)
    assert client.post("/slack/events", content=raw, headers=headers).status_code == 401


def test_refuses_without_signing_secret():
    client = TestClient(create_app(graph=object(), slack_client=FakeSlack(), signing_secret=""))
    assert client.post("/slack/events", content=b"{}").status_code == 503


def test_url_verification(setup):
    client, _ = setup
    raw, headers = signed({"type": "url_verification", "challenge": "abc123"})
    assert client.post("/slack/events", content=raw, headers=headers).json() == {"challenge": "abc123"}


def test_mention_is_answered_in_thread_once(setup):
    client, slack = setup
    event = {"type": "event_callback", "event_id": "Ev1",
             "event": {"type": "app_mention", "text": "<@U0BOT> hello", "user": "U1", "channel": "C1", "ts": "100.1"}}
    raw, headers = signed(event)
    assert client.post("/slack/events", content=raw, headers=headers).json() == {"ok": True}
    raw, headers = signed(event)                                       # same event id again (Slack duplicate)
    client.post("/slack/events", content=raw, headers=headers)
    names = [name for name, _ in slack.calls]
    assert names.count("chat_postMessage") == 1 and names.count("chat_update") == 1
    post = dict(slack.calls)["chat_postMessage"]
    assert post["thread_ts"] == "100.1" and post["channel"] == "C1"
    update = [kw for name, kw in slack.calls if name == "chat_update"][0]
    assert "GridIQ" in update["text"]


def test_retries_and_bot_messages_are_ignored(setup):
    client, slack = setup
    raw, headers = signed({"type": "event_callback", "event_id": "Ev2",
                           "event": {"type": "app_mention", "text": "hi", "user": "U1", "channel": "C1", "ts": "1.1"}})
    client.post("/slack/events", content=raw, headers={**headers, "X-Slack-Retry-Num": "1"})
    raw, headers = signed({"type": "event_callback", "event_id": "Ev3",
                           "event": {"type": "message", "channel_type": "im", "bot_id": "B1", "text": "hi",
                                     "channel": "D1", "ts": "2.2"}})
    client.post("/slack/events", content=raw, headers=headers)
    assert slack.calls == []


def test_slack_formatting_and_footer():
    assert to_mrkdwn("**Peak** load\n- one\n### Title") == "*Peak* load\n• one\n*Title*"
    text, blocks = answer_blocks({"answer": "Peak was *702.7 MW*.", "category": "data_question", "verified": True,
                                  "sql_queries": ["SELECT 1"]}, "what was the peak? show the sql")
    assert "702.7" in text
    assert any("```SELECT 1```" in b["text"]["text"] for b in blocks if b["type"] == "section")
    assert "verified" in blocks[-1]["elements"][0]["text"]
