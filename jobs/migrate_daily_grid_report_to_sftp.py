"""Daily migration: uploads only the PREVIOUS day's grid report(s) to the SFTP server.

The target date is yesterday in UTC (on 2026-10-02 UTC it migrates 20261001). Each file
name is broken apart ({region}_{YYYYMMDD}_grid_report.pdf) to read its date, and every
report whose date matches is uploaded to /upload/Grid_Report/{YYYY}/{MM}/.

If there is no report for that date, the script logs it and stops cleanly with exit
code 0: a missing day is not an error. Settings: see sftp_utils.py.

Usage:
    # from Windows (.venv activated)
    python jobs/migrate_daily_grid_report_to_sftp.py
    python jobs/migrate_daily_grid_report_to_sftp.py --date 2026-09-29   # re-run a specific day

    # from the Airflow container
    docker compose exec airflow-scheduler python /opt/spark-jobs/migrate_daily_grid_report_to_sftp.py

Exit codes: 0 = uploaded, already up to date, or no report for the day
            1 = upload failed, 2 = setup/connection error.
"""

import argparse
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import paramiko

# Helpers live next to this file: works with `python jobs/<script>.py` and `python -m jobs.<script>`.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sftp_utils import (DEFAULT_REMOTE_ROOT, ReportFile, SftpConnection, default_local_root,
                        load_dotenv, log, report_date_from_name, setup_logging, sync_reports)


def yesterday_utc() -> date:
    return datetime.now(timezone.utc).date() - timedelta(days=1)


def parse_args() -> argparse.Namespace:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Upload the previous day's (UTC) grid report to the SFTP server.")
    parser.add_argument("--date", type=date.fromisoformat, default=None,
                        help="Report date YYYY-MM-DD (default: yesterday in UTC).")
    parser.add_argument("--local-root", default=default_local_root(),
                        help="Local Grid_Report folder (default: GRID_REPORTS_ROOT or data/Grid_Report).")
    parser.add_argument("--remote-root", default=DEFAULT_REMOTE_ROOT)
    parser.add_argument("--host", default=os.environ.get("SFTP_HOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("SFTP_PORT", "22")))
    parser.add_argument("--username", default=os.environ.get("SFTP_USERNAME"))
    parser.add_argument("--overwrite", action="store_true", help="Re-upload even if unchanged on the server.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be uploaded without uploading.")
    return parser.parse_args()


def find_reports_for_date(local_root: Path, target: date) -> list[ReportFile]:
    """Every report in the target's YYYY/MM folder whose file-name date equals the target."""
    year, month = f"{target.year:04d}", f"{target.month:02d}"
    month_dir = local_root / year / month
    if not month_dir.is_dir():
        return []
    return [
        ReportFile(local_path=pdf, year=year, month=month)
        for pdf in sorted(month_dir.glob("*.pdf"))
        if report_date_from_name(pdf.name) == target
    ]


def main() -> int:
    args = parse_args()
    setup_logging()

    target = args.date or yesterday_utc()
    local_root = Path(args.local_root)
    log.info("Daily migration for %s (%s)", target.strftime("%Y%m%d"),
             "requested date" if args.date else "yesterday, UTC")

    if not local_root.is_dir():
        log.error("Local folder not found: %s", local_root)
        return 2

    reports = find_reports_for_date(local_root, target)
    if not reports:
        log.info("No report found for %s in %s. Nothing to migrate; stopping.",
                 target.strftime("%Y%m%d"), local_root / f"{target:%Y}" / f"{target:%m}")
        return 0
    for report in reports:
        log.info("Matched %s", report.name)

    password = os.environ.get("SFTP_PASSWORD")
    if not args.username or not password:
        log.error("SFTP_USERNAME and SFTP_PASSWORD must be set (environment or .env)")
        return 2

    try:
        with SftpConnection(args.host, args.port, args.username, password) as conn:
            log.info("Connected to %s:%s as %s -> %s%s", args.host, args.port, args.username,
                     args.remote_root, " (dry run)" if args.dry_run else "")
            _, _, failed = sync_reports(conn, reports, args.remote_root, args.overwrite, args.dry_run, verbose=True)
    except (OSError, paramiko.SSHException) as exc:
        log.error("Could not connect to %s:%s as %s: %s", args.host, args.port, args.username, exc)
        return 2
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
