"""Daily grid report pipeline: SFTP -> Azure File Share (landing) -> SQL Server (datamart).

    sftp_to_azure_fileshare  >>  fileshare_etl_sql_server

Runs at 06:00 UTC and processes the PREVIOUS day's report. Both tasks get the same report
date from the DAG run, the run's date minus one day, so clearing or re-running an old
run reprocesses that run's day, not "yesterday" relative to today.

Each task stops cleanly (success) when there is no report for the day, and both are
idempotent: a re-run skips what is already landed or loaded.

Upstream (not part of this DAG): the report must already be on the SFTP server, e.g.
    python -m pdf_generator.grid_reports.generate_report
    python -m jobs.migrate_daily_grid_report_to_sftp

Slack (dags/common/slack_notifications.py): a failure alert per task after its last retry,
and one success message per run with what's in datamart for the report date.
"""

from datetime import datetime, timedelta

from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator
from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import dag

from common.grid_report import REPORT_DATE, datamart_status
from common.slack_notifications import slack_notifications


@slack_notifications(report_date=REPORT_DATE, success_details=datamart_status)
@dag(
    dag_id="grid_report_daily",
    description="SFTP -> Azure File Share -> SQL Server datamart, previous day's grid report",
    start_date=datetime(2026, 10, 1),
    schedule="0 6 * * *",
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=5)},
    tags=["grid_report", "sftp", "azure", "sqlserver"],
    doc_md=__doc__,
)
def grid_report_daily():
    sftp_to_azure_fileshare = BashOperator(
        task_id="sftp_to_azure_fileshare",
        bash_command=f"python /opt/spark-jobs/land_daily_grid_report_sftp_to_azure.py --date {REPORT_DATE}",
    )

    fileshare_etl_sql_server = SparkSubmitOperator(
        task_id="fileshare_etl_sql_server",
        conn_id="spark_default",
        application="/opt/spark-jobs/grid_report_etl_to_sqlserver.py",
        application_args=["--date", REPORT_DATE],
        name="grid_report_etl_to_sqlserver",
    )

    sftp_to_azure_fileshare >> fileshare_etl_sql_server


grid_report_daily()
