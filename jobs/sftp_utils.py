"""Shared helpers for the Grid_Report SFTP migration scripts.

Settings come from environment variables, falling back to the project's .env:
    SFTP_HOST                  default: localhost (Airflow containers get "sftp")
    SFTP_PORT                  default: 22
    SFTP_USERNAME / SFTP_PASSWORD
    SFTP_HOST_KEY_FINGERPRINT  optional "SHA256:..."; when set, the server key must match
    GRID_REPORTS_ROOT          default: <project>/data/Grid_Report
"""

import base64
import hashlib
import logging
import os
import posixpath
import re
import stat
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import paramiko

from env_utils import load_dotenv  # noqa: F401  (re-exported for the migration scripts)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCAL_ROOT = PROJECT_ROOT / "data" / "Grid_Report"
DEFAULT_REMOTE_ROOT = "/upload/Grid_Report"

YEAR_RE = re.compile(r"^\d{4}$")
MONTH_RE = re.compile(r"^(0[1-9]|1[0-2])$")
# {region_slug}_{YYYYMMDD}_grid_report.pdf, e.g. south_dakota_interconnection_20261001_grid_report.pdf
REPORT_NAME_RE = re.compile(r"^(?P<region>.+)_(?P<date>\d{8})_grid_report\.pdf$", re.IGNORECASE)

log = logging.getLogger("grid_report_sftp")


# ----------------------------------------------------------------- settings ----

def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("paramiko").setLevel(logging.WARNING)
    logging.getLogger("azure").setLevel(logging.WARNING)  # the Azure SDK logs every HTTP request at INFO


def default_local_root() -> str:
    return os.environ.get("GRID_REPORTS_ROOT") or str(DEFAULT_LOCAL_ROOT)


# ------------------------------------------------------------ report files ----

@dataclass
class ReportFile:
    local_path: Path
    year: str
    month: str

    @property
    def name(self) -> str:
        return self.local_path.name

    def remote_path(self, remote_root: str) -> str:
        return posixpath.join(remote_root, self.year, self.month, self.name)


@dataclass
class RemoteReport:
    """A report PDF on the SFTP server."""
    path: str
    year: str
    month: str
    size: int
    mtime: int

    @property
    def name(self) -> str:
        return posixpath.basename(self.path)


def report_date_from_name(filename: str) -> date | None:
    """Breaks the file name apart and returns its report date, or None if it isn't a grid report."""
    match = REPORT_NAME_RE.match(filename)
    if not match:
        return None
    try:
        return date(int(match["date"][:4]), int(match["date"][4:6]), int(match["date"][6:]))
    except ValueError:  # e.g. 20261341
        return None


# --------------------------------------------------------------------- sftp ----

def fingerprint(key: paramiko.PKey) -> str:
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


class SftpConnection:
    """Context manager: verified, password-authenticated SFTP session."""

    def __init__(self, host: str, port: int, username: str, password: str):
        self.host, self.port, self.username, self.password = host, port, username, password
        self._known_dirs: set[str] = set()

    def __enter__(self) -> "SftpConnection":
        self.transport = paramiko.Transport((self.host, self.port))
        try:
            self.transport.start_client(timeout=15)
            server_key = self.transport.get_remote_server_key()
            actual = fingerprint(server_key)
            expected = os.environ.get("SFTP_HOST_KEY_FINGERPRINT")
            if expected and expected != actual:
                raise paramiko.SSHException(
                    f"Host key mismatch for {self.host}:{self.port}: expected {expected}, got {actual}. Refusing to connect.")
            if not expected:
                log.warning("Host key not verified (set SFTP_HOST_KEY_FINGERPRINT to enforce): %s %s",
                            server_key.get_name(), actual)
            self.transport.auth_password(self.username, self.password)
            self.sftp = paramiko.SFTPClient.from_transport(self.transport)
        except Exception:
            self.transport.close()
            raise
        return self

    def __exit__(self, *exc) -> None:
        if getattr(self, "sftp", None):
            self.sftp.close()
        self.transport.close()

    def is_up_to_date(self, local_path: Path, remote_path: str) -> bool | None:
        """None = not on the server; True = same size and modification time; False = differs."""
        try:
            remote = self.sftp.stat(remote_path)
        except FileNotFoundError:
            return None
        local = local_path.stat()
        return remote.st_size == local.st_size and int(remote.st_mtime) == int(local.st_mtime)

    def _list_dirs(self, path: str, pattern: re.Pattern) -> list[str]:
        try:
            entries = self.sftp.listdir_attr(path)
        except FileNotFoundError:
            return []
        return sorted(e.filename for e in entries if stat.S_ISDIR(e.st_mode) and pattern.match(e.filename))

    def list_reports(self, remote_root: str, year: str | None = None, month: str | None = None) -> list[RemoteReport]:
        """PDFs under {remote_root}/{YYYY}/{MM}/ on the server (in-progress .partial uploads excluded).
        Missing folders simply yield no reports."""
        reports = []
        years = [year] if year else self._list_dirs(remote_root, YEAR_RE)
        for y in years:
            months = [month] if month else self._list_dirs(posixpath.join(remote_root, y), MONTH_RE)
            for m in months:
                month_dir = posixpath.join(remote_root, y, m)
                try:
                    entries = self.sftp.listdir_attr(month_dir)
                except FileNotFoundError:
                    continue
                for e in sorted(entries, key=lambda e: e.filename):
                    if stat.S_ISREG(e.st_mode) and e.filename.lower().endswith(".pdf"):
                        reports.append(RemoteReport(path=posixpath.join(month_dir, e.filename), year=y, month=m,
                                                    size=e.st_size, mtime=int(e.st_mtime)))
        return reports

    def ensure_dir(self, path: str) -> None:
        """mkdir -p on the server."""
        if path in self._known_dirs or path in ("", "/"):
            return
        self.ensure_dir(posixpath.dirname(path))
        try:
            self.sftp.stat(path)
        except FileNotFoundError:
            self.sftp.mkdir(path)
            log.info("Created remote folder %s", path)
        self._known_dirs.add(path)

    def upload(self, local_path: Path, remote_path: str) -> None:
        """Uploads as <name>.partial, keeps the local modification time, then renames atomically,
        so readers never see a half-written file."""
        self.ensure_dir(posixpath.dirname(remote_path))
        partial = remote_path + ".partial"
        self.sftp.put(str(local_path), partial, confirm=True)
        local = local_path.stat()
        self.sftp.utime(partial, (int(local.st_atime), int(local.st_mtime)))
        self.sftp.posix_rename(partial, remote_path)  # replaces an existing file


def sync_reports(conn: SftpConnection, reports: list[ReportFile], remote_root: str,
                 overwrite: bool, dry_run: bool, verbose: bool = False) -> tuple[int, int, int]:
    """Uploads new or changed reports. Returns (uploaded, skipped, failed)."""
    uploaded = skipped = failed = 0
    uploaded_bytes = 0
    for report in reports:
        remote_path = report.remote_path(remote_root)
        try:
            state = conn.is_up_to_date(report.local_path, remote_path)
            if state and not overwrite:
                skipped += 1
                if verbose:
                    log.info("skip     %s (already on server, unchanged)", remote_path)
                continue

            action = "upload  " if state is None else "replace "
            size = report.local_path.stat().st_size
            if dry_run:
                log.info("%s %s (dry run)", action, remote_path)
            else:
                conn.upload(report.local_path, remote_path)
                log.info("%s %s (%.1f KB)", action, remote_path, size / 1024)
            uploaded += 1
            uploaded_bytes += size
        except (OSError, paramiko.SSHException) as exc:
            failed += 1
            log.error("FAILED   %s: %s", remote_path, exc)

    verb = "would upload" if dry_run else "uploaded"
    log.info("Done: %d %s (%.1f MB), %d skipped (already on server, unchanged), %d failed",
             uploaded, verb, uploaded_bytes / 1024 / 1024, skipped, failed)
    return uploaded, skipped, failed
