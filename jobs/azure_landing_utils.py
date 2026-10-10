"""Azure File Share landing layer: copies grid report PDFs from the SFTP server into an
Azure File Share over HTTPS (azure-storage-file-share, port 443; no SMB mount needed).

    SFTP:  /upload/Grid_Report/{YYYY}/{MM}/<name>.pdf
    Azure: <share>/Grid_Report/{YYYY}/{MM}/<name>.pdf

Settings come from environment variables, falling back to the project's .env:
    AZURE_STORAGE_CONNECTION_STRING  storage account connection string (Access keys blade)
    AZURE_FILE_SHARE_NAME            the file share, e.g. landing
    AZURE_LANDING_ROOT               folder inside the share (default: Grid_Report)
plus the SFTP_* settings described in sftp_utils.py.
"""

import os
import posixpath

from azure.core.exceptions import AzureError, ResourceExistsError, ResourceNotFoundError
from azure.storage.fileshare import ShareClient

from sftp_utils import RemoteReport, SftpConnection, log

DEFAULT_AZURE_ROOT = "Grid_Report"
SOURCE_MTIME_KEY = "source_mtime"  # file metadata: the SFTP file's modification time


class AzureLanding:
    """Thin wrapper over one Azure File Share."""

    def __init__(self, connection_string: str, share_name: str):
        self.share = ShareClient.from_connection_string(connection_string, share_name)
        self.share_name = share_name
        self._known_dirs: set[str] = set()

    def check(self) -> None:
        """Fails fast with a clear error if the share doesn't exist or the credentials are wrong."""
        self.share.get_share_properties()

    def ensure_dir(self, path: str) -> None:
        """Creates every level of a directory path inside the share (Azure Files has no mkdir -p)."""
        if not path or path in self._known_dirs:
            return
        self.ensure_dir(posixpath.dirname(path))
        try:
            self.share.get_directory_client(path).create_directory()
            log.info("Created Azure folder %s/%s", self.share_name, path)
        except ResourceExistsError:
            pass
        self._known_dirs.add(path)

    def is_up_to_date(self, path: str, size: int, mtime: int) -> bool | None:
        """None = not in the share; True = same size and same source modification time; False = differs."""
        try:
            props = self.share.get_file_client(path).get_file_properties()
        except ResourceNotFoundError:
            return None
        return props.size == size and props.metadata.get(SOURCE_MTIME_KEY) == str(mtime)

    def upload_stream(self, path: str, stream, size: int, mtime: int) -> None:
        """Uploads as <name>.partial, then renames, so readers never see a half-written file."""
        self.ensure_dir(posixpath.dirname(path))
        metadata = {SOURCE_MTIME_KEY: str(mtime)}
        partial = self.share.get_file_client(path + ".partial")
        partial.upload_file(stream, length=size, metadata=metadata)
        partial.rename_file(path, overwrite=True, metadata=metadata)


def short_error(exc: Exception) -> str:
    """Azure SDK errors embed the whole HTTP exchange; keep the error code and first line."""
    code = getattr(exc, "error_code", None)
    first_line = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    return f"{code}: {first_line}" if code else first_line


def landing_settings() -> tuple[str | None, str | None, str]:
    return (os.environ.get("AZURE_STORAGE_CONNECTION_STRING"),
            os.environ.get("AZURE_FILE_SHARE_NAME"),
            os.environ.get("AZURE_LANDING_ROOT", DEFAULT_AZURE_ROOT))


def azure_path(azure_root: str, report: RemoteReport) -> str:
    return posixpath.join(azure_root, report.year, report.month, report.name)


def land_reports(sftp: SftpConnection, landing: AzureLanding, reports: list[RemoteReport], azure_root: str,
                 overwrite: bool, dry_run: bool, verbose: bool = False) -> tuple[int, int, int]:
    """Copies new or changed reports from SFTP to the share. Returns (copied, skipped, failed)."""
    copied = skipped = failed = 0
    copied_bytes = 0
    for report in reports:
        target = azure_path(azure_root, report)
        try:
            state = landing.is_up_to_date(target, report.size, report.mtime)
            if state and not overwrite:
                skipped += 1
                if verbose:
                    log.info("skip     %s/%s (already landed, unchanged)", landing.share_name, target)
                continue

            action = "land    " if state is None else "replace "
            if dry_run:
                log.info("%s %s -> %s/%s (dry run)", action, report.path, landing.share_name, target)
            else:
                with sftp.sftp.open(report.path, "rb") as source:
                    source.prefetch(report.size)
                    landing.upload_stream(target, source, report.size, report.mtime)
                log.info("%s %s -> %s/%s (%.1f KB)", action, report.path, landing.share_name, target,
                         report.size / 1024)
            copied += 1
            copied_bytes += report.size
        except (OSError, AzureError) as exc:
            failed += 1
            log.error("FAILED   %s: %s", report.path, short_error(exc))

    verb = "would land" if dry_run else "landed"
    log.info("Done: %d %s (%.1f MB), %d skipped (already landed, unchanged), %d failed",
             copied, verb, copied_bytes / 1024 / 1024, skipped, failed)
    return copied, skipped, failed
