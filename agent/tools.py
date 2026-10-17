"""The agent's ONLY tool: run one validated, read-only SELECT against the datamart."""

import json

from langchain_core.tools import tool

from .config import Settings, get_settings
from .db import DatabaseNotConfigured, run_readonly_query
from .sql_guard import check_sql


def format_result(columns: list, rows: list, truncated: bool, max_chars: int, max_rows: int) -> str:
    """Compact JSON for the model, cut to max_chars with an explicit note when anything is dropped."""
    payload = {"row_count": len(rows), "columns": columns, "rows": rows}
    if truncated:
        payload["warning"] = (f"Only the first {max_rows} rows were returned; the full result is larger. "
                              "Aggregate in SQL instead of reading raw rows.")
    text = json.dumps(payload, default=str, ensure_ascii=False)
    if len(text) <= max_chars:
        return text
    kept = rows
    while kept and len(text) > max_chars:
        kept = kept[: max(1, len(kept) // 2)] if len(kept) > 1 else []
        payload = {"row_count": len(rows), "rows_shown": len(kept), "columns": columns, "rows": kept,
                   "warning": "Result too large to show in full; only some rows are shown. Aggregate in SQL instead."}
        text = json.dumps(payload, default=str, ensure_ascii=False)
    return text


def sql_error_text(exc: Exception) -> str:
    """pymssql errors arrive as (code, b'message DB-Lib error ...'), sometimes nested; keep code + SQL Server's message."""
    args = exc.args[0] if exc.args and isinstance(exc.args[0], tuple) else exc.args
    if len(args) >= 2 and isinstance(args[0], int):
        raw = args[1].decode(errors="replace") if isinstance(args[1], bytes) else str(args[1])
        return f"error {args[0]}: {raw.split('DB-Lib error')[0].strip()[:500]}"
    return str(exc).split("DB-Lib error")[0].strip()[:500]


def make_query_tool(settings: Settings | None = None):
    settings = settings or get_settings()

    @tool
    def query_datamart(sql: str) -> str:
        """Run ONE read-only T-SQL SELECT against the grid operations datamart and return the rows as JSON.

        Only datamart.grid_operations_daily_summary and datamart.grid_operations_detail can be read,
        always schema-qualified. Returns at most 200 rows. On a rejected or failed query it returns an
        error message explaining what to fix.

        Args:
            sql: a single SELECT statement in T-SQL.
        """
        verdict = check_sql(sql, settings.allowed_tables)
        if not verdict.ok:
            return f"QUERY REJECTED by the SQL guard: {verdict.reason}"
        try:
            result = run_readonly_query(settings, sql)
        except DatabaseNotConfigured as exc:
            return f"QUERY FAILED: {exc}"
        except Exception as exc:  # noqa: BLE001 - the model reads the error and fixes its SQL
            return f"QUERY FAILED: {sql_error_text(exc)}"
        return format_result(result.columns, result.rows, result.truncated,
                             settings.max_result_chars, settings.max_rows)

    return query_datamart
