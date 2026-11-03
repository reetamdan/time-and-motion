from __future__ import annotations

from pathlib import Path
from collections.abc import Iterator

import pytest

from backend.app.configuration import AppConfig, load_configuration
from backend.app.db.repository import SQLiteRepository

from .support import PROJECT_ROOT


@pytest.fixture(scope="session")
def config() -> AppConfig:
    return load_configuration(PROJECT_ROOT / "configs", PROJECT_ROOT)


@pytest.fixture
def repository(tmp_path: Path, config: AppConfig) -> Iterator[SQLiteRepository]:
    repository = SQLiteRepository(tmp_path / "repository.db")
    repository.migrate()
    repository.seed_configuration(config)
    try:
        yield repository
    finally:
        repository.close()
