"""Where reports are written, and how they are named.

Layout:  {REPORTS_ROOT}/{YYYY}/{MM}/{region_slug}_{YYYYMMDD}_grid_report.pdf
Example: data/Grid_Report/2025/01/south_dakota_interconnection_20250101_grid_report.pdf

REPORTS_ROOT comes from the GRID_REPORTS_ROOT environment variable (set to
/opt/spark-data/Grid_Report in the Jupyter and Airflow containers), falling back
to <project>/data/Grid_Report when run directly from the repo.
"""

import os
from datetime import date
from pathlib import Path

_DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "data" / "Grid_Report"


def use_shared_permissions() -> None:
    """Folders and PDFs are written by different container users (Jupyter 1000,
    Airflow 50000, Spark 185), so create everything group/world-writable, the same
    convention as the rest of /opt/spark-data."""
    os.umask(0)


def reports_root(override: str | None = None) -> Path:
    return Path(override or os.environ.get("GRID_REPORTS_ROOT") or _DEFAULT_ROOT)


def region_slug(grid_region: str) -> str:
    return grid_region.lower().replace(" ", "_")


def report_path(root: Path, grid_region: str, report_date: date) -> Path:
    month_dir = root / report_date.strftime("%Y") / report_date.strftime("%m")
    return month_dir / f"{region_slug(grid_region)}_{report_date.strftime('%Y%m%d')}_grid_report.pdf"
