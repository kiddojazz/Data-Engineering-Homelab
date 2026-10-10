# syntax=docker/dockerfile:1.7
# Spark master / worker / history server image.
# Adds Python 3.12 (to match Jupyter + Airflow drivers) and Delta + MSSQL JDBC jars.
ARG SPARK_VERSION=4.1.3
FROM spark:${SPARK_VERSION}-scala2.13-java17-ubuntu

ARG DELTA_VERSION=4.4.0
ARG DELTA_SPARK_ARTIFACT=delta-spark_4.1_2.13
ARG MSSQL_JDBC_VERSION=13.6.0.jre11
ARG PYTHON_VERSION=3.12

USER root

RUN set -ex; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl ca-certificates procps tini; \
    rm -rf /var/lib/apt/lists/*

# Python 3.12 via uv, installed in a shared location readable by the spark user.
COPY --from=ghcr.io/astral-sh/uv:0.12.21 /uv /usr/local/bin/uv
ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python \
    UV_NO_CACHE=1
RUN set -ex; \
    uv venv /opt/venv --python ${PYTHON_VERSION}; \
    uv pip install --python /opt/venv/bin/python "numpy>=1.26" "pandas>=2.2" "pyarrow>=15"; \
    chmod -R a+rX /opt/uv-python /opt/venv

# Extra jars: Delta Lake + SQL Server JDBC driver (baked in, no runtime downloads).
ADD --chmod=644 https://repo1.maven.org/maven2/io/delta/${DELTA_SPARK_ARTIFACT}/${DELTA_VERSION}/${DELTA_SPARK_ARTIFACT}-${DELTA_VERSION}.jar /opt/spark/jars/
ADD --chmod=644 https://repo1.maven.org/maven2/io/delta/delta-storage/${DELTA_VERSION}/delta-storage-${DELTA_VERSION}.jar /opt/spark/jars/
ADD --chmod=644 https://repo1.maven.org/maven2/com/microsoft/sqlserver/mssql-jdbc/${MSSQL_JDBC_VERSION}/mssql-jdbc-${MSSQL_JDBC_VERSION}.jar /opt/spark/jars/

RUN mkdir -p /opt/spark-data /opt/spark-events /opt/spark-jobs /tmp/spark-worker \
 && chown -R spark:spark /opt/spark-data /opt/spark-events /opt/spark-jobs /tmp/spark-worker

# Drivers and executors run as different uids but share /opt/spark-data, so every
# Spark process uses umask 0000 (files and dirs writable by all containers).
RUN printf '#!/bin/sh\numask 0000\nexec "$@"\n' > /usr/local/bin/umask-exec \
 && chmod 755 /usr/local/bin/umask-exec

ENV PATH="/opt/spark/bin:/opt/spark/sbin:${PATH}" \
    PYSPARK_PYTHON=/opt/venv/bin/python \
    SPARK_CONF_DIR=/opt/spark-conf \
    SPARK_WORKER_DIR=/tmp/spark-worker \
    SPARK_NO_DAEMONIZE=true

USER spark
WORKDIR /opt/spark/work-dir

# Compose passes the role-specific spark-class command.
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/umask-exec"]
