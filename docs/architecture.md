# Energy Intelligence Platform: Architecture

Hybrid data platform: open-source compute and orchestration in Docker, an Azure landing layer, an on-premise SQL Server data mart, Slack alerting and an observability stack.

> **Importing into draw.io:** *Arrange → Insert → Advanced → Mermaid*, then paste the code block below (without the ```mermaid fence). Each box and arrow becomes an editable shape.

## Diagram

```mermaid
flowchart LR

  %% ---------- Sources ----------
  subgraph SRC["1. Report source (Windows host)"]
    GEN["PDF generator<br/>Faker + reportlab"]
    LOCAL["Local folder<br/>data/Grid_Report/YYYY/MM"]
  end

  %% ---------- Landing ----------
  subgraph LAND["2. SFTP landing (Docker)"]
    SFTP["SFTP server<br/>atmoz/sftp :22<br/>upload/Grid_Report"]
  end

  %% ---------- Azure ----------
  subgraph AZ["3. Azure cloud landing (Terraform)"]
    AFS["Azure File Share<br/>homelablandfileshare / landing<br/>Grid_Report/YYYY/MM"]
  end

  %% ---------- Orchestration ----------
  subgraph ORCH["4. Orchestration (Docker)"]
    AF["Airflow 3.3.2<br/>API server :8088, scheduler,<br/>DAG processor, triggerer"]
    PG[("Postgres 16<br/>Airflow metadata")]
    T1["Task 1<br/>sftp_to_azure_fileshare"]
    T2["Task 2<br/>fileshare_etl_sql_server"]
  end

  %% ---------- Processing ----------
  subgraph PROC["5. Processing (Docker)"]
    SM["Spark master 4.1.3<br/>:8080 UI / :7077"]
    SW["Spark workers x2<br/>4 cores, 6 GB each"]
    SH["Spark history server<br/>:18080"]
    JUP["JupyterLab :8888<br/>backfill notebook"]
  end

  %% ---------- Destination ----------
  subgraph DB["6. On-premise SQL Server 2022 (MonitoringLab)"]
    STG[("staging<br/>grid_operations_*")]
    DM[("datamart<br/>daily_summary + detail")]
    ETL[("etl<br/>file_load_log")]
  end

  %% ---------- Alerting ----------
  SLACK["7. Slack channel<br/>success / no report / failure"]

  %% ---------- Observability ----------
  subgraph OBS["8. Observability (Docker)"]
    OTEL["OTel Collector<br/>:4317 / :4318"]
    CADV["cAdvisor :8090"]
    ALLOY["Grafana Alloy :12345"]
    PROM[("Prometheus :9090<br/>15-day retention")]
    LOKI[("Loki :3100<br/>7-day retention")]
    GRAF["Grafana :3000<br/>Homelab overview"]
  end

  %% ---------- Data flow ----------
  GEN -->|"daily + backfill PDFs"| LOCAL
  LOCAL -->|"migrate_*_to_sftp.py<br/>(paramiko, atomic upload)"| SFTP
  AF -->|"06:00 UTC, T-1"| T1
  T1 -->|"then"| T2
  SFTP -->|"land_*_sftp_to_azure.py"| T1
  T1 -->|"HTTPS :443<br/>Azure SDK"| AFS
  AFS -->|"download PDFs"| T2
  T2 -->|"spark-submit<br/>client mode"| SM
  SM --> SW
  SW -->|"JDBC bulk write"| STG
  STG -->|"T-SQL MERGE<br/>one transaction"| DM
  T2 -->|"per-file status"| ETL
  JUP -->|"backfill, same logic"| SM
  AFS -->|"backfill read"| JUP
  AF --- PG
  SM -.->|"event logs"| SH

  %% ---------- Alerting flow ----------
  AF -->|"bot token callbacks"| SLACK

  %% ---------- Observability flow ----------
  AF -.->|"OTLP metrics"| OTEL
  OTEL -.->|":8889"| PROM
  SM -.->|"/metrics"| PROM
  SW -.->|"/metrics"| PROM
  CADV -.->|"container stats"| PROM
  ALLOY -.->|"container logs"| LOKI
  PROM -.-> GRAF
  LOKI -.-> GRAF
```

**Legend:** solid arrows = data and control flow · dotted arrows = monitoring signals · cylinders = storage.

## Components

| # | Layer | Component | Where | Port(s) | Notes |
|---|---|---|---|---|---|
| 1 | Source | PDF generator (`pdf_generator/`) | Windows host / Jupyter | — | Faker + reportlab; deterministic seeds |
| 2 | Landing | SFTP server | Docker | 22 | `atmoz/sftp:alpine`, host keys in a volume |
| 3 | Cloud landing | Azure File Share `landing` | Azure, UK South, `rg-datalake-test-uksouth` | 443 (HTTPS) | StorageV2, LRS, Transaction optimized; Terraform in `infra/azure-landing/` |
| 4 | Orchestration | Airflow 3.3.2 (LocalExecutor) | Docker | 8088 | DAG `grid_report_daily`; Postgres 16 metadata DB |
| 5 | Processing | Spark 4.1.3 master, 2 workers, history server | Docker | 8080, 7077, 8081/8082, 18080 | Delta 4.4.0, Python 3.12, SQL Server JDBC driver built in |
| 5 | Processing | JupyterLab | Docker | 8888, 4040 | Backfill notebook `01_backfill_grid_report_to_sqlserver.ipynb` |
| 6 | Destination | SQL Server 2022 Developer | On-premise (Windows host) | 1433 | Database `MonitoringLab`: schemas `staging`, `datamart`, `etl` |
| 7 | Alerting | Slack | SaaS | — | Bot token; `@slack_notifications` decorator in `dags/common/` |
| 8 | Observability | Prometheus | Docker | 9090 | 15-day retention |
| 8 | Observability | Grafana | Docker | 3000 | "Homelab overview" dashboard, 24 panels |
| 8 | Observability | Loki | Docker | 3100 | 7-day retention |
| 8 | Observability | Grafana Alloy | Docker | 12345 | Ships Docker container logs to Loki |
| 8 | Observability | OpenTelemetry Collector | Docker | 4317, 4318 | Airflow metrics in, Prometheus exporter on 8889 |
| 8 | Observability | cAdvisor | Docker | 8090 | Container CPU, memory, network |

## Flows

| Flow | Path | Trigger |
|---|---|---|
| Report delivery | Generator → local folder → SFTP | Manual / upstream (`migrate_daily_grid_report_to_sftp.py`) |
| Cloud landing | SFTP → Azure File Share | Airflow task 1 at 06:00 UTC (T-1) |
| Daily ETL | Azure File Share → Spark → `staging` → `MERGE` → `datamart`, plus `etl.file_load_log` | Airflow task 2, after task 1 |
| Backfill | Azure File Share → Jupyter + Spark → `staging` → `MERGE` → `datamart` | Manual, from the notebook |
| Alerting | Airflow → Slack | Every DAG run (success) and every failed task (after 2 retries) |
| Metrics | Spark, Airflow (via OTel Collector), cAdvisor → Prometheus → Grafana | Continuous, every 15 s |
| Logs | All containers → Alloy → Loki → Grafana | Continuous |

## Suggested draw.io layout

- **Left to right, following the data:** Source → SFTP → Azure → Airflow and Spark → SQL Server.
- **Observability as a band along the bottom,** with dotted lines up to the components it watches.
- **Slack** in the top-right corner, fed by Airflow.
- **Three background zones** for where things run: *Windows host / on-premise* (generator, SQL Server), *Docker homelab* (SFTP, Airflow, Spark, Jupyter, observability), and *Azure* (File Share).
