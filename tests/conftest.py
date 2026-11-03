import os
from pathlib import Path

import pytest
from sqlalchemy import text

from kiro_headless_service.infrastructure.config import Settings
from kiro_headless_service.infrastructure.store import Store


@pytest.fixture
def settings(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_API_TOKEN", "a-test-token-at-least-24-characters")
    monkeypatch.setenv("SECOND_API_TOKEN", "b-test-token-at-least-24-characters")
    config_root = tmp_path / "native-config"
    (config_root / "agents").mkdir(parents=True)
    (config_root / "agents/custom.json").write_text(
        '{"name":"custom","prompt":"custom system instruction","tools":[]}'
    )
    return Settings(
        engine="simulated",
        database_url=os.environ.get(
            "TEST_DATABASE_URL", "postgresql+psycopg://test@localhost/test"
        ),
        temp_dir=str(tmp_path),
        consumers={
            "alice": {"token_ref": "env:TEST_API_TOKEN", "agents": ["custom"], "mcp": ["echo"]},
            "bob": {"token_ref": "env:SECOND_API_TOKEN"},
        },
        kiro_config_dir=str(config_root),
        mcp_servers={
            "echo": {
                "command": "/usr/bin/python3",
                "args": [str(Path("examples/mcp_echo.py").resolve())],
                "tools": ["echo"],
            }
        },
        callback_urls=["https://hooks.example.com/job"],
        lease_seconds=6,
        sse_enabled=True,
    )


@pytest.fixture
def store(settings):
    if not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("TEST_DATABASE_URL not set; real PostgreSQL integration is opt-in")
    database = Store(settings)
    with database.engine.begin() as conn:
        # Tests require a dedicated database; never point TEST_DATABASE_URL at production.
        conn.execute(
            text(
                "TRUNCATE sessions, jobs, messages, events, artifacts, outbox, "
                "heartbeats RESTART IDENTITY CASCADE"
            )
        )
    yield database
    database.engine.dispose()


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    executable = tmp_path / "kiro-cli"
    executable.write_bytes(Path("tests/fake_kiro.py").read_bytes())
    executable.chmod(0o700)
    monkeypatch.setenv("TEST_KIRO_KEY", "test-provider-key-not-real")
    return executable
