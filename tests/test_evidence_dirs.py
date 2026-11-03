from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sys

import pytest

from .support import PROJECT_ROOT

sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "lib"))
from evidence_dirs import EvidenceExistsError, resolve_run_dir  # noqa: E402

NOW = datetime(2026, 10, 26, 17, 0, 0, 123456, tzinfo=timezone.utc)


def tree_digest(directory: Path) -> dict[str, str]:
    return {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.rglob("*")) if p.is_file()}


def seed_old_evidence(root: Path) -> Path:
    old = root / "reports" / "validation" / "live-eos-replay-cam01"
    old.mkdir(parents=True)
    (old / "result.json").write_text('{"result": "PASS"}\n', encoding="utf-8")
    (old / "live-eos-replay.db").write_bytes(b"original database")
    (old / "validator.log").write_text("original log\n", encoding="utf-8")
    return old


def test_default_run_creates_new_timestamped_directory_and_leaves_old_evidence(tmp_path: Path) -> None:
    old = seed_old_evidence(tmp_path)
    before = tree_digest(old)
    run = resolve_run_dir(tmp_path, "live-eos-replay-cam01", "AEGIS_X", argv=[], environ={}, now=NOW)
    assert run.source == "default"
    assert run.directory == (tmp_path / "reports" / "validation" / "live-eos-replay-cam01-20261026T170000123456Z").resolve()
    assert run.directory.is_dir() and not any(run.directory.iterdir())
    assert tree_digest(old) == before


def test_existing_directory_is_refused_for_every_selection_route(tmp_path: Path) -> None:
    old = seed_old_evidence(tmp_path)
    before = tree_digest(old)
    with pytest.raises(EvidenceExistsError):
        resolve_run_dir(tmp_path, "p", "AEGIS_X", argv=["--output-dir", str(old)], environ={})
    with pytest.raises(EvidenceExistsError):
        resolve_run_dir(tmp_path, "p", "AEGIS_X", argv=[], environ={"AEGIS_X": str(old)})
    # Even an empty pre-existing directory is not reused.
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(EvidenceExistsError):
        resolve_run_dir(tmp_path, "p", "AEGIS_X", argv=["--output-dir", str(empty)], environ={})
    assert tree_digest(old) == before


def test_same_default_timestamp_is_not_shared(tmp_path: Path) -> None:
    resolve_run_dir(tmp_path, "p", "AEGIS_X", argv=[], environ={}, now=NOW)
    with pytest.raises(EvidenceExistsError):
        resolve_run_dir(tmp_path, "p", "AEGIS_X", argv=[], environ={}, now=NOW)


def test_cli_and_env_routes_are_recorded_in_the_command(tmp_path: Path) -> None:
    cli = resolve_run_dir(tmp_path, "p", "AEGIS_X", argv=["--output-dir", "reports/validation/new-a"], environ={})
    assert cli.source == "cli" and "--output-dir" in cli.command
    assert cli.directory == (tmp_path / "reports" / "validation" / "new-a").resolve()
    env = resolve_run_dir(tmp_path, "p", "AEGIS_X", argv=[], environ={"AEGIS_X": "reports/validation/new-b"})
    assert env.source == "env" and env.command.startswith("AEGIS_X=reports/validation/new-b ")


def test_live_validators_no_longer_delete_or_reuse_fixed_directories() -> None:
    for name in ("validate_live_eos.py", "validate_live_application.py"):
        text = (PROJECT_ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert "resolve_run_dir(" in text
        assert ".unlink(" not in text
        assert 'reports" / "validation" / "live-eos-replay-cam01")' not in text
        assert 'reports" / "validation" / "live-application-cam01"\n' not in text
