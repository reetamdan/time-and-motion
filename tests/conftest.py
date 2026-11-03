from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.configuration import AppConfig, load_configuration
from backend.app.db.repository import SQLiteRepository

from .support import PROJECT_ROOT


@pytest.fixture(scope="session")
def config() -> AppConfig:
    return load_configuration(PROJECT_ROOT / "configs", PROJECT_ROOT)


@pytest.fixture
def repository(tmp_path: Path, config: AppConfig) -> SQLiteRepository:
    repository = SQLiteRepository(tmp_path / "repository.db")
    repository.migrate()
    repository.seed_configuration(config)
    return repository
