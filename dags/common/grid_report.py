"""Grid report pipeline helpers shared by its DAG(s): the report-date rule and the datamart status."""

import os
from datetime import datetime, timedelta

# The run's date (or, for a manual run without one, the time it was triggered) minus one day, as YYYY-MM-DD.
REPORT_DATE = "{{ ((logical_date or dag_run.run_after) - macros.timedelta(days=1)) | ds }}"


def report_date(context) -> datetime:
    """Same rule as REPORT_DATE, for Python callbacks."""
    return (context.get("logical_date") or context["dag_run"].run_after) - timedelta(days=1)


def datamart_status(context) -> str:
    """Success-message details: the report date and what datamart holds for it
    (loaded by this run, or already loaded earlier)."""
    import pymssql  # in the Airflow image via the mssql provider

    day = report_date(context)
    with pymssql.connect(server=os.environ["MSSQL_HOST"], port=os.environ.get("MSSQL_PORT", "1433"),
                         user=os.environ["MSSQL_USER"], password=os.environ["MSSQL_PASSWORD"],
                         database=os.environ["MSSQL_DB"], login_timeout=10) as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*), COALESCE(SUM(detail_row_count), 0), MAX(COALESCE(updated_at, loaded_at)) "
                    "FROM datamart.grid_operations_daily_summary WHERE report_date = %s", (day.date(),))
        reports, rows, last_change = cur.fetchone()
    if not reports:
        return f"*Report date:* {day:%Y-%m-%d}\n:large_yellow_circle: no report for this day (nothing on SFTP / in the landing layer)"
    return (f"*Report date:* {day:%Y-%m-%d}\n:white_check_mark: in datamart: {reports} report(s), {rows} detail rows "
            f"(last change {last_change:%Y-%m-%d %H:%M} UTC)")
