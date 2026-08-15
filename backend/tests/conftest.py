from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
import shutil
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[2]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _build_client(*, raise_server_exceptions: bool = True) -> TestClient:
    from backend.app.main import create_app

    return TestClient(
        create_app(),
        raise_server_exceptions=raise_server_exceptions,
    )


@pytest.fixture(autouse=True)
def app_paths(monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    base_dir = PROJECT_ROOT / "backend" / ".pytest-tmp" / str(uuid4())
    app_data_dir = base_dir / "app-data"
    output_root = base_dir / "outputs"
    sqlite_path = app_data_dir / "knowledge-curator.db"

    monkeypatch.setenv("KNOWLEDGE_CURATOR_APP_DATA_DIR", str(app_data_dir))
    monkeypatch.setenv("KNOWLEDGE_CURATOR_SQLITE_PATH", str(sqlite_path))
    monkeypatch.setenv("KNOWLEDGE_CURATOR_OUTPUT_ROOT", str(output_root))

    paths = {
        "app_data_dir": app_data_dir,
        "sqlite_path": sqlite_path,
        "output_root": output_root,
        "summary_output_dir": output_root / "summaries",
        "report_output_dir": output_root / "reports",
    }
    try:
        yield paths
    finally:
        shutil.rmtree(base_dir, ignore_errors=True)


@pytest.fixture
def configured_app_paths(
    app_paths: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> dict[str, Path]:
    monkeypatch.setenv("KNOWLEDGE_CURATOR_LLM_PROVIDER", "stub-llm")
    monkeypatch.setenv("KNOWLEDGE_CURATOR_LLM_MODEL", "stub-summary-model")
    monkeypatch.setenv("KNOWLEDGE_CURATOR_EMBEDDING_PROVIDER", "stub-embedding")
    monkeypatch.setenv("KNOWLEDGE_CURATOR_EMBEDDING_MODEL", "stub-embedding-model")
    return app_paths


@pytest.fixture
def client(app_paths: dict[str, Path]) -> TestClient:
    with _build_client() as test_client:
        yield test_client


@pytest.fixture
def configured_client(configured_app_paths: dict[str, Path]) -> TestClient:
    with _build_client() as test_client:
        yield test_client


@pytest.fixture
def configured_client_no_raise(
    configured_app_paths: dict[str, Path],
) -> TestClient:
    with _build_client(raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def sqlite_connection(
    configured_client: TestClient,
    configured_app_paths: dict[str, Path],
) -> sqlite3.Connection:
    connection = sqlite3.connect(configured_app_paths["sqlite_path"])
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def test_db(configured_app_paths: dict[str, Path]):
    from backend.app.db import Database

    database = Database(configured_app_paths["sqlite_path"])
    database.initialize()
    return database
