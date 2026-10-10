"""Dependency-free settings helper shared by the jobs."""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path = PROJECT_ROOT / ".env") -> None:
    """Minimal .env reader: fills variables that aren't already set in the environment.
    In containers there is no project .env; settings come from docker-compose instead."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
