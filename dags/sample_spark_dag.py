"""Sample DAG: two parallel pre-checks, then a Spark job on the cluster, then a report.

    [check_input, check_cluster] >> run_sample_batch >> list_output
"""
from datetime import datetime

from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator
from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG

INPUT = "/opt/spark-data/raw/sample_sales.csv"
OUTPUT = "/opt/spark-data/output/airflow"

with DAG(
    dag_id="sample_spark_dag",
    start_date=datetime(2026, 9, 1),
    schedule=None,
    catchup=False,
    tags=["spark", "sample"],
) as dag:
    check_input = BashOperator(
        task_id="check_input",
        bash_command=f"test -s {INPUT} && wc -l {INPUT}",
    )

    check_cluster = BashOperator(
        task_id="check_cluster",
        bash_command=(
            "curl -fsS http://spark-master:8080/json/ "
            "| python -c \"import json,sys; d=json.load(sys.stdin); "
            "print('status', d['status'], 'workers', d['aliveworkers']); "
            "sys.exit(0 if d['aliveworkers'] > 0 else 1)\""
        ),
    )

    run_sample_batch = SparkSubmitOperator(
        task_id="run_sample_batch",
        conn_id="spark_default",
        application="/opt/spark-jobs/sample_batch.py",
        application_args=["--input", INPUT, "--output", OUTPUT],
        name="airflow_sample_batch",
    )

    list_output = BashOperator(
        task_id="list_output",
        bash_command=f"ls -R {OUTPUT} | head -40",
    )

    [check_input, check_cluster] >> run_sample_batch >> list_output
