"""Daily ETL: one day's grid report PDF(s) from the Azure File Share landing layer -> SQL Server.

Script version of notebooks/01_backfill_grid_report_to_sqlserver.ipynb in single mode
(use the notebook for backfills). Run with spark-submit; Airflow runs it as the
`fileshare_etl_sql_server` task of the grid_report_daily DAG.

    Azure File Share  landing/Grid_Report/{YYYY}/{MM}/*_{YYYYMMDD}_grid_report.pdf
      -> pdfplumber parse (header + summary + 96 hourly rows per report)
      -> Spark DataFrames + transformations
      -> JDBC into staging.grid_operations_daily_summary / staging.grid_operations_detail
      -> T-SQL MERGE (one transaction) into datamart.grid_operations_daily_summary / datamart.grid_operations_detail
      -> etl.file_load_log

Behaviour:
  - Target date: --date (YYYY-MM-DD or YYYYMMDD), default yesterday in UTC.
  - No report for the date in the file share -> logs it and exits 0 (a missing day is not an error).
  - Reports already in datamart are skipped (exit 0) unless --reprocess, which re-MERGEs (updates) them.
  - --dry-run downloads and parses, reports what would be loaded, and writes nothing.

Settings (environment, falling back to the project .env):
    AZURE_STORAGE_CONNECTION_STRING, AZURE_FILE_SHARE_NAME, AZURE_LANDING_ROOT (default Grid_Report)
    MSSQL_HOST, MSSQL_PORT, MSSQL_DB, MSSQL_USER, MSSQL_PASSWORD

Usage:
    docker compose exec airflow-scheduler spark-submit /opt/spark-jobs/grid_report_etl_to_sqlserver.py
    docker compose exec airflow-scheduler spark-submit /opt/spark-jobs/grid_report_etl_to_sqlserver.py --date 2026-10-01 --dry-run
    docker compose exec jupyter python /opt/spark-jobs/grid_report_etl_to_sqlserver.py --date 2026-10-01

Exit codes: 0 = loaded / already loaded / nothing for that day
            1 = a file failed to download or parse, or the load failed
            2 = configuration or connection error
"""

import argparse
import io
import logging
import os
import re
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pdfplumber
import pymssql
from azure.core.exceptions import AzureError, ResourceNotFoundError
from azure.storage.fileshare import ShareClient

# Helpers live next to this file: works with `python jobs/<script>.py`, `python -m jobs.<script>` and spark-submit.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from env_utils import load_dotenv

PIPELINE_NAME = "grid_report_daily_etl_to_sqlserver"
EXPECTED_ROWS_PER_REPORT = 24 * 4   # 24 hours x 4 substations

DATAMART, STAGING, ETL = "datamart", "staging", "etl"
SUMMARY_TABLE = f"{DATAMART}.grid_operations_daily_summary"
DETAIL_TABLE = f"{DATAMART}.grid_operations_detail"
STG_SUMMARY_TABLE = f"{STAGING}.grid_operations_daily_summary"
STG_DETAIL_TABLE = f"{STAGING}.grid_operations_detail"
LOAD_LOG_TABLE = f"{ETL}.file_load_log"

log = logging.getLogger("grid_report_etl")


# =========================================================================== table definitions ====
# Single source of truth for the DDL, the Spark column order and the MERGE (same as the notebook).

SUMMARY_COLUMNS = [
    ("source_file",            "NVARCHAR(260) NOT NULL"),
    ("report_date",            "DATE NOT NULL"),
    ("grid_region",            "NVARCHAR(100) NULL"),
    ("operator",               "NVARCHAR(200) NULL"),
    ("balancing_authority",    "NVARCHAR(200) NULL"),
    ("transmission_operator",  "NVARCHAR(200) NULL"),
    ("market_zone",            "NVARCHAR(50) NULL"),
    ("generated_at",           "DATETIME2(0) NULL"),
    ("peak_load_mw",           "DECIMAL(10,1) NULL"),
    ("avg_load_mw",            "DECIMAL(10,1) NULL"),
    ("avg_frequency_hz",       "DECIMAL(7,3) NULL"),
    ("avg_reserve_margin_pct", "DECIMAL(9,1) NULL"),
    ("renewable_share_pct",    "DECIMAL(5,1) NULL"),
    ("avg_reliability_index",  "DECIMAL(5,1) NULL"),
    ("detail_row_count",       "INT NULL"),
]
SUMMARY_KEY = ["source_file"]

DETAIL_COLUMNS = [
    ("source_file",            "NVARCHAR(260) NOT NULL"),
    ("reading_time",           "CHAR(5) NOT NULL"),
    ("substation",             "NVARCHAR(150) NOT NULL"),
    ("report_date",            "DATE NOT NULL"),
    ("reading_datetime",       "DATETIME2(0) NOT NULL"),
    ("grid_region",            "NVARCHAR(100) NULL"),
    ("operator",               "NVARCHAR(200) NULL"),
    ("balancing_authority",    "NVARCHAR(200) NULL"),
    ("transmission_operator",  "NVARCHAR(200) NULL"),
    ("market_zone",            "NVARCHAR(50) NULL"),
    ("generated_at",           "DATETIME2(0) NULL"),
    ("gen_mw",                 "DECIMAL(10,1) NULL"),
    ("load_mw",                "DECIMAL(10,1) NULL"),
    ("renew_mw",               "DECIMAL(10,1) NULL"),
    ("wind_mw",                "DECIMAL(10,1) NULL"),
    ("solar_mw",               "DECIMAL(10,1) NULL"),
    ("batt_mw",                "DECIMAL(10,1) NULL"),
    ("freq_hz",                "DECIMAL(7,3) NULL"),
    ("volt_kv",                "DECIMAL(8,1) NULL"),
    ("congestion_pct",         "DECIMAL(5,1) NULL"),
    ("avail_cap_mw",           "DECIMAL(10,1) NULL"),
    ("reserve_pct",            "DECIMAL(9,1) NULL"),
    ("carbon_g_per_kwh",       "DECIMAL(8,1) NULL"),
    ("lmp_usd_per_mwh",        "DECIMAL(10,2) NULL"),
    ("weather",                "NVARCHAR(30) NULL"),
    ("status",                 "NVARCHAR(20) NULL"),
    ("reliability_index",      "DECIMAL(5,1) NULL"),
    ("renewable_pct_of_gen",   "DECIMAL(7,2) NULL"),
    ("is_operational",         "BIT NULL"),
]
DETAIL_KEY = ["source_file", "reading_time", "substation"]

METADATA_FIELDS = ["report_date", "grid_region", "generated_at", "operator",
                   "balancing_authority", "transmission_operator", "market_zone"]
SUMMARY_METRICS = ["peak_load_mw", "avg_load_mw", "avg_frequency_hz",
                   "avg_reserve_margin_pct", "renewable_share_pct", "avg_reliability_index"]


def create_schema_sql(schema: str) -> str:
    return f"IF SCHEMA_ID(N'{schema}') IS NULL EXEC(N'CREATE SCHEMA [{schema}]');"


def create_table_sql(table: str, columns, key=None, audit_columns=False) -> str:
    base = table.split(".")[-1]
    lines = [f"[{name}] {sql_type}" for name, sql_type in columns]
    if audit_columns:
        lines += [f"[loaded_at] DATETIME2(0) NOT NULL CONSTRAINT [DF_{base}_loaded_at] DEFAULT SYSUTCDATETIME()",
                  "[updated_at] DATETIME2(0) NULL"]
    if key:
        lines.append(f"CONSTRAINT [PK_{base}] PRIMARY KEY CLUSTERED ({', '.join(f'[{k}]' for k in key)})")
    body = ",\n    ".join(lines)
    return f"IF OBJECT_ID(N'{table}', N'U') IS NULL\nCREATE TABLE {table} (\n    {body}\n);"


def create_index_sql(table: str, name: str, columns) -> str:
    return (f"IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'{name}' AND object_id = OBJECT_ID(N'{table}'))\n"
            f"CREATE INDEX [{name}] ON {table} ({', '.join(f'[{c}]' for c in columns)});")


DDL = [
    create_schema_sql(DATAMART),
    create_schema_sql(STAGING),
    create_schema_sql(ETL),
    create_table_sql(SUMMARY_TABLE, SUMMARY_COLUMNS, key=SUMMARY_KEY, audit_columns=True),
    create_table_sql(DETAIL_TABLE, DETAIL_COLUMNS, key=DETAIL_KEY, audit_columns=True),
    create_index_sql(SUMMARY_TABLE, "IX_grid_operations_daily_summary_report_date", ["report_date"]),
    create_index_sql(DETAIL_TABLE, "IX_grid_operations_detail_report_date", ["report_date", "substation"]),
    create_table_sql(STG_SUMMARY_TABLE, SUMMARY_COLUMNS),
    create_table_sql(STG_DETAIL_TABLE, DETAIL_COLUMNS),
    f"""IF OBJECT_ID(N'{LOAD_LOG_TABLE}', N'U') IS NULL
CREATE TABLE {LOAD_LOG_TABLE} (
    [log_id]       BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT [PK_file_load_log] PRIMARY KEY,
    [run_id]       UNIQUEIDENTIFIER NOT NULL,
    [pipeline]     NVARCHAR(100) NOT NULL,
    [source_file]  NVARCHAR(260) NOT NULL,
    [report_date]  DATE NULL,
    [status]       NVARCHAR(20) NOT NULL,
    [detail_rows]  INT NULL,
    [message]      NVARCHAR(4000) NULL,
    [logged_at]    DATETIME2(0) NOT NULL CONSTRAINT [DF_file_load_log_logged_at] DEFAULT SYSUTCDATETIME()
);""",
]


# ===================================================================================== parsing ====
# Same parsers as the notebook (step 7/8), including the fixes for text that overflows its PDF column.

NUM = r"(-?\d+(?:\.\d+)?)"
GENERATED_PATTERN = r"Generated\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"
REPORT_NAME_RE = re.compile(r"^(?P<region>.+)_(?P<date>\d{8})_grid_report\.pdf$", re.IGNORECASE)
DETAIL_ROW_PATTERN = re.compile(
    r"^(\d{2}:\d{2})\s+"
    r"(.+?)\s+"
    r"(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+"
    r"(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+"
    r"(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s+"
    r"(-?[\d.]+)\s+"
    r"([A-Za-z ]+?)\s+"
    r"([\d.]+)$"
)
DETAIL_NUMERIC_FIELDS = ["gen_mw", "load_mw", "renew_mw", "wind_mw", "solar_mw", "batt_mw", "freq_hz", "volt_kv",
                         "congestion_pct", "avail_cap_mw", "reserve_pct", "carbon_g_per_kwh", "lmp_usd_per_mwh"]
KNOWN_STATUSES = ("Operational", "Maintenance", "Alert")


def _find(pattern: str, text: str, cast=str):
    match = re.search(pattern, text, re.MULTILINE)
    if not match:
        return None
    value = match.group(1).strip()
    return cast(value) if value else None


def parse_report_header(full_text: str, flow_text: str = "") -> dict:
    """full_text: page-position order. flow_text: written order (used for the footer's "Generated" stamp)."""
    report_date = _find(r"Report Date:\s*(\d{4}-\d{2}-\d{2})", full_text)
    generated = _find(GENERATED_PATTERN, flow_text) or _find(GENERATED_PATTERN, full_text)
    authorities = re.search(r"^Balancing Authority\s+(.+?)\s+Transmission Operator\s+(.+?)\s*$",
                            full_text, re.MULTILINE)
    return {
        "report_date":            datetime.strptime(report_date, "%Y-%m-%d").date() if report_date else None,
        "generated_at":           datetime.strptime(generated, "%Y-%m-%d %H:%M:%S") if generated else None,
        "grid_region":            _find(r"Grid Region:\s*(.+?)\s*$", full_text),
        "operator":               _find(r"^(.+?)\s+Grid Region:", full_text),
        "balancing_authority":    authorities.group(1).strip() if authorities else None,
        "transmission_operator":  authorities.group(2).strip() if authorities else None,
        "market_zone":            _find(r"^Market Zone\s+(.+?)\s+Reporting Period", full_text),
        "peak_load_mw":           _find(rf"Peak Load \(MW\)\s+{NUM}", full_text, float),
        "avg_load_mw":            _find(rf"Avg Load \(MW\)\s+{NUM}", full_text, float),
        "avg_frequency_hz":       _find(rf"Avg Frequency \(Hz\)\s+{NUM}", full_text, float),
        "avg_reserve_margin_pct": _find(rf"Avg Reserve Margin \(%\)\s+{NUM}", full_text, float),
        "renewable_share_pct":    _find(rf"Renewable Share \(%\)\s+{NUM}", full_text, float),
        "avg_reliability_index":  _find(rf"Avg Reliability Index\s+{NUM}", full_text, float),
    }


def split_weather_status(text: str) -> tuple:
    """'Partly Cloudy' overflows its column, so weather and status can arrive glued ('Partly CloudyMaintenance')."""
    text = text.strip()
    for status in KNOWN_STATUSES:
        if text.endswith(status):
            return text[:-len(status)].strip(), status
    weather, _, status = text.rpartition(" ")
    return weather.strip(), status


def parse_detail_rows(flow_text: str) -> list:
    rows = []
    for line in flow_text.splitlines():
        match = DETAIL_ROW_PATTERN.match(line.strip())
        if not match:
            continue
        g = match.groups()
        weather, status = split_weather_status(g[15])
        row = {"reading_time": g[0], "substation": g[1].strip()}
        row.update({name: float(value) for name, value in zip(DETAIL_NUMERIC_FIELDS, g[2:15])})
        row.update({"weather": weather, "status": status, "reliability_index": float(g[16])})
        rows.append(row)
    return rows


def extract_grid_report(file_bytes: bytes, file_name: str) -> dict:
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        layout_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        flow_text = "\n".join(page.extract_text(use_text_flow=True) or "" for page in pdf.pages)

    header = parse_report_header(layout_text, flow_text)
    if header["report_date"] is None:
        raise ValueError("'Report Date' not found; not a grid report?")
    detail_rows = parse_detail_rows(flow_text)
    if not detail_rows:
        raise ValueError("no hourly detail rows matched")

    metadata = {field: header[field] for field in METADATA_FIELDS}
    for row in detail_rows:
        row.update(source_file=file_name, **metadata)
    summary_row = {"source_file": file_name, **metadata,
                   **{metric: header[metric] for metric in SUMMARY_METRICS},
                   "detail_row_count": len(detail_rows)}
    return {"detail": detail_rows, "summary": summary_row}


# ================================================================================= connections ====

def settings() -> dict:
    load_dotenv()
    required = ["AZURE_STORAGE_CONNECTION_STRING", "AZURE_FILE_SHARE_NAME",
                "MSSQL_HOST", "MSSQL_DB", "MSSQL_USER", "MSSQL_PASSWORD"]
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"missing settings: {', '.join(missing)}")
    return {
        "azure_conn": os.environ["AZURE_STORAGE_CONNECTION_STRING"],
        "share_name": os.environ["AZURE_FILE_SHARE_NAME"],
        "azure_root": os.environ.get("AZURE_LANDING_ROOT", "Grid_Report"),
        "mssql": {"server": os.environ["MSSQL_HOST"], "port": os.environ.get("MSSQL_PORT", "1433"),
                  "user": os.environ["MSSQL_USER"], "password": os.environ["MSSQL_PASSWORD"],
                  "database": os.environ["MSSQL_DB"]},
    }


class SqlServer:
    def __init__(self, mssql: dict):
        self.mssql = mssql

    def connect(self, autocommit=True):
        return pymssql.connect(**self.mssql, autocommit=autocommit, login_timeout=15, timeout=300,
                               appname=PIPELINE_NAME)

    def execute(self, statements) -> None:
        with self.connect() as conn, conn.cursor() as cur:
            for statement in statements:
                cur.execute(statement)

    def query(self, sql: str, params=None) -> list:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    @property
    def jdbc_options(self) -> dict:
        m = self.mssql
        return {"url": f"jdbc:sqlserver://{m['server']}:{m['port']};databaseName={m['database']};"
                       "encrypt=true;trustServerCertificate=true",
                "user": m["user"], "password": m["password"],
                "driver": "com.microsoft.sqlserver.jdbc.SQLServerDriver"}


# ==================================================================================== pipeline ====

def find_reports(share: ShareClient, azure_root: str, target: date) -> list:
    """Every PDF in the target's YYYY/MM folder whose file-name date equals the target."""
    folder = f"{azure_root}/{target:%Y}/{target:%m}"
    wanted = target.strftime("%Y%m%d")
    try:
        items = list(share.get_directory_client(folder).list_directories_and_files())
    except ResourceNotFoundError:
        log.info("Folder %s does not exist in the file share", folder)
        return []
    reports = []
    for item in items:
        match = REPORT_NAME_RE.match(item.name)
        if not item.is_directory and match and match["date"] == wanted:
            reports.append({"path": f"{folder}/{item.name}", "name": item.name})
    return sorted(reports, key=lambda r: r["name"])


def already_loaded(sql: SqlServer, names: list) -> set:
    if not names:
        return set()
    placeholders = ", ".join(["%s"] * len(names))
    rows = sql.query(f"IF OBJECT_ID(N'{SUMMARY_TABLE}', N'U') IS NOT NULL "
                     f"SELECT source_file FROM {SUMMARY_TABLE} WHERE source_file IN ({placeholders}) "
                     f"ELSE SELECT CAST(NULL AS NVARCHAR(260)) WHERE 1 = 0", tuple(names))
    return {r[0] for r in rows}


def download_and_parse(share: ShareClient, reports: list):
    detail_records, summary_records, file_results = [], [], []
    for report in reports:
        name = report["name"]
        try:
            content = share.get_file_client(report["path"]).download_file().readall()
            result = extract_grid_report(content, name)
        except Exception as exc:  # noqa: BLE001 - recorded per file
            log.error("FAILED  %s: %s", name, exc)
            file_results.append({"source_file": name, "report_date": None, "status": "failed",
                                 "detail_rows": None, "message": str(exc)[:4000]})
            continue
        rows = len(result["detail"])
        ok = rows == EXPECTED_ROWS_PER_REPORT
        detail_records.extend(result["detail"])
        summary_records.append(result["summary"])
        file_results.append({"source_file": name, "report_date": result["summary"]["report_date"],
                             "status": "loaded" if ok else "warning", "detail_rows": rows,
                             "message": None if ok else f"expected {EXPECTED_ROWS_PER_REPORT} detail rows, parsed {rows}"})
        (log.info if ok else log.warning)("Parsed  %s: %d detail rows%s", name, rows,
                                          "" if ok else f" (expected {EXPECTED_ROWS_PER_REPORT})")
    return detail_records, summary_records, file_results


def build_dataframes(spark, detail_records: list, summary_records: list):
    from pyspark.sql import functions as F
    from pyspark.sql.types import (DateType, DoubleType, IntegerType, StringType, StructField, StructType,
                                   TimestampType)

    text, num = StringType(), DoubleType()
    meta = [StructField("source_file", text, False), StructField("report_date", DateType()),
            StructField("grid_region", text), StructField("generated_at", TimestampType()),
            StructField("operator", text), StructField("balancing_authority", text),
            StructField("transmission_operator", text), StructField("market_zone", text)]
    detail_schema = StructType(meta + [StructField("reading_time", text, False), StructField("substation", text, False)]
                               + [StructField(n, num) for n in DETAIL_NUMERIC_FIELDS]
                               + [StructField("weather", text), StructField("status", text),
                                  StructField("reliability_index", num)])
    summary_schema = StructType(meta + [StructField(n, num) for n in SUMMARY_METRICS]
                                + [StructField("detail_row_count", IntegerType())])

    detail_df = (
        spark.createDataFrame(detail_records, schema=detail_schema)
        .withColumn("reading_datetime", F.to_timestamp(
            F.concat_ws(" ", F.col("report_date").cast("string"), F.col("reading_time")), "yyyy-MM-dd HH:mm"))
        .withColumn("renewable_pct_of_gen",
                    F.when(F.col("gen_mw") > 0, F.round(F.col("renew_mw") / F.col("gen_mw") * 100, 2)))
        .withColumn("is_operational", F.col("status") == F.lit("Operational"))
        .dropDuplicates(DETAIL_KEY)
        .select(*[name for name, _ in DETAIL_COLUMNS])
    )
    summary_df = (spark.createDataFrame(summary_records, schema=summary_schema)
                  .dropDuplicates(SUMMARY_KEY)
                  .select(*[name for name, _ in SUMMARY_COLUMNS]))
    return detail_df, summary_df


def write_staging(sql: SqlServer, df, table: str) -> None:
    sql.execute([f"TRUNCATE TABLE {table}"])
    (df.coalesce(1).write.format("jdbc").options(**sql.jdbc_options)
       .option("dbtable", table).option("batchsize", 5000).mode("append").save())


def merge_sql(target: str, staging: str, columns, key) -> str:
    names = [name for name, _ in columns]
    on = " AND ".join(f"t.[{k}] = s.[{k}]" for k in key)
    updates = ",\n        ".join([f"t.[{n}] = s.[{n}]" for n in names if n not in key] + ["t.[updated_at] = SYSUTCDATETIME()"])
    return f"""
SET NOCOUNT ON;
DECLARE @actions TABLE ([action] NVARCHAR(10));
MERGE {target} WITH (HOLDLOCK) AS t
USING {staging} AS s
   ON {on}
WHEN MATCHED THEN UPDATE SET
        {updates}
WHEN NOT MATCHED BY TARGET THEN
    INSERT ({", ".join(f"[{n}]" for n in names)})
    VALUES ({", ".join(f"s.[{n}]" for n in names)})
OUTPUT $action INTO @actions;
SELECT [action], COUNT(*) FROM @actions GROUP BY [action];
"""


def merge_into_datamart(sql: SqlServer) -> dict:
    """Both MERGEs in one transaction: both business tables change, or neither does."""
    results = {}
    conn = sql.connect(autocommit=False)
    try:
        with conn.cursor() as cur:
            for target, staging, columns, key in [(SUMMARY_TABLE, STG_SUMMARY_TABLE, SUMMARY_COLUMNS, SUMMARY_KEY),
                                                  (DETAIL_TABLE, STG_DETAIL_TABLE, DETAIL_COLUMNS, DETAIL_KEY)]:
                cur.execute(merge_sql(target, staging, columns, key))
                results[target] = dict(cur.fetchall())
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return results


def write_load_log(sql: SqlServer, run_id: str, file_results: list) -> None:
    if not file_results:
        return
    with sql.connect() as conn, conn.cursor() as cur:
        cur.executemany(
            f"INSERT INTO {LOAD_LOG_TABLE} (run_id, pipeline, source_file, report_date, status, detail_rows, message) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [(run_id, PIPELINE_NAME, r["source_file"], r["report_date"], r["status"], r["detail_rows"], r["message"])
             for r in file_results])


def verify_loaded(sql: SqlServer, names: list) -> None:
    placeholders = ", ".join(["%s"] * len(names))
    rows = sql.query(f"SELECT s.source_file, s.detail_row_count, COUNT(d.source_file) FROM {SUMMARY_TABLE} s "
                     f"LEFT JOIN {DETAIL_TABLE} d ON d.source_file = s.source_file "
                     f"WHERE s.source_file IN ({placeholders}) GROUP BY s.source_file, s.detail_row_count", tuple(names))
    for source_file, expected, actual in rows:
        (log.info if expected == actual else log.warning)(
            "Check   %s: %d detail rows in %s (summary says %d)", source_file, actual, DETAIL_TABLE, expected)


# ======================================================================================== main ====

def parse_date(value: str) -> date:
    return datetime.strptime(value.replace("-", "").strip(), "%Y%m%d").date()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load one day's grid report(s) from the Azure File Share into SQL Server.")
    parser.add_argument("--date", type=parse_date, default=None,
                        help="Report date, YYYY-MM-DD or YYYYMMDD (default: yesterday in UTC).")
    parser.add_argument("--reprocess", action="store_true", help="Reload reports already in datamart (MERGE updates them).")
    parser.add_argument("--dry-run", action="store_true", help="Download and parse only; write nothing.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("azure", "py4j", "pdfminer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    target = args.date or (datetime.now(timezone.utc).date() - timedelta(days=1))
    run_id = str(uuid.uuid4())
    log.info("Grid report ETL for %s (%s)%s | run %s", target.strftime("%Y%m%d"),
             "requested date" if args.date else "yesterday, UTC", " | DRY RUN" if args.dry_run else "", run_id)

    try:
        cfg = settings()
        share = ShareClient.from_connection_string(cfg["azure_conn"], cfg["share_name"])
        share.get_share_properties()
        sql = SqlServer(cfg["mssql"])
        sql.query("SELECT 1")
    except (RuntimeError, AzureError, ValueError, pymssql.Error) as exc:
        log.error("Setup failed: %s", str(exc).splitlines()[0])
        return 2

    # 1. find the day's report(s) in the landing layer
    reports = find_reports(share, cfg["azure_root"], target)
    if not reports:
        log.info("No report for %s in the file share. Nothing to load; stopping.", target.strftime("%Y%m%d"))
        return 0
    log.info("Found %d report(s): %s", len(reports), ", ".join(r["name"] for r in reports))

    # 2. skip what's already in datamart (unless --reprocess)
    if not args.reprocess:
        loaded = already_loaded(sql, [r["name"] for r in reports])
        for name in sorted(loaded):
            log.info("Skip    %s (already in %s; use --reprocess to reload)", name, SUMMARY_TABLE)
        reports = [r for r in reports if r["name"] not in loaded]
        if not reports:
            log.info("Everything for %s is already loaded. Nothing to do.", target.strftime("%Y%m%d"))
            return 0

    # 3. download + parse
    detail_records, summary_records, file_results = download_and_parse(share, reports)
    failed = [r for r in file_results if r["status"] == "failed"]
    log.info("Parsed %d report(s), %d detail rows, %d failed", len(summary_records), len(detail_records), len(failed))

    if args.dry_run:
        log.info("Dry run: would load %d summary and %d detail rows; nothing written.",
                 len(summary_records), len(detail_records))
        return 1 if failed else 0

    sql.execute(DDL)  # idempotent: creates schemas/tables only if missing

    if not detail_records:
        write_load_log(sql, run_id, file_results)
        log.error("Nothing could be parsed; nothing loaded.")
        return 1

    # 4. Spark: DataFrames -> staging (JDBC) -> MERGE into datamart
    from pyspark.sql import SparkSession
    spark = SparkSession.builder.appName(PIPELINE_NAME).getOrCreate()
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    try:
        detail_df, summary_df = build_dataframes(spark, detail_records, summary_records)
        write_staging(sql, summary_df, STG_SUMMARY_TABLE)
        write_staging(sql, detail_df, STG_DETAIL_TABLE)
        log.info("Staged %d summary and %d detail rows", len(summary_records), len(detail_records))
        results = merge_into_datamart(sql)
    except Exception as exc:  # noqa: BLE001
        log.error("Load failed, datamart unchanged (transaction rolled back): %s", str(exc).splitlines()[0])
        write_load_log(sql, run_id, failed)
        return 1
    finally:
        spark.stop()

    for target_table, actions in results.items():
        log.info("MERGE   %s: inserted %d, updated %d", target_table, actions.get("INSERT", 0), actions.get("UPDATE", 0))

    # 5. load log + quick post-load check
    write_load_log(sql, run_id, file_results)
    verify_loaded(sql, [r["source_file"] for r in file_results if r["status"] != "failed"])
    log.info("Done: %d loaded, %d failed", len(file_results) - len(failed), len(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
