"""Reusable Slack notifications for DAGs, applied with a decorator.

    from airflow.sdk import dag
    from common.slack_notifications import slack_notifications

    @slack_notifications()            # failure alert per task + one success message per run
    @dag(dag_id="my_pipeline", ...)
    def my_pipeline():
        ...

    my_pipeline()

Options:
    on_failure=True       one message per task that fails after its last retry (task, error, log link)
    on_success=True       one message when the whole DAG run succeeds
    report_date=None      Jinja template for a business date to show in the failure message
    success_details=None  callable(context) -> str, extra lines for the success message

Settings (Airflow container environment, from the project's .env):
    AIRFLOW_CONN_SLACK_ALERTS   Slack connection with the bot token (xoxb-...); built from SLACK_BOT_TOKEN
    SLACK_ALERT_CHANNEL         channel to post in; the bot needs chat:write and must be invited (/invite @bot)
    AIRFLOW__API__BASE_URL      public Airflow UI address, used for the "Open the run" link
Sending never raises: a Slack problem is logged and doesn't change the task or DAG result.
"""

import functools
import logging
import os
from datetime import datetime, timezone

from airflow.providers.slack.notifications.slack import SlackNotifier

SLACK_CONN_ID = "slack_alerts"
SLACK_CHANNEL = os.environ.get("SLACK_ALERT_CHANNEL", "#homelab-alerts")
SLACK_USERNAME = "Homelab Airflow"
AIRFLOW_UI = os.environ.get("AIRFLOW__API__BASE_URL", "http://localhost:8088")

log = logging.getLogger(__name__)


def post_message(text: str) -> None:
    """Posts to SLACK_CHANNEL with the slack_alerts bot connection."""
    from airflow.providers.slack.hooks.slack import SlackHook

    SlackHook(slack_conn_id=SLACK_CONN_ID).client.chat_postMessage(
        channel=SLACK_CHANNEL, text=text, username=SLACK_USERNAME, unfurl_links=False)


def failure_notifier(report_date: str | None = None) -> SlackNotifier:
    """Task-level failure alert (Jinja-rendered with the failed task's context)."""
    date_part = f"   *Report date:* {report_date}" if report_date else ""
    return SlackNotifier(
        slack_conn_id=SLACK_CONN_ID,
        channel=SLACK_CHANNEL,
        username=SLACK_USERNAME,
        unfurl_links=False,   # the log link points at localhost; no preview
        text=(
            ":red_circle: *Airflow task failed*\n"
            "*DAG:* `{{ dag.dag_id }}`   *Task:* `{{ ti.task_id }}`" + date_part + "\n"
            "*Run:* `{{ dag_run.run_id }}`   *Attempt:* {{ ti.try_number }}\n"
            "*Error:* {{ (exception | string)[:500] if exception else 'see log' }}\n"
            "<{{ ti.log_url }}|Open the task log>"
        ),
    )


def success_callback(details=None):
    """DAG-level success callback: one message per successful run, plus optional pipeline details."""
    def _on_success(context) -> None:
        try:
            dag_run = context["dag_run"]
            started = dag_run.start_date
            took = f"{(datetime.now(timezone.utc) - started).total_seconds() / 60:.1f} min" if started else "n/a"
            lines = [
                ":large_green_circle: *Pipeline succeeded*",
                f"*DAG:* `{dag_run.dag_id}`   *Run:* `{dag_run.run_id}`   *Took:* {took}",
            ]
            if details:
                try:
                    lines.append(details(context))
                except Exception as exc:  # noqa: BLE001 - still send the message
                    lines.append(f":grey_question: details unavailable ({str(exc).splitlines()[0][:120]})")
            lines.append(f"<{AIRFLOW_UI}/dags/{dag_run.dag_id}/runs/{dag_run.run_id}|Open the run>")
            post_message("\n".join(lines))
        except Exception:  # noqa: BLE001 - never affect the DAG result
            log.exception("Slack success notification failed")
    return _on_success


def slack_notifications(*, on_failure: bool = True, on_success: bool = True,
                        report_date: str | None = None, success_details=None):
    """Decorator for a @dag-decorated function: adds the Slack failure alert to every task
    (keeping any callbacks a task already has) and the success message to the DAG."""
    def decorator(dag_factory):
        @functools.wraps(dag_factory)
        def build(*args, **kwargs):
            dag = dag_factory(*args, **kwargs)
            if on_failure:
                notifier = failure_notifier(report_date)
                for task in dag.tasks:
                    existing = task.on_failure_callback or []
                    existing = list(existing) if isinstance(existing, (list, tuple)) else [existing]
                    task.on_failure_callback = [*existing, notifier]
            if on_success:
                dag.on_success_callback = success_callback(success_details)
                # Airflow computes this flag once, when the DAG object is created, and the scheduler only
                # runs DAG success callbacks when it's set; we add the callback afterwards, so set it too.
                dag.has_on_success_callback = True
            return dag
        return build
    return decorator
