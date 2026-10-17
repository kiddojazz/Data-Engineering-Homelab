# Data Engineering Homelab: Energy Intelligence Platform

A hybrid data platform built on a single-host Docker Compose stack. Daily energy-grid operations reports (synthetic PDFs) move from **SFTP** to an **Azure File Share** landing layer, are parsed and transformed with **Apache Spark**, and are loaded into an **on-premise SQL Server** data mart. **Apache Airflow** orchestrates the run, **Slack** reports every run, and **Prometheus, Grafana and Loki** provide observability.

On top of the data mart, **GridIQ** is an AI data agent. Business users ask questions in **Slack**, and a **LangGraph** agent using **gpt-4o on Microsoft Foundry** answers them with guarded, read-only text-to-SQL. Every run is traced in a self-hosted **Langfuse**.

```
Part 1  PDF generator → SFTP → Azure File Share → Spark ETL → SQL Server (staging → datamart) → Slack
                          └──────── Airflow DAG: grid_report_daily (06:00 UTC, T-1) ────────┘

Part 2  Slack @eagle-bot → ngrok → agent-api (FastAPI + LangGraph) ⇄ gpt-4o (Foundry)
                                        └─ SQL firewall → read-only login → datamart
                                        └─ traces → Langfuse · memory → Postgres
```

## Documentation

| Document | What it covers |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Part 1 platform architecture (Mermaid diagram, components, flows) |
| [docs/article_part1_hybrid_energy_pipeline.md](docs/article_part1_hybrid_energy_pipeline.md) | Part 1 article: SFTP → Azure → Spark → SQL Server, Airflow and Slack alerts |
| [docs/agent_architecture.md](docs/agent_architecture.md) | GridIQ agent architecture: system and LangGraph diagrams, guardrail layers, components |
| [docs/article_part2_gridiq_ai_agent.md](docs/article_part2_gridiq_ai_agent.md) | Part 2 article: Foundry, LangGraph, guardrails, memory, FastAPI, ngrok, Slack, Langfuse, red-team |
| [agent/README.md](agent/README.md) | Agent setup, use, tests and service |
| [agent/redteam/README.md](agent/redteam/README.md) | Red-team process: cases, Slack execution, scoring from Langfuse |

> All report data is synthetically generated with Faker; nothing represents real grid operations.

## What's in the stack

- **Spark** processes the data (Delta Lake is available for lakehouse experiments).
- **Airflow** orchestrates the jobs.
- **Jupyter** is for interactive development and backfills.
- **SFTP** (`atmoz/sftp`) is the file-delivery landing zone.
- **Azure File Share** is the cloud landing layer, provisioned with Terraform in `infra/azure-landing/`.
- **SQL Server** is the destination, running outside Docker.
- **Observability:** Prometheus, Grafana, Loki, Alloy and OpenTelemetry collect metrics and logs.
- **AI agent (profile `agent`):**
  - `agent-api` runs FastAPI with LangGraph;
  - Langfuse v4 runs as web, worker, Postgres, ClickHouse, Redis and MinIO;
  - an ngrok tunnel lets Slack reach the agent.

## GridIQ AI agent (Part 2)

| Piece | Code | Notes |
|---|---|---|
| Agent graph | `agent/graph.py` | LangGraph: `input_guard` → `agent` ⇄ `tools` → `grounding_check` |
| Only tool + SQL firewall | `agent/tools.py`, `agent/sql_guard.py` | One read-only `SELECT` on the two `datamart` tables, validated with sqlglot |
| Read-only database | `agent/db.py`, `agent/sql/create_readonly_login.sql` | Login `grid_agent_reader`; always rollback, 30 s timeout, 200-row cap |
| Guardrails and prompts | `agent/guardrails.py`, `agent/prompts.py` | Input classification, Prompt Shields handling, numeric grounding check |
| Service | `agent/api.py`, `docker/agent/Dockerfile` | Slack Events API (signature-verified), `/ask`, `/health` |
| Memory and tracing | `agent/memory.py`, `agent/observability.py` | Postgres checkpointer; Langfuse CallbackHandler |
| Tests | `agent/tests/` | 66 tests, no Azure or SQL Server needed: `python -m pytest agent/tests -q` |
| Red-team | `agent/redteam/` | 33 Slack attack cases scored from Langfuse: 33/33 passed |

Setup steps are in [agent/README.md](agent/README.md); Azure OpenAI, Langfuse, Slack and ngrok settings go in `.env` (see `.env.example`).

## Energy grid report pipeline

| Step | Code | Notes |
|---|---|---|
| Generate the daily PDF report | `pdf_generator/` | `python -m pdf_generator.grid_reports.generate_report` (yesterday) or `generate_historical_reports` (backfill) |
| Local folder → SFTP | `jobs/migrate_daily_grid_report_to_sftp.py`, `jobs/migrate_grid_reports_to_sftp.py` | Idempotent and atomic; the daily script stops cleanly when there is no file |
| SFTP → Azure File Share | `jobs/land_daily_grid_report_sftp_to_azure.py`, `jobs/land_grid_reports_sftp_to_azure.py` | Azure SDK over HTTPS (no SMB mount) |
| Azure File Share → SQL Server (daily) | `jobs/grid_report_etl_to_sqlserver.py` | spark-submit; staging → `MERGE` → `datamart`, audit in `etl.file_load_log` |
| Azure File Share → SQL Server (backfill) | `notebooks/01_backfill_grid_report_to_sqlserver.ipynb` | Same logic, over a month range |
| Orchestration and alerts | `dags/grid_report_daily_dag.py`, `dags/common/` | `sftp_to_azure_fileshare >> fileshare_etl_sql_server`; Slack via the `@slack_notifications` decorator |

Settings for SFTP, Azure, SQL Server and Slack go in `.env`; see `.env.example`.

## Versions

| Component | Version | Notes |
|---|---|---|
| Spark | 4.1.3 (Scala 2.13, Java 17) | Custom image in `docker/spark`, built on the official `spark` image |
| Delta Lake | 4.4.0 (`delta-spark_4.1_2.13`) | Supports Spark 4.1 and 4.2 |
| Python | 3.12 everywhere | Drivers (Jupyter, Airflow) and executors must use the same minor version |
| Airflow | 3.3.2, LocalExecutor, Postgres 16 | Providers: apache-spark 6.3.2, microsoft-mssql 4.7.1 |
| SQL Server JDBC | 13.6.0.jre11 | Built into every image that runs Spark |
| Prometheus / Grafana / Loki / Alloy | 3.15.0 / 13.2.3 / 3.7.8 / 1.20.1 | |
| OTel Collector (contrib) / cAdvisor | 0.161.0 / 0.60.6 | |

To upgrade Spark, Delta or the JDBC driver, change `x-spark-build` in `docker-compose.yml` and the `ARG` defaults in `docker/*/Dockerfile`. Then rebuild all three images.

## Quick start

```powershell
copy .env.example .env      # then set FERNET_KEY, JWT secret and the MSSQL_* values
docker compose up -d --build
docker compose ps
```

`COMPOSE_PROFILES` in `.env` decides which parts start. It defaults to `airflow,observability,agent`, so a plain `up` starts everything. Spark and Jupyter always start. Drop `agent` if you don't need the AI agent and Langfuse.

- To run only Spark and Jupyter, set `COMPOSE_PROFILES=` in `.env`, and also set `AIRFLOW_OTEL_ON=False` if Airflow will run without observability.
- Stop everything with `docker compose down`. Add `-v` to also delete the Postgres, Prometheus, Loki and Grafana data.

## URLs

| Service | URL | Login |
|---|---|---|
| Spark master UI | http://localhost:8080 | |
| Spark workers | http://localhost:8081, http://localhost:8082 | |
| Spark history server | http://localhost:18080 | |
| Spark driver UI (notebook session) | http://localhost:4040 | |
| JupyterLab | http://localhost:8888/lab?token=homelab | token from `JUPYTER_TOKEN` |
| Airflow | http://localhost:8088 | airflow / airflow |
| SFTP | `sftp -P 22 homelab@localhost` (or WinSCP / FileZilla) | `SFTP_USERNAME` / `SFTP_PASSWORD`. Upload into `upload/`, which lands in `data/sftp/upload` and is visible to Spark as `/opt/spark-data/sftp/upload` |
| Grafana | http://localhost:3000 | admin / admin. Open the dashboard at **Homelab → Homelab overview** |
| Prometheus | http://localhost:9090 | |
| Alloy UI | http://localhost:12345 | |
| cAdvisor | http://localhost:8090 | |
| Langfuse (agent traces) | http://localhost:3001 | `LANGFUSE_INIT_USER_EMAIL` / `LANGFUSE_INIT_USER_PASSWORD` |
| GridIQ agent API | http://localhost:8010/health | local only; Slack reaches it through ngrok |
| ngrok inspector | http://localhost:4041 | |

## Layout

```
docker-compose.yml     all services; profiles: airflow, observability, agent
docker/spark|jupyter|airflow|agent/   custom images
agent/                 GridIQ AI agent (LangGraph, guardrails, FastAPI service, tests, red-team)
docs/                  architecture diagrams and the Part 1 / Part 2 articles
conf/spark/            spark-defaults.conf, metrics.properties, log4j2.properties
                       (mounted into every Spark container at /opt/spark-conf)
jobs/                  PySpark jobs      -> /opt/spark-jobs  (all Spark containers + Airflow)
data/                  input and output  -> /opt/spark-data
  data/Grid_Report/YYYY/MM/   grid report PDFs (energy project), see pdf_generator/README.md
pdf_generator/         Faker + reportlab generator for the grid report PDFs -> /opt/pdf_generator (Jupyter, Airflow)
dags/                  Airflow DAGs
notebooks/             Jupyter work dir  -> /home/jovyan/work
sql/                   T-SQL scripts (Phase 4)
observability/         prometheus, otel, loki, alloy, grafana (provisioned data sources + dashboard)
```

## How it fits together

- **Client mode.** The Spark driver runs where the code starts: in Jupyter, in `airflow-scheduler` (LocalExecutor runs tasks there), or in `spark-master` when you run `spark-submit` inside that container. Executors run on the workers. Each driver container's `hostname` is set to its service name so the executors can connect back to it.
- **Shared paths.** Jobs and data are mounted at identical paths in every container. Always use `/opt/spark-data/...` paths in your code.
- **Permissions.** Containers run as different users (spark 185, jovyan 1000, airflow 50000). The Windows bind mount keeps Linux permissions, so Spark writes with umask 000 (`spark.hadoop.fs.permissions.umask-mode`). Any container can then write into folders another one created.
- **Delta** is enabled for every session through `spark-defaults.conf`. `` MERGE INTO delta.`/path` `` works with no extra setup.
- **Airflow connections** come from environment variables, so they don't appear on the UI's Connections page:
  - `spark_default` points at spark://spark-master:7077 in client mode.
  - `mssql_default` points at `MSSQL_HOST` from `.env`, which defaults to `host.docker.internal:1433`.
- **Metrics.**
  - Airflow sends metrics over OTLP to `otel-collector`, which exposes them on :8889 for Prometheus to scrape.
  - Spark master and workers expose Prometheus metrics on their UI ports.
  - cAdvisor supplies container CPU and memory.
- **Logs.** Alloy tails every container in this Compose project and pushes the logs to Loki. The labels are `service`, `container`, `project` and `stream`.

## Checks

```powershell
# Spark: batch job on the cluster
docker compose exec spark-master spark-submit /opt/spark-jobs/sample_batch.py

# Jupyter: open notebooks/00_smoke_test.ipynb and run all cells
#   (checks Spark session, executor Python, Delta MERGE, SQL Server JDBC)

# Airflow: unpause and trigger the sample DAG ([check_input, check_cluster] >> run_sample_batch >> list_output)
docker compose exec airflow-scheduler airflow dags unpause sample_spark_dag
docker compose exec airflow-scheduler airflow dags trigger sample_spark_dag
```

## External SQL Server

SQL Server runs on Windows, outside Docker. Containers reach it at `host.docker.internal:1433`. Before using it:

1. In SQL Server Configuration Manager, enable TCP/IP on port 1433 and restart the service.
2. Enable SQL Server and Windows authentication mode, and set up a SQL login.
3. Allow inbound TCP 1433 in Windows Firewall. This may not be needed, because Docker Desktop traffic usually counts as local.
4. Put the login in `MSSQL_*` in `.env`, then run `docker compose up -d` to apply it.

## Tips

- **Resources.** The two workers are set to 4 cores and 6 GB each (`SPARK_WORKER_*` in `.env`). Each app is limited to 4 cores (`spark.cores.max`), so Jupyter and Airflow jobs can run at the same time. Give WSL enough memory in `%UserProfile%\.wslconfig`:
  ```ini
  [wsl2]
  memory=20GB
  ```
- **Bind mount speed.** Mounts from `C:\` are slow for large data. For heavier workloads, move the project into the WSL filesystem, e.g. `\\wsl$\Ubuntu\home\<you>\spark-homelab`.
- **Spark log level.** Set it in `conf/spark/log4j2.properties`; the default is WARN.
- **Grafana dashboard edits.** If you change a dashboard JSON file, restart Grafana (`docker compose restart grafana`). File watching over the Windows mount isn't reliable.
