"""Agent settings, read from the environment (falling back to the project's .env)."""

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path = PROJECT_ROOT / ".env") -> None:
    """Fills variables that aren't already set (in containers, settings come from docker-compose)."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    # --- Azure OpenAI (Microsoft Foundry)
    azure_endpoint: str | None
    azure_api_key: str | None
    azure_api_version: str
    chat_deployment: str          # gpt-4o: input guard, SQL writing and answers

    # --- SQL Server (use a dedicated READ-ONLY login; see agent/sql/create_readonly_login.sql)
    mssql_host: str
    mssql_port: str
    mssql_db: str
    mssql_user: str | None
    mssql_password: str | None

    # --- guardrail limits
    max_rows: int = 200                  # rows fetched per query (extra rows are not fetched)
    max_result_chars: int = 6000         # result text handed to the LLM
    query_timeout_s: int = 30            # SQL Server query timeout
    max_tool_calls: int = 6              # queries per question
    max_grounding_retries: int = 1       # re-tries when the answer has unverified figures
    max_question_chars: int = 2000

    allowed_tables: tuple = field(default=("datamart.grid_operations_daily_summary",
                                           "datamart.grid_operations_detail"))


def get_settings() -> Settings:
    load_dotenv()
    return Settings(
        azure_endpoint=_env("AZURE_OPENAI_ENDPOINT"),
        azure_api_key=_env("AZURE_OPENAI_API_KEY"),
        azure_api_version=_env("AZURE_OPENAI_API_VERSION", "2024-10-21"),
        chat_deployment=_env("AZURE_OPENAI_CHAT_DEPLOYMENT", "gpt-4o"),
        mssql_host=_env("AGENT_MSSQL_HOST", _env("MSSQL_HOST", "localhost")),
        mssql_port=_env("AGENT_MSSQL_PORT", _env("MSSQL_PORT", "1433")),
        mssql_db=_env("AGENT_MSSQL_DB", _env("MSSQL_DB", "MonitoringLab")),
        mssql_user=_env("AGENT_MSSQL_USER"),
        mssql_password=_env("AGENT_MSSQL_PASSWORD"),
        max_rows=int(_env("AGENT_MAX_ROWS", "200")),
        query_timeout_s=int(_env("AGENT_QUERY_TIMEOUT_S", "30")),
        max_tool_calls=int(_env("AGENT_MAX_TOOL_CALLS", "6")),
    )
