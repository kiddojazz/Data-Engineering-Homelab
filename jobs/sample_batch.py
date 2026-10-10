"""Sample batch job: read CSV -> transform -> write Parquet and Delta.

Run it from the master container:
    docker compose exec spark-master spark-submit /opt/spark-jobs/sample_batch.py
or from Airflow via SparkSubmitOperator (dags/sample_spark_dag.py).
"""
import argparse

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

DATA_ROOT = "/opt/spark-data"


def main(input_path: str, output_root: str) -> None:
    spark = SparkSession.builder.appName("sample_batch").getOrCreate()

    sales = (
        spark.read.option("header", True)
        .option("inferSchema", True)
        .csv(input_path)
        .withColumn("order_date", F.to_date("order_date"))
        .withColumn("revenue", F.round(F.col("quantity") * F.col("unit_price"), 2))
    )

    by_region = (
        sales.groupBy("region")
        .agg(
            F.countDistinct("order_id").alias("orders"),
            F.sum("quantity").alias("units"),
            F.round(F.sum("revenue"), 2).alias("revenue"),
        )
        .orderBy(F.desc("revenue"))
    )

    by_region.show(truncate=False)

    sales.write.mode("overwrite").partitionBy("region").parquet(f"{output_root}/sales_parquet")
    by_region.write.format("delta").mode("overwrite").save(f"{output_root}/region_summary_delta")

    print(f"Wrote {sales.count()} sales rows and {by_region.count()} region rows to {output_root}")
    spark.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=f"{DATA_ROOT}/raw/sample_sales.csv")
    parser.add_argument("--output", default=f"{DATA_ROOT}/output")
    args = parser.parse_args()
    main(args.input, args.output)
