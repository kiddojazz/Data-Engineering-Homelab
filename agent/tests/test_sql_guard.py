import pytest

from agent.sql_guard import check_sql

ALLOWED = ("datamart.grid_operations_daily_summary", "datamart.grid_operations_detail")

GOOD = [
    "SELECT MAX(peak_load_mw) AS peak FROM datamart.grid_operations_daily_summary",
    "SELECT TOP (5) report_date, peak_load_mw FROM datamart.grid_operations_daily_summary ORDER BY peak_load_mw DESC",
    """WITH monthly AS (
           SELECT FORMAT(report_date, 'yyyy-MM') AS month, AVG(renewable_share_pct) AS share
           FROM datamart.grid_operations_daily_summary GROUP BY FORMAT(report_date, 'yyyy-MM'))
       SELECT month, ROUND(share, 1) FROM monthly ORDER BY month""",
    """SELECT d.substation, COUNT(*) AS alert_hours
       FROM datamart.grid_operations_detail AS d
       JOIN datamart.grid_operations_daily_summary AS s ON s.source_file = d.source_file
       WHERE d.status = 'Alert' GROUP BY d.substation""",
    """SELECT substation, load_mw, RANK() OVER (PARTITION BY report_date ORDER BY load_mw DESC) AS rnk
       FROM datamart.grid_operations_detail WHERE report_date = '2026-10-02'""",
    """SELECT 'summary' AS t, COUNT(*) FROM datamart.grid_operations_daily_summary
       UNION ALL SELECT 'detail', COUNT(*) FROM datamart.grid_operations_detail""",
    "SELECT DATEPART(HOUR, reading_datetime) AS hr, AVG(lmp_usd_per_mwh) FROM datamart.grid_operations_detail GROUP BY DATEPART(HOUR, reading_datetime)",
    "SELECT COUNT(*) FROM datamart.grid_operations_detail WHERE weather = 'Partly Cloudy' AND status <> 'Operational'",
]

BAD = {
    "insert": "INSERT INTO datamart.grid_operations_daily_summary (source_file) VALUES ('x')",
    "update": "UPDATE datamart.grid_operations_detail SET load_mw = 0",
    "delete": "DELETE FROM datamart.grid_operations_detail",
    "drop": "DROP TABLE datamart.grid_operations_detail",
    "truncate": "TRUNCATE TABLE datamart.grid_operations_detail",
    "merge": "MERGE datamart.grid_operations_detail AS t USING datamart.grid_operations_detail AS s ON 1=1 WHEN MATCHED THEN DELETE;",
    "select_into": "SELECT * INTO datamart.copy FROM datamart.grid_operations_detail",
    "select_into_temp": "SELECT * INTO #tmp FROM datamart.grid_operations_detail",
    "multi_statement": "SELECT 1 FROM datamart.grid_operations_detail; DROP TABLE datamart.grid_operations_detail",
    "comment_smuggle": "SELECT 1 FROM datamart.grid_operations_detail; -- harmless\nDELETE FROM datamart.grid_operations_detail",
    "exec": "EXEC sp_who",
    "xp_cmdshell": "SELECT 1 FROM datamart.grid_operations_detail WHERE 1 = (SELECT 1 FROM OPENROWSET('SQLNCLI', 'x', 'EXEC xp_cmdshell ''dir'''))",
    "openquery": "SELECT * FROM OPENQUERY(linked, 'SELECT 1')",
    "waitfor": "SELECT 1 FROM datamart.grid_operations_detail; WAITFOR DELAY '00:00:10'",
    "other_schema": "SELECT * FROM etl.file_load_log",
    "staging_schema": "SELECT * FROM staging.grid_operations_detail",
    "system_view": "SELECT name FROM sys.tables",
    "info_schema": "SELECT * FROM INFORMATION_SCHEMA.TABLES",
    "unqualified": "SELECT COUNT(*) FROM grid_operations_detail",
    "cross_database": "SELECT * FROM master.datamart.grid_operations_detail",
    "linked_server": "SELECT * FROM srv.MonitoringLab.datamart.grid_operations_detail",
    "join_forbidden": "SELECT d.load_mw FROM datamart.grid_operations_detail d JOIN etl.file_load_log l ON l.source_file = d.source_file",
    "subquery_forbidden": "SELECT * FROM datamart.grid_operations_detail WHERE source_file IN (SELECT source_file FROM etl.file_load_log)",
    "cte_write": "WITH x AS (SELECT 1 AS a) DELETE FROM datamart.grid_operations_detail",
    "grant": "GRANT SELECT ON SCHEMA::etl TO public",
    "set": "SET NOCOUNT ON",
    "no_table": "SELECT 1",
    "empty": "   ",
    "not_sql": "please show me everything",
}


@pytest.mark.parametrize("sql", GOOD)
def test_allows_read_only_queries(sql):
    result = check_sql(sql, ALLOWED)
    assert result.ok, result.reason


@pytest.mark.parametrize("name,sql", BAD.items())
def test_rejects_unsafe_or_out_of_scope_queries(name, sql):
    result = check_sql(sql, ALLOWED)
    assert not result.ok, f"{name} should be rejected"
    assert result.reason


def test_cte_name_is_not_treated_as_a_table():
    sql = "WITH latest AS (SELECT MAX(report_date) AS d FROM datamart.grid_operations_daily_summary) SELECT d FROM latest"
    result = check_sql(sql, ALLOWED)
    assert result.ok, result.reason
    assert result.tables == ("datamart.grid_operations_daily_summary",)
