# Building an Energy Intelligence Platform - A Hybrid System Using Apache Spark, Airflow, Azure, and SQL Server

*Part 1: From daily grid reports to an on-premise SQL Server data mart, orchestrated with Airflow and monitored through Slack*

**Temidayo Omoniyi**

---

## Business Problem

In the [previous series](#), we automated the retrieval of daily energy reports and processed them in **Microsoft Fabric**. That solution works well for organizations that are all-in on a single cloud platform. Many organizations are not.

A typical enterprise data estate is **hybrid**:

- Critical systems of record still run on **on-premise SQL Server**, often for regulatory, latency or cost reasons.
- Files arrive from partners and internal systems through **SFTP**, a decades-old protocol that isn't going anywhere.
- Cloud storage is adopted incrementally, usually starting as a **landing layer**, not as a full migration.
- Teams want **open, portable tooling** (Spark, Airflow) they can run anywhere, without locking every workload into one vendor's runtime and pricing.

Data and technology leaders keep coming back to the same questions:

- How do we move data reliably between on-premise systems and the cloud **without manual hand-offs**?
- How do we make pipelines **safe to re-run**, so a retry or a backfill never creates duplicate data?
- How do we find out a pipeline failed **before the business does**?
- Can we build this with **open-source tools** we control, and still connect it to Azure?

This article answers those questions by rebuilding the energy reporting pipeline as a **hybrid system**: open-source compute and orchestration running in containers, an Azure landing layer provisioned with Infrastructure as Code, and an on-premise SQL Server data mart as the destination.

> **Disclaimer:** The data used in this project is synthetically generated with the Python Faker library. None of the reports are real; they exist purely for learning purposes.

---

## Proposed Solution

The solution simulates a realistic daily operational flow for an energy grid operator:

1. **Daily reports are produced as PDFs.** Each one is an *ISO Daily Market & Grid Operations Report*: 24 hours × 4 substations of load, generation, renewables, frequency, prices and reliability.
2. **Reports are delivered to an SFTP server**, the way many partners and legacy systems exchange files.
3. **An Azure File Share acts as the cloud landing layer.** A job copies new reports from SFTP into Azure, keeping the `Grid_Report/{YYYY}/{MM}/` folder structure.
4. **Apache Spark extracts and transforms the PDFs.** It parses unstructured text into typed records, validates them and stages them in SQL Server.
5. **A T-SQL `MERGE` publishes the data** into a clean **datamart** schema on an **on-premise SQL Server**, in a single transaction.
6. **Apache Airflow orchestrates the daily run** at 06:00 UTC, processing the previous day (T-1), with retries.
7. **Slack notifications** report every successful run, and alert on every failure with a direct link to the failing task's log.

Everything except Azure and SQL Server runs as containers in a single **Docker Compose** homelab. The Azure resources are provisioned with **Terraform**.

---

## Architecture Flow Process

📸 *[Architecture diagram: Docker homelab (Spark, Airflow, Jupyter, SFTP) ↔ Azure File Share ↔ on-prem SQL Server ↔ Slack]*

```
 ┌──────────────────────────── Docker Compose homelab ─────────────────────────────┐
 │                                                                                 │
 │  PDF generator ──► SFTP server ──┐                                              │
 │  (Faker + reportlab)  (atmoz/sftp)│                                              │
 │                                   │  Airflow DAG: grid_report_daily (06:00 UTC)  │
 │                                   │                                              │
 │                     ┌─────────────▼──────────────┐    ┌──────────────────────┐   │
 │                     │ sftp_to_azure_fileshare    │ >> │ fileshare_etl_sql_   │   │
 │                     │ (BashOperator, paramiko +  │    │ server (SparkSubmit- │   │
 │                     │  Azure SDK over HTTPS)     │    │ Operator, Spark 4.1) │   │
 │                     └─────────────┬──────────────┘    └──────────┬───────────┘   │
 └───────────────────────────────────┼──────────────────────────────┼───────────────┘
                                     ▼                              │ JDBC + T-SQL MERGE
                        Azure File Share "landing"                  ▼
                        Grid_Report/YYYY/MM/*.pdf        On-prem SQL Server 2022
                        (Terraform)                      staging.* ──► datamart.*
                                                         etl.file_load_log
                                                                    │
                                                                    ▼
                                                           Slack: 🟢 success / 🔴 failure
```

### Technology Stack

| Layer | Technology | Version |
|---|---|---|
| Containers | Docker Desktop + Compose (WSL2) | Compose v5.5 |
| Processing | Apache Spark (standalone: 1 master, 2 workers) | 4.1.3 (Scala 2.13, Java 17) |
| Table format (lab) | Delta Lake | 4.4.0 |
| Orchestration | Apache Airflow (LocalExecutor, Postgres metadata DB) | 3.3.2 |
| Interactive dev | JupyterLab (as a Spark driver) | Python 3.12 |
| File delivery | SFTP server | atmoz/sftp:alpine |
| Cloud landing | Azure File Share (StorageV2, LRS) | azurerm provider 5.8 |
| Infrastructure as Code | Terraform | 1.15 |
| Destination | SQL Server 2022 Developer (on-premise) | 16.0 |
| PDF parsing | pdfplumber | 0.11 |
| Alerting | Slack (bot token) via Airflow Slack provider | 9.10 |

---

## Solution Implementation

The implementation is split into eight sections. Each one builds on the previous section, ending with a scheduled, monitored, end-to-end pipeline.

---

## Section 1: Building the Homelab Foundation with Docker Compose

Everything starts with a single `docker-compose.yml` that runs the whole platform on one machine:

- a **Spark** master, two workers and a history server
- **JupyterLab** for interactive development
- **Airflow 3**: API server, scheduler, DAG processor and triggerer, backed by **Postgres**
- an **SFTP** server
- an observability stack, which is covered in Part 2

Optional components sit behind Compose **profiles**, so subsets of the platform can be started.

📸 *[Screenshot: `docker compose ps` with all services healthy]*

### Step 1: Pin Versions That Actually Work Together

The most important early decision was choosing versions that are compatible with each other, rather than simply the newest of each.

- **Spark 4.2.0 was the newest release, but Delta Lake 4.3 only supported Spark 4.0 and 4.1.** We settled on **Spark 4.1.3** with **Delta 4.4.0**.
- **Python 3.12 runs in every container.** In client mode, the Spark driver (Jupyter or Airflow) and the executors (the Spark workers) must run the **same Python minor version**, or PySpark refuses to run.

> **Issue:** `pip install delta-spark==4.3.1 pyspark==4.1.3` failed with *ResolutionImpossible*.
> **Fix:** Delta 4.3.1's Python package caps PySpark at 4.1.1. Delta **4.4.0**, released a month later, supports Spark 4.1 and 4.2.

### Step 2: Custom Images

We built three small images on top of the official ones:

- **Spark:** the official image, plus a Python 3.12 virtual environment and the Delta and SQL Server JDBC JARs **baked in**, so jobs never download dependencies at runtime.
- **Jupyter:** PySpark 4.1.3, the same JARs, `pdfplumber`, `pymssql` and the Azure SDK.
- **Airflow:** Java 17 and full PySpark, so `SparkSubmitOperator` can run `spark-submit`, plus the Spark, MSSQL and Slack providers.

```yaml
x-spark-build: &spark-build-args
  SPARK_VERSION: "4.1.3"
  DELTA_VERSION: "4.4.0"
  DELTA_SPARK_ARTIFACT: "delta-spark_4.1_2.13"
  MSSQL_JDBC_VERSION: "13.6.0.jre11"
```

> **Issue:** Writes from Jupyter failed with `Permission denied` on the shared data folder.
> **Fix:** The drivers and executors run as different Linux users (Jupyter 1000, Spark 185, Airflow 50000), and the Windows bind mount enforces Linux permissions. Setting `spark.hadoop.fs.permissions.umask-mode=000` makes Spark create shared files that any container can write to.

---

## Section 2: Generating the Daily Grid Reports

To simulate a real operator's output, a Python generator (`pdf_generator/`) produces the daily report PDFs.

- **Faker** creates a fictitious utility, *South Dakota Interconnection*, with its operator, balancing authority, transmission operator and four substations.
- **NumPy** generates **96 hourly readings per day** (24 hours × 4 substations) that follow realistic shapes:
  - load peaks around 18:00 and solar around 13:00
  - weather affects wind and solar output
  - equipment alerts lower the reliability index
- **reportlab** renders each report as a landscape PDF with a header, a summary table and a detail table.

Every report follows a strict naming convention, which the rest of the pipeline relies on:

```
Grid_Report/{YYYY}/{MM}/{region}_{YYYYMMDD}_grid_report.pdf
Grid_Report/2026/10/south_dakota_interconnection_20261002_grid_report.pdf
```

Two entry points cover history and the daily cadence:

```bash
# Historical backfill: 8-10 reports per month, Jan 2025 to last month (deterministic, seed 42)
python -m pdf_generator.grid_reports.generate_historical_reports

# Daily: yesterday's report (seeded from the date, so re-generating gives the same figures)
python -m pdf_generator.grid_reports.generate_report --date 2026-10-02
```

📸 *[Screenshot: a generated PDF report, first page]*

> **Why determinism matters:** because every report is generated from a known seed, we can rebuild the *exact* original numbers later. That became the ground truth for validating the parser in Section 6.

---

## Section 3: The SFTP Landing Zone

Many organizations still exchange files over SFTP, so the pipeline's entry point is an SFTP server running in Docker.

```yaml
sftp:
  image: atmoz/sftp:alpine
  command: ${SFTP_USERNAME}:${SFTP_PASSWORD}:::upload
  ports:
    - "${SFTP_PORT:-22}:22"
  volumes:
    - ./data/sftp/upload:/home/${SFTP_USERNAME}/upload
    - sftp-ssh:/etc/ssh        # persists the server's host keys across restarts
```

> **Issue:** The commonly copied example mounts host keys at `/etc/ssh/ssh_host_keys`, which the image never reads. The keys change on every restart, and clients warn that the host key has changed.
> **Fix:** Persist `/etc/ssh` in a named volume. Mounting the keys from a Windows folder also fails, because `sshd` rejects private keys with loose permissions.

### Migration Scripts: Full and Daily

Two Python scripts move reports from the local folder to SFTP:

- **Full migration** mirrors the whole `Grid_Report/YYYY/MM` tree.
- **Daily migration** splits each file name, extracts its date, and uploads only **yesterday's (UTC)** report. If there is no report for that day, it **stops cleanly with exit code 0**: a missing day is not an error.

Both scripts are built to be safe in production:

- **Idempotent:** a file already on the server with the same size *and* modification time is skipped.
- **Atomic:** each file is uploaded as `<name>.partial`, then renamed, so downstream readers never see half-written PDFs.
- **Verified:** the scripts refuse to connect if the server's SSH key fingerprint changes.

```python
def upload(self, local_path, remote_path):
    partial = remote_path + ".partial"
    self.sftp.put(str(local_path), partial, confirm=True)
    self.sftp.utime(partial, (atime, mtime))     # keep the source modification time
    self.sftp.posix_rename(partial, remote_path) # atomic: readers never see a partial file
```

📸 *[Screenshot: WinSCP / FileZilla showing upload/Grid_Report/2025, 2026]*

---

## Section 4: Azure File Share as the Cloud Landing Layer (Terraform)

The cloud landing layer holds raw files exactly as delivered, before any processing. We chose an **Azure File Share** for it. It keeps a true folder hierarchy and is easy to browse, and it suits a landing zone well, where raw files are held rather than queried.

### Step 1: Provision with Terraform

```hcl
resource "azurerm_storage_account" "landing" {
  name                     = "homelablandfileshare"
  resource_group_name      = data.azurerm_resource_group.datalake.name
  location                 = data.azurerm_resource_group.datalake.location
  account_kind             = "StorageV2"
  account_tier             = "Standard"
  account_replication_type = "LRS"
  https_traffic_only_enabled = true
  min_tls_version            = "TLS1_2"
}

resource "azurerm_storage_share" "landing" {
  name               = "landing"
  storage_account_id = azurerm_storage_account.landing.id   # azurerm 5.x: id, not name
  quota              = 5
  access_tier        = "TransactionOptimized"
}
```

Key decisions:

- **Pay-as-you-go** billing instead of *Provisioned v2*. At a few MB of data, provisioned capacity would be paying for headroom that's never used.
- **Transaction optimized** tier: the cheapest per operation, which suits small files moved by scripts.
- **Hierarchical namespace stays off.** That setting creates an ADLS Gen2 data lake, which is a different tool from a file share.
- **The existing resource group is only read** through a `data` source, so `terraform destroy` removes just the storage account and share.

📸 *[Screenshot: `terraform plan` showing 2 to add / Azure portal storage account]*

### Step 2: Land Files over HTTPS, Not SMB

File Shares are usually *mounted* over SMB on port 445. Many home and corporate networks block that port, and mounting network drives into Docker is awkward. Instead, the landing scripts use the **Azure Storage SDK over HTTPS (port 443)**. They stream each file straight from SFTP to Azure, with no local copy:

```python
with sftp.open(report.path, "rb") as source:
    source.prefetch(report.size)
    landing.upload_stream(target, source, report.size, report.mtime)  # .partial + rename, as on SFTP
```

The scripts follow the same pattern as the SFTP migration: **full** and **daily** variants, idempotent, atomic, and a clean exit-0 stop when there's no report for the day. They record the SFTP file's modification time as **file metadata**, so a regenerated report is detected and replaced.

**Result:** 188 reports landed in about 1.5 minutes. A re-run skipped all 188.

📸 *[Screenshot: Azure portal → File shares → landing → Grid_Report → 2026 → 10]*

---

## Section 5: Connecting to the On-Premise SQL Server

The destination is a **SQL Server 2022** instance on the host machine. Before building the load, a connectivity test script (`jobs/test_sqlserver_connection.py`) checks each layer and stops at the first failure with a hint:

1. **Network:** can it reach the host and port?
2. **Login:** is SQL authentication accepted?
3. **Server:** which version, database and client IP is it on?
4. **Database:** does the database exist, and can this login `CREATE TABLE`?
5. **Write test:** create, insert into and read a `#temp` table.

```
[PASS] Network   host.docker.internal (192.168.65.254):1433 reachable in 3 ms
[PASS] Login     as 'sa' (SQL authentication)
[PASS] Server    PC\MSSQLSERVER2022 | SQL Server 16.0.1200.5 Developer Edition (64-bit)
[PASS] Database  'MonitoringLab' is ONLINE | CREATE TABLE: yes | db_owner: yes
[PASS] Write     created, inserted and read a #temp table (2 rows)
```

> **Tip:** Containers reach the host's SQL Server through `host.docker.internal`. Docker Desktop keeps it pointed at the laptop's current IP, so the connection string survives DHCP changes. The SQL Server instance needs TCP/IP enabled on a static port (1433) and **mixed-mode authentication**, because containers can't use Windows authentication.

---

## Section 6: Transformation: PDF to SQL Server Data Mart

This is the heart of the pipeline. A **backfill notebook** in JupyterLab loads history, and a **daily Spark job** (Section 7) reuses exactly the same logic for one day.

### Step 1: Three Schemas, One Job Each

| Schema | Purpose | Tables |
|---|---|---|
| `datamart` | Final business tables only | `grid_operations_daily_summary` (1 row per report), `grid_operations_detail` (96 rows per report) |
| `staging` | Transient load tables, truncated each run | `staging.grid_operations_daily_summary`, `staging.grid_operations_detail` |
| `etl` | Pipeline bookkeeping | `etl.file_load_log` (one row per processed file per run) |

Keeping staging and logging out of `datamart` means business users only ever see curated tables.

### Step 2: The Load Pattern

```
Azure File Share ──download──► pdfplumber parse ──► Spark DataFrames + derived columns
        ──JDBC──► staging.*  ──T-SQL MERGE (single transaction)──► datamart.*  ──► etl.file_load_log
```

- **Derived columns:** `reading_datetime`, `renewable_pct_of_gen` and `is_operational`.
- **Bulk staging:** Spark writes the staging tables in bulk through the SQL Server JDBC driver.
- **One transaction:** a single SQL Server transaction runs both `MERGE`s, upserting the summary and detail tables. **Either both tables change or neither does**, so the data mart is never half-loaded.

```sql
MERGE datamart.grid_operations_detail WITH (HOLDLOCK) AS t
USING staging.grid_operations_detail AS s
   ON t.source_file = s.source_file AND t.reading_time = s.reading_time AND t.substation = s.substation
WHEN MATCHED THEN UPDATE SET ..., t.updated_at = SYSUTCDATETIME()
WHEN NOT MATCHED BY TARGET THEN INSERT (...) VALUES (...);
```

### Step 3: The Bug That Silently Dropped 75% of the Data

The first backfill run looked fine at a glance: 187 reports, no failures. But one warning repeated for every file:

```
WARNING  south_dakota_interconnection_20250101_grid_report.pdf: expected 96 detail rows, parsed 24
```

Only **one of four substations** was being parsed. Printing the raw text extracted from the PDF showed why:

```
00:00 East William Substation 437.0 435.9 120.0 ...            ← parsed
00:00 Johnsonland Switching Stat3io6n6.2 365.8 85.0 ...         ← NOT parsed
```

Longer substation names like *"Johnsonland **Switching Station**"* are wider than the PDF's Substation column, so the text **spills into the Gen (MW) column**. pdfplumber reads characters by their **position on the page**, so it interleaves the two: `Station` + `366.2` became `Stat3io6n6.2`. The regex then couldn't separate the name from the first number.

The same problem appeared in two other places:

- The footer's **"Generated" timestamp** overlaps the disclaimer text. As a result, `generated_at` was null for every report.
- **"Partly Cloudy"** overflows into the Status column and arrives glued to it, as `Partly CloudyMaintenance`.

**The fix:** read the text in the order it was **written to the PDF**, not in page-position order. pdfplumber supports this directly:

```python
layout_text = "\n".join(page.extract_text() for page in pdf.pages)                     # header & summary
flow_text   = "\n".join(page.extract_text(use_text_flow=True) for page in pdf.pages)   # hourly rows & footer
```

For the glued weather/status values, the parser splits off the known status at the end: *Operational*, *Maintenance* or *Alert*.

> **Lesson:** this bug also existed in the original Fabric version of this pipeline, unnoticed. It was only caught here because the loader **checks the row count of every report** against the expected 96 instead of trusting "no errors".

### Step 4: Validating Against Ground Truth

Because the generator is deterministic (Section 2), we rebuilt the original data for all 188 reports and compared it with what the parser extracts from the PDFs, value by value:

```
reports: 188 | values compared: 288,768 | mismatches: 0
```

📸 *[Screenshot: notebook step 11: "Parsed reports: 187 | detail rows: 17952 | warnings: 0 | failed: 0"]*

### Step 5: Safe to Re-run, by Design

Three layers stop data from being duplicated:

1. **Incremental filter:** reports whose `source_file` is already in `datamart` are skipped before anything is downloaded.
2. **`MERGE`, not `INSERT`:** if a report is deliberately reprocessed, the existing rows are **updated in place** and `updated_at` is stamped.
3. **Primary keys:** `source_file` for the summary, and `source_file + reading_time + substation` for the detail. SQL Server itself rejects duplicates.

A final **data-quality cell** checks the whole data mart:

- no report without exactly 96 detail rows
- no orphaned summary or detail rows
- no nulls in key header fields

---

## Section 7: Orchestration with Apache Airflow

### Step 1: From Notebook to Production Job

The notebook's single-day mode became a standalone Spark job, `jobs/grid_report_etl_to_sqlserver.py`. It uses the **same parsers, schemas, staging and `MERGE`**, and adds production behaviour:

- `--date` (defaulting to yesterday in UTC), `--reprocess`, and `--dry-run`, which parses but writes nothing
- Spark starts **only when there is something to load**, so a no-op day finishes in seconds
- exit codes Airflow understands: `0` loaded / already loaded / no file, `1` failure, `2` configuration error

### Step 2: The DAG

```python
@slack_notifications(report_date=REPORT_DATE, success_details=datamart_status)
@dag(
    dag_id="grid_report_daily",
    schedule="0 6 * * *",
    start_date=datetime(2026, 10, 1),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=5)},
)
def grid_report_daily():
    sftp_to_azure_fileshare = BashOperator(
        task_id="sftp_to_azure_fileshare",
        bash_command=f"python /opt/spark-jobs/land_daily_grid_report_sftp_to_azure.py --date {REPORT_DATE}",
    )
    fileshare_etl_sql_server = SparkSubmitOperator(
        task_id="fileshare_etl_sql_server",
        conn_id="spark_default",
        application="/opt/spark-jobs/grid_report_etl_to_sqlserver.py",
        application_args=["--date", REPORT_DATE],
    )
    sftp_to_azure_fileshare >> fileshare_etl_sql_server
```

📸 *[Screenshot: Airflow UI, grid_report_daily graph with both tasks green]*

**One date for the whole run.** Both tasks receive the same report date from the DAG run, rather than each computing "yesterday" itself:

```python
REPORT_DATE = "{{ ((logical_date or dag_run.run_after) - macros.timedelta(days=1)) | ds }}"
```

That makes runs **reproducible**: clearing or re-running last Tuesday's run reprocesses *Tuesday's* data, not today's yesterday.

**Client-mode Spark from Airflow.** With the LocalExecutor, `spark-submit` runs inside the scheduler container, which acts as the Spark driver. The executors run on the Spark workers, and they write to SQL Server through `host.docker.internal`.

### Step 3: Re-runs Are Free

Triggering the DAG again for a day that's already loaded completes in seconds and changes nothing:

```
Found 1 report(s): south_dakota_interconnection_20261002_grid_report.pdf
Skip    south_dakota_interconnection_20261002_grid_report.pdf (already in datamart.grid_operations_daily_summary)
Everything for 20261002 is already loaded. Nothing to do.
```

---

## Section 8: Proactive Alerting with Slack

A pipeline that fails silently is worse than no pipeline. The DAG posts to Slack using a **bot token**:

- **🔴 Failure:** one message per task that fails *after its retries are used up*, with the task, report date, attempt, error and a direct link to the log.
- **🟢 Success:** one message per successful run, stating what's actually in the data mart for that day. Days with no report show up as 🟡 instead of looking like a silent success.

📸 *[Screenshot: Slack message "🟢 Pipeline succeeded … Report date: 2026-10-02 … in datamart: 1 report(s), 96 detail rows"]*

### A Reusable Decorator

Rather than repeating notification code in every DAG, it lives in a shared module (`dags/common/slack_notifications.py`) and is applied as a decorator:

```python
from common.slack_notifications import slack_notifications

@slack_notifications()          # failure alert on every task + one success message per run
@dag(dag_id="any_pipeline", ...)
def any_pipeline(): ...
```

The decorator builds the DAG, adds the Slack failure notifier to every task (keeping any existing callbacks) and sets the success callback. Credentials stay in `.env` as an Airflow connection, never in DAG code.

> **Issue:** Failure alerts worked, but the success message never arrived.
> **Fix:** In Airflow 3, the scheduler only runs a DAG's success callback if the serialized DAG has `has_on_success_callback = True`, and that flag is computed **once, when the DAG object is created**. Our decorator adds the callback *afterwards*, so the flag stayed false. Setting `dag.has_on_success_callback = True` in the decorator solved it. (Task-level callbacks are checked live, which is why failures were unaffected.)

---

## Developer Analysis

With the data in SQL Server, analysts can query it straight away with T-SQL in SSMS or Azure Data Studio.

```sql
-- ========= Reliability risk: non-operational hours by substation =========
SELECT  substation,
        status,
        COUNT(*)                AS readings,
        AVG(reliability_index)  AS avg_reliability,
        MIN(reliability_index)  AS worst_reliability
FROM    datamart.grid_operations_detail
WHERE   status <> 'Operational'
GROUP BY substation, status
ORDER BY readings DESC;
```

📸 *[Screenshot: query result]*

```sql
-- ========= Daily peak load and renewable share per substation =========
SELECT  report_date,
        substation,
        MAX(load_mw)               AS peak_load_mw,
        AVG(renewable_pct_of_gen)  AS avg_renewable_pct,
        AVG(lmp_usd_per_mwh)       AS avg_lmp_usd_per_mwh
FROM    datamart.grid_operations_detail
GROUP BY report_date, substation
ORDER BY report_date DESC, peak_load_mw DESC;
```

```sql
-- ========= Monthly trend: peak load, renewable share and reliability =========
SELECT  FORMAT(report_date, 'yyyy-MM')              AS [month],
        COUNT(*)                                    AS reports,
        MAX(peak_load_mw)                           AS max_peak_load_mw,
        CAST(AVG(renewable_share_pct) AS DECIMAL(5,1)) AS avg_renewable_share_pct,
        CAST(AVG(avg_reliability_index) AS DECIMAL(5,1)) AS avg_reliability
FROM    datamart.grid_operations_daily_summary
GROUP BY FORMAT(report_date, 'yyyy-MM')
ORDER BY [month];
```

```sql
-- ========= Pipeline audit: what was loaded, when, by which pipeline =========
SELECT  pipeline, status, COUNT(*) AS files, SUM(detail_rows) AS detail_rows,
        MIN(logged_at) AS first_load, MAX(logged_at) AS last_load
FROM    etl.file_load_log
GROUP BY pipeline, status;
```

---

## Lessons Learned

- **Check compatibility before choosing "latest".** The newest Spark wasn't supported by the newest Delta Lake, and one library's packaging cap blocked an otherwise valid combination.
- **Validate counts, not just errors.** The parsing bug produced *no errors*: it quietly kept 24 of 96 rows. An expected-row-count check caught it, and deterministic ground truth proved the fix.
- **Design every step to be re-run safely.** Skip what's already done, write atomically, `MERGE` instead of `INSERT`, and put primary keys on the targets. Retries and backfills then become routine instead of risky.
- **Keep business schemas clean.** Staging and audit tables belong outside `datamart`.
- **Prefer HTTPS APIs over network mounts in hybrid setups.** Port 445 is often blocked; port 443 rarely is.
- **Notify on success, too.** For a daily job, a quiet day and a broken day look the same unless something confirms the run happened.

---

## Conclusion

We rebuilt the energy reporting pipeline as a hybrid, open-source-first system. Here is the path the data travels each day:

1. **The generator produces the daily report.** A deterministic Faker + reportlab generator creates the operator's PDF, with a strict naming convention.
2. **SFTP receives it.** Idempotent, atomic migration scripts deliver each report, matched by the date in its file name, and stop cleanly when a day is missing.
3. **Azure File Share lands it.** A Terraform-provisioned file share holds the raw files in the cloud, fed over HTTPS straight from SFTP.
4. **Spark transforms it.** PDFs are parsed with a fix for the column-overflow problem and validated against ground truth with **0 mismatches in 288,768 values**, then staged in SQL Server through JDBC.
5. **SQL Server publishes it.** A single-transaction `MERGE` updates the curated `datamart` schema, now holding **189 reports and 18,144 hourly readings**, with an audit trail in `etl.file_load_log`.
6. **Airflow orchestrates it.** A daily T-1 DAG with retries runs the two-step pipeline, and every run is reproducible and safe to repeat.
7. **Slack reports on it:** a 🟢 message when a run succeeds, and a 🔴 alert with a log link when one fails.

The result is a pipeline that **fails loudly, retries safely, and never duplicates data**, connecting open-source compute, Azure cloud storage and on-premise SQL Server.

---

## What's Next?

All the code for this project is available on [GitHub](#): the Docker Compose stack, Terraform, the generator, migration and ETL jobs, the notebook and the DAG.

This wraps up **Part 1**. In **Part 2**, we'll add **monitoring and observability**:

- **Prometheus and Grafana** for Spark, Airflow and container metrics
- **Loki** for centralized logs
- **OpenTelemetry** for Airflow metrics
- a dashboard that shows the health of the whole pipeline at a glance

Later in the series, we'll build an **AI agent** on top of the data mart that can answer questions about grid operations, summarize trends and flag anomalies.
