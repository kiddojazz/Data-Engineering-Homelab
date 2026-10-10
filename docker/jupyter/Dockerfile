# syntax=docker/dockerfile:1.7
# JupyterLab acting as a Spark driver (client mode) against spark://spark-master:7077.
# Python 3.12 + PySpark/Delta versions must match the cluster image (docker/spark).
FROM quay.io/jupyter/base-notebook:python-3.12

ARG SPARK_VERSION=4.1.3
ARG DELTA_VERSION=4.4.0
ARG DELTA_SPARK_ARTIFACT=delta-spark_4.1_2.13
ARG MSSQL_JDBC_VERSION=13.6.0.jre11

USER root

RUN set -ex; \
    apt-get update; \
    apt-get install -y --no-install-recommends openjdk-17-jre-headless curl procps; \
    rm -rf /var/lib/apt/lists/*; \
    # Executors run PYSPARK_PYTHON=/opt/venv/bin/python; make the same path valid here.
    mkdir -p /opt/venv/bin; \
    ln -s /opt/conda/bin/python /opt/venv/bin/python; \
    # Shared /opt/spark-data is written by several uids: run the server (and kernels) with umask 0000.
    echo 'umask 0000' > /usr/local/bin/before-notebook.d/10-umask.sh

USER ${NB_UID}

# Faker/reportlab: grid report PDF generator (pdf_generator/); pdfplumber: parsing the PDFs;
# azure-storage-file-share: reading the landing layer (same version as Airflow's constraints)
RUN pip install --no-cache-dir \
      "pyspark==${SPARK_VERSION}" \
      "delta-spark==${DELTA_VERSION}" \
      "numpy>=1.26" "pandas>=2.2" "pyarrow>=15" \
      pymssql \
      "Faker==40.28.1" "reportlab==5.0.0" pdfplumber \
      "azure-storage-file-share==12.26.0"

# Same extra jars as the cluster, placed on the driver classpath (pip pyspark's jars dir).
ARG PYSPARK_JARS=/opt/conda/lib/python3.12/site-packages/pyspark/jars
ADD --chmod=644 https://repo1.maven.org/maven2/io/delta/${DELTA_SPARK_ARTIFACT}/${DELTA_VERSION}/${DELTA_SPARK_ARTIFACT}-${DELTA_VERSION}.jar ${PYSPARK_JARS}/
ADD --chmod=644 https://repo1.maven.org/maven2/io/delta/delta-storage/${DELTA_VERSION}/delta-storage-${DELTA_VERSION}.jar ${PYSPARK_JARS}/
ADD --chmod=644 https://repo1.maven.org/maven2/com/microsoft/sqlserver/mssql-jdbc/${MSSQL_JDBC_VERSION}/mssql-jdbc-${MSSQL_JDBC_VERSION}.jar ${PYSPARK_JARS}/
RUN ls "$(python -c 'import os, pyspark; print(os.path.dirname(pyspark.__file__))')/jars/" | grep -E 'delta|mssql'

ENV SPARK_CONF_DIR=/opt/spark-conf \
    PYSPARK_PYTHON=/opt/venv/bin/python
