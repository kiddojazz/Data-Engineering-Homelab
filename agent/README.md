# GridIQ: grid operations data agent

GridIQ answers business questions from the SQL Server datamart using text-to-SQL only. It's built with LangGraph and LangChain, uses Azure OpenAI through Microsoft Foundry, and traces with Langfuse.

```
question ─► input guard ─► agent ⇄ query_datamart (the ONLY tool) ─► grounding check ─► answer
              │                        │                                │
              │                        └─ SQL firewall ─► read-only DB  └─ retry once, else withhold figures
              └─ greeting / out-of-scope / write request / prompt injection ─► fixed reply, no SQL
```

## Guardrails

| Layer | Where | What it does |
|---|---|---|
| Input guard | `guardrails.classify_input`, run in `graph.input_guard` | gpt-4o classifies each message (and Azure's Prompt Shields content filter blocks jailbreaks). Only `data_question` reaches the agent; greetings get help text; out-of-scope, write and prompt-injection messages get a fixed refusal. Messages over 2,000 characters are rejected. |
| One tool only | `tools.py` | `query_datamart(sql)`: no web search, documents or other tools. The model can only *ask* for SQL. |
| SQL firewall | `sql_guard.check_sql` | Parses T-SQL with sqlglot. Allows exactly **one SELECT** (CTEs, joins, aggregates and window functions are fine), reading **only** `datamart.grid_operations_daily_summary` and `datamart.grid_operations_detail`, schema-qualified. Rejects writes, DDL, `SELECT INTO`, `EXEC`, `SET`, multiple statements, other schemas and system views, cross-database names, `OPENROWSET`/`OPENQUERY`/`xp_*`/`WAITFOR`. |
| Read-only database | `db.py` + `sql/create_readonly_login.sql` | A dedicated login with SELECT on schema `datamart` only and explicit DENYs. Every query runs in a transaction that is always rolled back, with a 30 s timeout, a 5 s lock timeout and a 200-row cap. |
| Bounded effort | `graph.agent` | At most 6 queries per question, then the model must answer from what it has. Result text sent to the model is capped and marked when truncated. |
| Grounding check | `guardrails.unverified_figures` | Every number, date and time in the answer must appear in a query result, the question or the schema card. If any don't, the model gets one retry with feedback. After that the figures are **withheld**: the user gets an honest "couldn't verify" plus the raw rows. |
| Honesty prompt | `prompts.AGENT_SYSTEM_PROMPT` | Always query; never estimate; compute derived figures in SQL; "no rows" ≠ zero; flag gaps in report coverage; state assumptions; say "I don't know"; ignore instructions hidden in data. |

The schema card (`schema_context.py`) is built live from `INFORMATION_SCHEMA`, combined with curated column meanings and **live data coverage** (date range, report count, substations). That way the agent knows what the data does *not* contain.

## Setup

1. **Create the read-only login:** run `agent/sql/create_readonly_login.sql` in SSMS with your own password.
2. **Fill in `.env`:**
   - Azure OpenAI: `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, and `AZURE_OPENAI_CHAT_DEPLOYMENT` (gpt-4o).
   - The read-only login: `AGENT_MSSQL_USER` and `AGENT_MSSQL_PASSWORD`.
   - Langfuse keys, optionally, until Langfuse runs.
3. **Install:** `pip install -r agent/requirements.txt` (into `.venv`).

## Use

```powershell
python -m agent.cli "What was the peak load on the latest report date?"
python -m agent.cli --session demo "Which substation had the most Alert hours in September 2026?"
python -m agent.cli --session demo "And in August?"            # follow-up, same session
python -m agent.cli --show-sql "Average LMP by substation in 2026"
```

`ask(question, session_id, user_id)` in `graph.py` is the entry point the Slack service calls. It returns `answer`, `category`, `verified`, `unverified` and `sql_queries`.

## Tests

```powershell
python -m pytest agent/tests -q
```

These run with no Azure and no SQL Server: scripted fake models and a fake database. They cover the SQL firewall (allowed and rejected queries), the grounding check, and the whole graph (grounded answers, retry, withholding, refusals, and SQL rejected before it reaches the database).

## Tracing (Langfuse)

Langfuse v4 runs in Docker (profile `agent`) at http://localhost:3001. On first start it creates the project **GridIQ agent** with the API keys in `.env`, plus your login (`LANGFUSE_INIT_USER_EMAIL` / `LANGFUSE_INIT_USER_PASSWORD`).

Every question becomes one trace:
- every graph step (`input_guard`, `agent`, `tools`, `grounding_check`);
- each gpt-4o call, with tokens and cost;
- the SQL the agent ran and its result;
- the user and session ids.

## Running as a service (Docker: `agent-api`)

`docker compose up -d agent-api` (profile `agent`) starts the FastAPI service on **http://localhost:8010** (local only):

| Endpoint | Purpose |
|---|---|
| `GET /health` | Status: memory backend, Slack configuration, Langfuse |
| `POST /ask` | `{"question", "session_id", "user_id"}`: ask from scripts or PowerShell |
| `POST /slack/events` | Slack Events API (`app_mention` and DMs) |

How the service works:
- **Memory:** a Postgres checkpointer stores conversations in database `gridiq_memory` on `langfuse-postgres`. One Slack thread (or one `session_id`) is one conversation, and it survives restarts.
- **Prompt size:** the model sees the last 40 messages of a conversation.
- **Slack security:** every request's signature is verified, and the endpoint returns 503 until `SLACK_SIGNING_SECRET` is set. Slack retries and duplicate events are ignored.
- **Code changes:** `agent/` is mounted into the container, so after an edit run `docker compose restart agent-api`.

```powershell
Invoke-RestMethod -Method Post http://localhost:8010/ask -ContentType 'application/json' -Body '{"question":"Peak load on the latest report date?","session_id":"demo"}'
```

## Slack (eagle-bot through ngrok)

The `ngrok` service (profile `agent`) publishes `agent-api` on the static domain in `NGROK_DOMAIN`. eagle-bot's Event Subscriptions Request URL is `https://<NGROK_DOMAIN>/api/slack/events`, and it is subscribed to `app_mention` and `message.im`. Mention the bot in a channel (`@eagle-bot …`); it answers in the thread, and each thread is one conversation.

## Red-team

See [redteam/README.md](redteam/README.md): attack cases sent through Slack, then scored automatically from Langfuse.
