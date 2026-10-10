"""Full migration: uploads ALL grid report PDFs from the local Grid_Report folder to the
SFTP server, keeping the same structure. For the daily run, see
migrate_daily_grid_report_to_sftp.py.

    local:  {LOCAL_ROOT}/{YYYY}/{MM}/<name>.pdf
    remote: /upload/Grid_Report/{YYYY}/{MM}/<name>.pdf
            (lands in data/sftp/upload/Grid_Report/... on the host)

Safe to re-run: files already on the server with the same size and modification time
are skipped (--overwrite re-sends them). Settings: see sftp_utils.py.

Usage:
    # from Windows (.venv activated)
    python jobs/migrate_grid_reports_to_sftp.py --dry-run
    python jobs/migrate_grid_reports_to_sftp.py
    python jobs/migrate_grid_reports_to_sftp.py --year 2026 --month 09

    # from the Airflow container
    docker compose exec airflow-scheduler python /opt/spark-jobs/migrate_grid_reports_to_sftp.py

Exit codes: 0 = success (including nothing to do), 1 = some files failed, 2 = setup/connection error.
"""

import argparse
import os
import sys
from pathlib import Path

import paramiko

# Helpers live next to this file: works with `python jobs/<script>.py` and `python -m jobs.<script>`.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sftp_utils import (DEFAULT_REMOTE_ROOT, MONTH_RE, YEAR_RE, ReportFile, SftpConnection,
                        default_local_root, load_dotenv, log, setup_logging, sync_reports)


def parse_args() -> argparse.Namespace:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Upload all Grid_Report PDFs to the SFTP server, preserving YYYY/MM folders.")
    parser.add_argument("--local-root", default=default_local_root(),
                        help="Local Grid_Report folder (default: GRID_REPORTS_ROOT or data/Grid_Report).")
    parser.add_argument("--remote-root", default=DEFAULT_REMOTE_ROOT,
                        help=f"Target folder on the SFTP server (default: {DEFAULT_REMOTE_ROOT}).")
    parser.add_argument("--host", default=os.environ.get("SFTP_HOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("SFTP_PORT", "22")))
    parser.add_argument("--username", default=os.environ.get("SFTP_USERNAME"))
    parser.add_argument("--year", help="Only migrate this year, e.g. 2026.")
    parser.add_argument("--month", help="Only migrate this month (01-12); combine with --year.")
    parser.add_argument("--overwrite", action="store_true", help="Re-upload files even if unchanged on the server.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be uploaded without uploading.")
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


def find_reports(local_root: Path, year: str | None, month: str | None) -> list[ReportFile]:
    """PDFs that follow {root}/{YYYY}/{MM}/*.pdf. Anything else is reported and ignored."""
    reports = []
    for pdf in sorted(local_root.rglob("*.pdf")):
        parts = pdf.relative_to(local_root).parts
        if len(parts) != 3 or not YEAR_RE.match(parts[0]) or not MONTH_RE.match(parts[1]):
            log.warning("Ignoring file outside the YYYY/MM structure: %s", pdf)
            continue
        if (year and parts[0] != year) or (month and parts[1] != month):
            continue
        reports.append(ReportFile(local_path=pdf, year=parts[0], month=parts[1]))
    return reports


def main() -> int:
    args = parse_args()
    setup_logging()

    local_root = Path(args.local_root)
    if not local_root.is_dir():
        log.error("Local folder not found: %s", local_root)
        return 2
    password = os.environ.get("SFTP_PASSWORD")
    if not args.username or not password:
        log.error("SFTP_USERNAME and SFTP_PASSWORD must be set (environment or .env)")
        return 2

    reports = find_reports(local_root, args.year, args.month)
    scope = f"{args.year or 'all years'}{'/' + args.month if args.month else ''}"
    log.info("Found %d report(s) in %s (%s)", len(reports), local_root, scope)
    if not reports:
        return 0

    try:
        with SftpConnection(args.host, args.port, args.username, password) as conn:
            log.info("Connected to %s:%s as %s -> %s%s", args.host, args.port, args.username,
                     args.remote_root, " (dry run)" if args.dry_run else "")
            _, _, failed = sync_reports(conn, reports, args.remote_root, args.overwrite, args.dry_run, args.verbose)
    except (OSError, paramiko.SSHException) as exc:
        log.error("Could not connect to %s:%s as %s: %s", args.host, args.port, args.username, exc)
        return 2
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
