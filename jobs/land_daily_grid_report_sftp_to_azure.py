"""Daily landing: copies only the PREVIOUS day's grid report(s) from the SFTP server into the
Azure File Share landing layer.

The target date is yesterday in UTC (on 2026-10-02 UTC it lands 20261001). Each file name
on SFTP is broken apart ({region}_{YYYYMMDD}_grid_report.pdf) to read its date, and every
report whose date matches is copied to <share>/Grid_Report/{YYYY}/{MM}/.

If SFTP has no report for that date, the script logs it and stops cleanly with exit code 0:
a missing day is not an error. Settings: see azure_landing_utils.py and sftp_utils.py.

Usage:
    python jobs/land_daily_grid_report_sftp_to_azure.py
    python jobs/land_daily_grid_report_sftp_to_azure.py --date 2026-09-29   # re-run a specific day
    docker compose exec airflow-scheduler python /opt/spark-jobs/land_daily_grid_report_sftp_to_azure.py

Exit codes: 0 = landed, already up to date, or no report for the day
            1 = copy failed, 2 = setup/connection error.
"""

import argparse
import os
import sys
from pathlib import Path
from datetime import date, datetime, timedelta, timezone

import paramiko
from azure.core.exceptions import AzureError

# Helpers live next to this file: works with `python jobs/<script>.py` and `python -m jobs.<script>`.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from azure_landing_utils import AzureLanding, land_reports, landing_settings, short_error
from sftp_utils import (DEFAULT_REMOTE_ROOT, SftpConnection, load_dotenv, log, report_date_from_name,
                        setup_logging)


def yesterday_utc() -> date:
    return datetime.now(timezone.utc).date() - timedelta(days=1)


def parse_args() -> argparse.Namespace:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Copy the previous day's (UTC) grid report from SFTP into the Azure landing layer.")
    parser.add_argument("--date", type=date.fromisoformat, default=None,
                        help="Report date YYYY-MM-DD (default: yesterday in UTC).")
    parser.add_argument("--sftp-root", default=DEFAULT_REMOTE_ROOT)
    parser.add_argument("--overwrite", action="store_true", help="Re-send even if already landed and unchanged.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be copied without copying.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    setup_logging()

    target = args.date or yesterday_utc()
    log.info("Daily landing for %s (%s)", target.strftime("%Y%m%d"),
             "requested date" if args.date else "yesterday, UTC")

    conn_str, share_name, azure_root = landing_settings()
    host, port = os.environ.get("SFTP_HOST", "localhost"), int(os.environ.get("SFTP_PORT", "22"))
    username, password = os.environ.get("SFTP_USERNAME"), os.environ.get("SFTP_PASSWORD")
    if not conn_str or not share_name:
        log.error("AZURE_STORAGE_CONNECTION_STRING and AZURE_FILE_SHARE_NAME must be set (environment or .env)")
        return 2
    if not username or not password:
        log.error("SFTP_USERNAME and SFTP_PASSWORD must be set (environment or .env)")
        return 2

    try:
        with SftpConnection(host, port, username, password) as sftp:
            month_reports = sftp.list_reports(args.sftp_root, f"{target:%Y}", f"{target:%m}")
            reports = [r for r in month_reports if report_date_from_name(r.name) == target]
            if not reports:
                log.info("No report on SFTP for %s in %s/%s/%s. Nothing to land; stopping.",
                         target.strftime("%Y%m%d"), args.sftp_root, f"{target:%Y}", f"{target:%m}")
                return 0
            for report in reports:
                log.info("Matched %s", report.name)

            try:
                landing = AzureLanding(conn_str, share_name)
                landing.check()
            except (AzureError, ValueError) as exc:
                log.error("Cannot reach Azure File Share '%s': %s", share_name, short_error(exc))
                return 2

            log.info("Landing into Azure %s/%s%s", share_name, azure_root, " (dry run)" if args.dry_run else "")
            _, _, failed = land_reports(sftp, landing, reports, azure_root, args.overwrite, args.dry_run, verbose=True)
    except (OSError, paramiko.SSHException) as exc:
        log.error("SFTP error (%s:%s as %s): %s", host, port, username, exc)
        return 2
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
