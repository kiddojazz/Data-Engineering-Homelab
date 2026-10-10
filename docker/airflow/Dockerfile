# syntax=docker/dockerfile:1.7
# Airflow 3 with Java + PySpark so SparkSubmitOperator can run spark-submit (client mode)
# against spark://spark-master:7077. Python 3.12 matches the Spark cluster and Jupyter.
ARG AIRFLOW_VERSION=3.3.2
FROM apache/airflow:${AIRFLOW_VERSION}-python3.12

ARG AIRFLOW_VERSION=3.3.2
ARG SPARK_VERSION=4.1.3
ARG DELTA_VERSION=4.4.0
ARG DELTA_SPARK_ARTIFACT=delta-spark_4.1_2.13
ARG MSSQL_JDBC_VERSION=13.6.0.jre11

USER root

RUN set -ex; \
    apt-get update; \
    apt-get install -y --no-install-recommends openjdk-17-jre-headless procps; \
    apt-get autoremove -yqq --purge; \
    rm -rf /var/lib/apt/lists/*; \
    # Executors run PYSPARK_PYTHON=/opt/venv/bin/python; make the same path valid here.
    mkdir -p /opt/venv/bin /opt/extra-jars; \
    ln -s "$(readlink -f "$(command -v python3)")" /opt/venv/bin/python

ADD --chmod=644 https://repo1.maven.org/maven2/io/delta/${DELTA_SPARK_ARTIFACT}/${DELTA_VERSION}/${DELTA_SPARK_ARTIFACT}-${DELTA_VERSION}.jar /opt/extra-jars/
ADD --chmod=644 https://repo1.maven.org/maven2/io/delta/delta-storage/${DELTA_VERSION}/delta-storage-${DELTA_VERSION}.jar /opt/extra-jars/
ADD --chmod=644 https://repo1.maven.org/maven2/com/microsoft/sqlserver/mssql-jdbc/${MSSQL_JDBC_VERSION}/mssql-jdbc-${MSSQL_JDBC_VERSION}.jar /opt/extra-jars/

USER airflow

# 1) Providers, pinned by the official constraints file for this Airflow/Python version.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir "apache-airflow==${AIRFLOW_VERSION}" -r /tmp/requirements.txt \
      --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-3.12.txt"

# 2) Full PySpark matching the cluster. The spark provider pulls in the thin `pyspark-client`
#    (Spark Connect only, same `pyspark` module); replace it with the full distribution,
#    which ships spark-submit and the JVM jars.
RUN pip uninstall -y pyspark-client \
 && pip install --no-cache-dir "apache-airflow==${AIRFLOW_VERSION}" \
      "pyspark==${SPARK_VERSION}" "delta-spark==${DELTA_VERSION}" \
 && cp /opt/extra-jars/*.jar "$(python -c 'import os, pyspark; print(os.path.dirname(pyspark.__file__))')/jars/" \
 && spark-submit --version 2>&1 | grep -m1 -i version

ENV SPARK_CONF_DIR=/opt/spark-conf \
    PYSPARK_PYTHON=/opt/venv/bin/python
