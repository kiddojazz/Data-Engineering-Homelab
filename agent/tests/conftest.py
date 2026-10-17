import os

import pytest


@pytest.fixture(autouse=True)
def no_langfuse_tracing(monkeypatch):
    """Tests use scripted fake models; keep them out of the real Langfuse project.
    Empty values win over .env because load_dotenv only fills variables that aren't set."""
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "")
    yield
