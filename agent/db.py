"""Read-only access to the SQL Server datamart.

Defence in depth (the SQL firewall in sql_guard.py runs before anything reaches here):
  - a dedicated read-only login (db_datareader on schema datamart only)
  - every query runs inside a transaction that is ALWAYS rolled back
  - a query timeout and a hard row cap (extra rows are never fetched)
"""

import datetime as dt
import decimal
from dataclasses import dataclass

import pymssql

from .config import Settings


class DatabaseNotConfigured(RuntimeError):
    pass


@dataclass
class QueryResult:
    columns: list
    rows: list
    truncated: bool          # True when more than max_rows rows existed


def _connect(settings: Settings):
    if not settings.mssql_user or not settings.mssql_password:
        raise DatabaseNotConfigured(
            "AGENT_MSSQL_USER / AGENT_MSSQL_PASSWORD are not set. The agent needs its own read-only "
            "login; see agent/sql/create_readonly_login.sql.")
    return pymssql.connect(server=settings.mssql_host, port=settings.mssql_port, user=settings.mssql_user,
                           password=settings.mssql_password, database=settings.mssql_db, login_timeout=10,
                           timeout=settings.query_timeout_s, autocommit=False, appname="grid-data-agent")


def _plain(value):
    """JSON-friendly cell values (Decimal -> float, dates -> ISO strings)."""
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, bytes):
        return "<binary>"
    return value


def run_readonly_query(settings: Settings, sql: str) -> QueryResult:
    """Executes ONE already-validated SELECT and returns at most settings.max_rows rows."""
    conn = _connect(settings)
    try:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED; SET LOCK_TIMEOUT 5000;")
            cur.execute(sql)
            columns = [c[0] for c in (cur.description or [])]
            fetched = cur.fetchmany(settings.max_rows + 1)
        truncated = len(fetched) > settings.max_rows
        rows = [[_plain(v) for v in row] for row in fetched[: settings.max_rows]]
        return QueryResult(columns=columns, rows=rows, truncated=truncated)
    finally:
        try:
            conn.rollback()   # nothing a SELECT does should ever persist
        finally:
            conn.close()


def run_internal_query(settings: Settings, sql: str, params=None) -> list:
    """For the agent's own fixed metadata queries (schema card, data coverage); never LLM-written."""
    conn = _connect(settings)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [[_plain(v) for v in row] for row in cur.fetchall()]
    finally:
        conn.rollback()
        conn.close()
