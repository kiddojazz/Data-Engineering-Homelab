"""Persistent conversation memory: a LangGraph Postgres checkpointer.

Each conversation (a Slack thread, or a `session_id` on /ask) is a LangGraph thread whose
messages survive restarts. It uses its own database (`gridiq_memory`, created if missing) on
the Langfuse Postgres server, so no extra container is needed.
"""

import logging
import os

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

log = logging.getLogger(__name__)


def memory_db_settings() -> dict | None:
    host = os.environ.get("AGENT_MEMORY_DB_HOST")
    password = os.environ.get("AGENT_MEMORY_DB_PASSWORD")
    if not host or not password:
        return None
    return {
        "host": host,
        "port": os.environ.get("AGENT_MEMORY_DB_PORT", "5432"),
        "user": os.environ.get("AGENT_MEMORY_DB_USER", "langfuse"),
        "password": password,
        "dbname": os.environ.get("AGENT_MEMORY_DB_NAME", "gridiq_memory"),
        "admin_db": os.environ.get("AGENT_MEMORY_DB_ADMIN_DB", "langfuse"),
    }


def _conninfo(cfg: dict, dbname: str) -> str:
    return psycopg.conninfo.make_conninfo(host=cfg["host"], port=cfg["port"], user=cfg["user"],
                                          password=cfg["password"], dbname=dbname, connect_timeout=10)


def ensure_database(cfg: dict) -> None:
    """Creates the memory database on first start (CREATE DATABASE can't run in a transaction)."""
    with psycopg.connect(_conninfo(cfg, cfg["admin_db"]), autocommit=True) as conn:
        if conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (cfg["dbname"],)).fetchone() is None:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(cfg["dbname"])))
            log.info("Created memory database %s", cfg["dbname"])


def postgres_checkpointer():
    """Returns (checkpointer, pool) or (None, None) when no memory database is configured."""
    cfg = memory_db_settings()
    if cfg is None:
        return None, None
    from langgraph.checkpoint.postgres import PostgresSaver

    ensure_database(cfg)
    pool = ConnectionPool(conninfo=_conninfo(cfg, cfg["dbname"]), min_size=1, max_size=5, open=True,
                          kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row})
    saver = PostgresSaver(pool)
    saver.setup()            # creates the checkpoint tables if they don't exist
    return saver, pool
