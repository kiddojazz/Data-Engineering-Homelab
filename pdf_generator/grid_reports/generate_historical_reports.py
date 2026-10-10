"""Backfill: one report per randomly picked date, 8-10 dates per month, for every
month from --start to --end (default: January 2025 through last month).

Every report belongs to the same utility (see identity.py); only the date and that
day's operational data vary. The date picks and per-report seeds come from
MASTER_SEED, so the archive is reproducible, and January 2025 - June 2026 matches
the original project's archive. Existing files are skipped unless --force is given.

Usage (from the pdf_generator folder):
    python -m grid_reports.generate_historical_reports
    python -m grid_reports.generate_historical_reports --start 2025-01 --end 2025-12
"""

import argparse
import calendar
import random
from datetime import date, datetime

from . import data_generator, identity, pdf_builder
from .paths import report_path, reports_root, use_shared_permissions

DEFAULT_START = "2025-01"
MIN_REPORTS_PER_MONTH = 8
MAX_REPORTS_PER_MONTH = 10
MASTER_SEED = 42


def _last_month() -> str:
    today = date.today()
    year, month = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    return f"{year:04d}-{month:02d}"


def parse_args():
    parser = argparse.ArgumentParser(description="Backfill a historical archive of grid report PDFs.")
    parser.add_argument("--start", default=DEFAULT_START, help="First month, YYYY-MM (default 2025-01).")
    parser.add_argument("--end", default=_last_month(), help="Last month, YYYY-MM (default: last month).")
    parser.add_argument("--output-root", help="Overrides GRID_REPORTS_ROOT.")
    parser.add_argument("--force", action="store_true", help="Overwrite reports that already exist.")
    return parser.parse_args()


def _parse_year_month(value: str) -> tuple[int, int]:
    parsed = datetime.strptime(value, "%Y-%m")
    return parsed.year, parsed.month


def _month_range(start: tuple[int, int], end: tuple[int, int]):
    year, month = start
    while (year, month) <= end:
        yield year, month
        month += 1
        if month > 12:
            month = 1
            year += 1


def _pick_dates_for_month(year: int, month: int, rng: random.Random) -> list[datetime]:
    days_in_month = calendar.monthrange(year, month)[1]
    count = min(rng.randint(MIN_REPORTS_PER_MONTH, MAX_REPORTS_PER_MONTH), days_in_month)
    days = rng.sample(range(1, days_in_month + 1), k=count)
    return sorted(datetime(year, month, day) for day in days)


def main():
    args = parse_args()
    use_shared_permissions()
    root = reports_root(args.output_root)

    # The RNG always starts at January 2025, so a later --start still picks the same
    # dates and seeds for its months as a full run would.
    master_rng = random.Random(MASTER_SEED)
    template, substations = identity.build_identity()
    print(f"Archive identity: {template['grid_region']} ({template['operator']})")
    print(f"Writing to: {root}")

    start, end = _parse_year_month(args.start), _parse_year_month(args.end)
    written = skipped = 0
    for year, month in _month_range(_parse_year_month(DEFAULT_START), end):
        report_dates = _pick_dates_for_month(year, month, master_rng)
        for report_date in report_dates:
            report_seed = master_rng.randint(0, 2**31 - 1)
            if (year, month) < start:
                continue

            metadata = identity.metadata_for(template, report_date, generated_at=report_date)
            output_path = report_path(root, metadata["grid_region"], metadata["report_date"])
            if output_path.exists() and not args.force:
                skipped += 1
                continue

            df = data_generator.build_dataframe(metadata, substations, seed=report_seed)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            pdf_builder.build_pdf(metadata, substations, df, output_path)
            written += 1
            print(f"[{written}] {output_path.relative_to(root)} ({len(df)} rows)")

    print(f"\nDone. {written} written, {skipped} already existed, in {root}")


if __name__ == "__main__":
    main()
