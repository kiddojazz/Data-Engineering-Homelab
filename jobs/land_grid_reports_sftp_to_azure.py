"""Full landing: copies ALL grid report PDFs from the SFTP server into the Azure File Share
landing layer, keeping the YYYY/MM structure. For the daily run, see
land_daily_grid_report_sftp_to_azure.py.

    SFTP:  /upload/Grid_Report/{YYYY}/{MM}/<name>.pdf
    Azure: <share>/Grid_Report/{YYYY}/{MM}/<name>.pdf

Safe to re-run: files already landed with the same size and source modification time are
skipped (--overwrite re-sends them). Settings: see azure_landing_utils.py and sftp_utils.py.

Usage:
    python jobs/land_grid_reports_sftp_to_azure.py --dry-run
    python jobs/land_grid_reports_sftp_to_azure.py
    python jobs/land_grid_reports_sftp_to_azure.py --year 2026 --month 09
    docker compose exec airflow-scheduler python /opt/spark-jobs/land_grid_reports_sftp_to_azure.py

Exit codes: 0 = success (including nothing to do), 1 = some files failed, 2 = setup/connection error.
"""

import argparse
import os
import sys
from pathlib import Path

import paramiko
from azure.core.exceptions import AzureError

# Helpers live next to this file: works with `python jobs/<script>.py` and `python -m jobs.<script>`.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from azure_landing_utils import AzureLanding, land_reports, landing_settings, short_error
from sftp_utils import (DEFAULT_REMOTE_ROOT, MONTH_RE, YEAR_RE, SftpConnection, load_dotenv, log,
                        setup_logging)


def parse_args() -> argparse.Namespace:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Copy all grid report PDFs from SFTP into the Azure File Share landing layer.")
    parser.add_argument("--sftp-root", default=DEFAULT_REMOTE_ROOT, help=f"Source folder on SFTP (default: {DEFAULT_REMOTE_ROOT}).")
    parser.add_argument("--year", help="Only land this year, e.g. 2026.")
    parser.add_argument("--month", help="Only land this month (01-12); combine with --year.")
    parser.add_argument("--overwrite", action="store_true", help="Re-send files even if already landed and unchanged.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be copied without copying.")
    parser.add_argument("--verbose", action="store_true", help="Also log every skipped file.")
    args = parser.parse_args()

    if args.month and not args.year:
        parser.error("--month needs --year")
    if args.year and not YEAR_RE.match(args.year):
        parser.error("--year must be YYYY")
    if args.month:
        args.month = args.month.zfill(2)
        if not MONTH_RE.match(args.month):
            parser.error("--month must be 01-12")
    return args


def main() -> int:
    args = parse_args()
    setup_logging()

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
        landing = AzureLanding(conn_str, share_name)
        landing.check()
    except (AzureError, ValueError) as exc:
        log.error("Cannot reach Azure File Share '%s': %s", share_name, short_error(exc))
        return 2

    try:
        with SftpConnection(host, port, username, password) as sftp:
            reports = sftp.list_reports(args.sftp_root, args.year, args.month)
            scope = f"{args.year or 'all years'}{'/' + args.month if args.month else ''}"
            log.info("Found %d report(s) on SFTP %s (%s) -> Azure %s/%s%s", len(reports), args.sftp_root, scope,
                     share_name, azure_root, " (dry run)" if args.dry_run else "")
            if not reports:
                return 0
            _, _, failed = land_reports(sftp, landing, reports, azure_root, args.overwrite, args.dry_run, args.verbose)
    except (OSError, paramiko.SSHException) as exc:
        log.error("SFTP error (%s:%s as %s): %s", host, port, username, exc)
        return 2
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
