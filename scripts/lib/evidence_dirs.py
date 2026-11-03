"""Fresh, never-reused evidence directories for live validators.

Every validator run writes to its own new directory. An existing directory is never
reused, cleared, or overwritten, whichever way the path was chosen.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import shlex
import sys


class EvidenceExistsError(RuntimeError):
    pass


@dataclass(frozen=True)
class EvidenceRun:
    directory: Path
    source: str  # "cli", "env" or "default"
    command: str


def utc_stamp(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%S%fZ")


def resolve_run_dir(
    root: Path,
    default_prefix: str,
    env_var: str,
    argv: list[str] | None = None,
    environ: dict[str, str] | None = None,
    now: datetime | None = None,
) -> EvidenceRun:
    """Choose a fresh evidence directory: --output-dir, then env_var, then a timestamped default.

    The directory must not already exist. It is created here, atomically, so two concurrent
    runs cannot share it.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    environ = dict(os.environ if environ is None else environ)
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--output-dir", type=Path, help="New evidence directory (must not exist)")
    args = parser.parse_args(argv)
    if args.output_dir is not None:
        directory, source = args.output_dir, "cli"
    elif environ.get(env_var):
        directory, source = Path(environ[env_var]), "env"
    else:
        directory, source = root / "reports" / "validation" / f"{default_prefix}-{utc_stamp(now)}", "default"
    if not directory.is_absolute():
        directory = root / directory
    directory = directory.resolve()
    try:
        directory.parent.mkdir(parents=True, exist_ok=True)
        directory.mkdir(exist_ok=False)
    except FileExistsError as exc:
        raise EvidenceExistsError(f"Refusing to reuse existing evidence directory {directory}") from exc
    script = Path(sys.argv[0]).name if sys.argv and sys.argv[0] else "validator"
    prefix = f"{env_var}={shlex.quote(environ[env_var])} " if source == "env" else ""
    command = prefix + " ".join(shlex.quote(part) for part in [sys.executable, script, *argv])
    return EvidenceRun(directory, source, command)
