# Grid report PDF generator

Produces synthetic **ISO Daily Market & Grid Operations Report** PDFs: daily operations reports for a fictitious US power-grid operator. Ported from `Industry_Fabric_Project/synth_grid_reports`, with the same data model, layout and file names.

## What a report contains

Each report is a landscape letter PDF of about 4 pages:

- **Header (every page):** report title, operator, report date, grid region.
- **Metadata table:** balancing authority, transmission operator, market zone, reporting period.
- **Grid Performance Summary:** peak and average load, average frequency, average reserve margin, renewable share, average reliability index.
- **Detail table:** 96 rows (24 hours × 4 substations) with 18 columns:
  - generation, load, renewables, wind, solar, battery
  - frequency, voltage, congestion, available capacity, reserve margin
  - carbon intensity, LMP price
  - weather, equipment status, reliability
- **Footer (every page):** synthetic-data disclaimer, generated timestamp, page number.

The data follows realistic daily shapes: load peaks around 18:00 and solar around 13:00, weather affects solar and wind output, and equipment alerts lower reliability. Every constant is in `grid_reports/config.py`.

## Where reports go

```
data/Grid_Report/{YYYY}/{MM}/{region_slug}_{YYYYMMDD}_grid_report.pdf
e.g. data/Grid_Report/2025/01/south_dakota_interconnection_20250101_grid_report.pdf
```

Inside the containers this is `/opt/spark-data/Grid_Report`. You can override it with `--output-root` or the `GRID_REPORTS_ROOT` environment variable.

## Running it

Run it in the Jupyter container. The Airflow scheduler container also works; the code is mounted at `/opt/pdf_generator` in both.

```powershell
# Backfill: 8-10 reports per month, Jan 2025 through last month
docker compose exec -w /opt/pdf_generator jupyter python -m grid_reports.generate_historical_reports

# One report: defaults to yesterday
docker compose exec -w /opt/pdf_generator jupyter python -m grid_reports.generate_report
docker compose exec -w /opt/pdf_generator jupyter python -m grid_reports.generate_report --date 2026-09-15
```

Options:

| Script | Option | Effect |
|---|---|---|
| both | `--output-root PATH` | Write somewhere other than `GRID_REPORTS_ROOT` |
| both | `--force` | Overwrite reports that already exist (by default they are skipped) |
| historical | `--start YYYY-MM --end YYYY-MM` | Month range (default `2025-01` to last month) |
| daily | `--date YYYY-MM-DD` | Report date (default yesterday) |
| daily | `--random-identity` | Invent a new fictitious utility instead of the fixed one |

## Reproducibility

- **One utility throughout.** All reports belong to the same fictitious utility, *South Dakota Interconnection*, built from seed 42 in `identity.py`. The archive therefore reads as one operator's history.
- **The backfill is deterministic.** Dates and per-report seeds come from `MASTER_SEED = 42`. January 2025 to June 2026 matches the original project's archive.
- **Daily reports are reproducible.** A daily report is seeded from its date, so regenerating a date gives the same figures; only the "Generated" timestamp changes.

## Changes from the original project

| Change | Why |
|---|---|
| The daily script defaults to **yesterday**, not a hard-coded `REPORT_DATE`. | It suits a scheduled run, e.g. from Airflow. |
| The daily script uses the **same utility** as the archive. | Without a seed, the original invented a new utility each run (Montana, Nevada…). That behaviour is still available with `--random-identity`. |
| Existing reports are **skipped** unless `--force` is given. | Re-runs are safe. |
| The output location is **configurable**. | It defaults to `data/Grid_Report`. |

## Code layout

```
grid_reports/
├── config.py                       # distributions and constants (load shape, solar, wind, prices, ...)
├── data_generator.py               # Faker metadata, substations, the 96-row hourly DataFrame
├── pdf_builder.py                  # reportlab layout
├── identity.py                     # the fixed utility (seed 42)
├── paths.py                        # output root and file naming
├── generate_report.py              # daily entry point
└── generate_historical_reports.py  # backfill entry point
```
