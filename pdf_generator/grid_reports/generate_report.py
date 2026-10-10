"""Daily run: generates one ISO Daily Market & Grid Operations Report PDF.

Defaults to yesterday's date, for the same utility as the historical archive.
Hourly data is seeded from the date, so re-running a date reproduces the same
figures. An existing report is left alone unless --force is given.

Usage (from the pdf_generator folder):
    python -m grid_reports.generate_report                    # yesterday
    python -m grid_reports.generate_report --date 2026-09-15
    python -m grid_reports.generate_report --random-identity  # a new fictitious utility
"""

import argparse
from datetime import datetime, timedelta

from faker import Faker

from . import data_generator, identity, pdf_builder
from .paths import report_path, reports_root, use_shared_permissions


def parse_args():
    parser = argparse.ArgumentParser(description="Generate one synthetic grid operations PDF report.")
    parser.add_argument("--date", help="Report date (YYYY-MM-DD). Defaults to yesterday.")
    parser.add_argument("--output-root", help="Overrides GRID_REPORTS_ROOT.")
    parser.add_argument("--force", action="store_true", help="Overwrite the report if it already exists.")
    parser.add_argument("--random-identity", action="store_true",
                        help="Invent a new utility instead of the fixed archive identity.")
    return parser.parse_args()


def main():
    args = parse_args()
    use_shared_permissions()

    if args.date:
        report_date = datetime.strptime(args.date, "%Y-%m-%d")
    else:
        report_date = datetime.combine(datetime.now().date() - timedelta(days=1), datetime.min.time())

    if args.random_identity:
        fake = Faker()
        template = data_generator.generate_report_metadata(fake, report_date)
        substations = data_generator.generate_substations(fake)
    else:
        template, substations = identity.build_identity()

    metadata = identity.metadata_for(template, report_date, generated_at=datetime.now())

    output_path = report_path(reports_root(args.output_root), metadata["grid_region"], metadata["report_date"])
    if output_path.exists() and not args.force:
        print(f"Report already exists, skipping: {output_path} (use --force to overwrite)")
        return

    df = data_generator.build_dataframe(metadata, substations, seed=int(report_date.strftime("%Y%m%d")))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_builder.build_pdf(metadata, substations, df, output_path)
    print(f"Report written to {output_path} ({len(df)} rows, {len(substations)} substations)")


if __name__ == "__main__":
    main()
