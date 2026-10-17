# syntax=docker/dockerfile:1.7
# GridIQ agent API: FastAPI + LangGraph agent (text-to-SQL over the SQL Server datamart).
# Build context is ./agent (see docker-compose.yml, service agent-api).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt requirements-api.txt /tmp/
RUN pip install -r /tmp/requirements.txt -r /tmp/requirements-api.txt

COPY . /app/agent

RUN useradd --create-home --uid 10001 gridiq
USER gridiq

EXPOSE 8000
CMD ["uvicorn", "agent.api:app", "--host", "0.0.0.0", "--port", "8000"]
