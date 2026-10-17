"""Deterministic SQL firewall: the LLM's SQL only reaches SQL Server if it passes every check.

Allowed: exactly ONE read-only SELECT (CTEs, joins, aggregates, UNION, window functions) that
reads only the allow-listed datamart tables, schema-qualified (datamart.<table>).

Rejected: anything that writes or changes state (INSERT/UPDATE/DELETE/MERGE/SELECT INTO/DDL/
EXEC/SET/...), multiple statements, other schemas or tables, system objects, cross-database or
linked-server names, and dangerous functions (OPENROWSET, OPENQUERY, xp_cmdshell, ...).

Each rejection says why, so the agent can correct its query. This is one of several layers:
the database login should also be read-only (see agent/sql/create_readonly_login.sql).
"""

import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

# Expression types that read data. Anything else at the top level is rejected.
_READ_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Subquery)

# Node types that must not appear anywhere in the tree.
_FORBIDDEN_NODES = tuple(t for t in (
    getattr(exp, name, None) for name in (
        "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "AlterTable", "TruncateTable",
        "Into", "Command", "Execute", "Set", "Grant", "Revoke", "Transaction", "Commit", "Rollback",
        "Use", "Pragma", "LoadData", "Copy", "Kill",
    )) if t is not None)

# Function names that can reach outside the allowed tables or the server.
_FORBIDDEN_FUNCTIONS = {
    "openrowset", "openquery", "opendatasource", "openxml", "openjson_file",
    "xp_cmdshell", "sp_executesql", "sp_configure", "bulk", "waitfor",
}
_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(xp_\w+|sp_\w+|openrowset|openquery|opendatasource|waitfor|shutdown|dbcc|reconfigure|"
    r"bulk\s+insert|into\s+#|exec(ute)?)\b", re.IGNORECASE)


@dataclass(frozen=True)
class GuardResult:
    ok: bool
    reason: str = ""
    tables: tuple = ()


def _reject(reason: str) -> GuardResult:
    return GuardResult(ok=False, reason=reason)


def check_sql(sql: str, allowed_tables: tuple) -> GuardResult:
    """Validates one T-SQL query against the allow-list. Never executes anything."""
    if not sql or not sql.strip():
        return _reject("The query is empty.")
    if len(sql) > 8000:
        return _reject("The query is too long (over 8000 characters). Simplify it.")
    if _FORBIDDEN_KEYWORDS.search(sql):
        return _reject("The query uses a forbidden keyword or procedure (EXEC, xp_/sp_ procedures, "
                       "OPENROWSET/OPENQUERY, WAITFOR, DBCC, BULK, SELECT INTO). Only plain SELECT is allowed.")

    try:
        statements = [s for s in sqlglot.parse(sql, read="tsql") if s is not None]
    except ParseError as exc:
        return _reject(f"The query could not be parsed as T-SQL: {str(exc).splitlines()[0][:200]}")
    if len(statements) != 1:
        return _reject(f"Exactly one statement is allowed; found {len(statements)}. Remove extra statements and semicolons.")

    tree = statements[0]
    if not isinstance(tree, _READ_ROOTS):
        return _reject(f"Only SELECT queries are allowed; got {tree.key.upper()}.")

    for node in tree.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            return _reject(f"The query contains a forbidden operation ({node.key.upper()}). Only read-only SELECT is allowed.")
        if isinstance(node, (exp.Anonymous, exp.Func)):
            name = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()
            if name in _FORBIDDEN_FUNCTIONS:
                return _reject(f"The function {name.upper()} is not allowed.")

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    allowed = {t.lower() for t in allowed_tables}
    used = set()
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        schema = (table.db or "").lower()
        if table.catalog:
            return _reject(f"Cross-database names are not allowed ({table.sql(dialect='tsql')}). "
                           f"Use {' or '.join(allowed_tables)} only.")
        if not schema and name in cte_names:
            continue  # a reference to a CTE defined in this query
        if not schema:
            return _reject(f"Table '{table.name}' must be schema-qualified. Use {' or '.join(allowed_tables)}.")
        full = f"{schema}.{name}"
        if full not in allowed:
            return _reject(f"Table '{full}' is not allowed. Only these tables can be queried: {', '.join(allowed_tables)}.")
        used.add(full)

    if not used:
        return _reject(f"The query must read from {' or '.join(allowed_tables)}.")
    return GuardResult(ok=True, tables=tuple(sorted(used)))
