"""Expected-vs-current identity checks for every artifact the selected runtime profile uses.

Three things are kept distinct for each artifact:
* ``expected_sha256`` - the identity recorded in configs/models.yaml (or None if nothing is recorded);
* ``current_sha256``  - the hash recomputed from the file on disk now (None if the file is missing);
* ``status``          - MATCH only when both exist and are equal. A computed hash with no recorded
  expectation is NO_EXPECTED_IDENTITY, which is a blocker, never an implicit pass.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from pathlib import Path
import threading
from typing import Any

from ..configuration import AppConfig

MATCH = "MATCH"
MISMATCH = "MISMATCH"
MISSING_FILE = "MISSING_FILE"
NO_EXPECTED_IDENTITY = "NO_EXPECTED_IDENTITY"


@dataclass(frozen=True, slots=True)
class ArtifactCheck:
    name: str
    path: str
    expected_sha256: str | None
    current_sha256: str | None
    status: str

    @property
    def ok(self) -> bool:
        return self.status == MATCH

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class HashMemo:
    """sha256 per file, recomputed whenever (size, mtime_ns) changes; thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._memo: dict[str, tuple[int, int, str]] = {}

    def sha256(self, path: Path) -> str | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        if not path.is_file():
            return None
        key = str(path)
        with self._lock:
            cached = self._memo.get(key)
        if cached and cached[0] == stat.st_size and cached[1] == stat.st_mtime_ns:
            return cached[2]
        digest = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError:
            return None
        value = digest.hexdigest()
        with self._lock:
            self._memo[key] = (stat.st_size, stat.st_mtime_ns, value)
        return value


def check(memo: HashMemo, name: str, path: Path | None, expected: str | None) -> ArtifactCheck:
    current = memo.sha256(path) if path is not None else None
    if current is None:
        status = MISSING_FILE
    elif not expected:
        status = NO_EXPECTED_IDENTITY
    else:
        status = MATCH if current == expected.lower() else MISMATCH
    return ArtifactCheck(name, str(path) if path else "", expected, current, status)


def runtime_artifact_checks(config: AppConfig, memo: HashMemo) -> list[ArtifactCheck]:
    """Every file identity the selected Service Maker profile depends on."""
    checks: list[ArtifactCheck] = []
    models = config.models
    selected = models.selected
    if selected is not None:
        checks.append(check(memo, "detector.checkpoint", selected.checkpoint_path, selected.sha256))
        checks.append(check(memo, "detector.engine", selected.engine_path, selected.engine_sha256))
        profile = selected.runtime_profile
        if profile is not None:
            checks.append(check(memo, "detector.infer_config", profile.infer_config_path, profile.infer_config_sha256))
            if profile.parser.kind == "custom_library":
                checks.append(check(memo, "detector.parser_library", profile.parser.library_path,
                                    profile.parser.library_sha256))
    tracker = models.tracker
    checks.append(check(memo, "tracker.config", tracker.config_path, tracker.sha256))
    pipeline = models.pipeline
    if pipeline is not None:
        checks.append(check(memo, "pipeline.wrapper", pipeline.wrapper_path, pipeline.wrapper_sha256))
        checks.append(check(memo, "pipeline.extractor", pipeline.extractor_path, pipeline.extractor_sha256))
        checks.append(check(memo, "pipeline.capability_probe", pipeline.probe_script_path, pipeline.probe_script_sha256))
        try:
            recorded = pipeline.image_reference_path.read_text(encoding="utf-8").strip()
        except OSError:
            recorded = None
        status = MISSING_FILE if recorded is None else (MATCH if recorded == pipeline.image else MISMATCH)
        # The image itself is identified by digest; the comparison is of the digest reference strings.
        checks.append(ArtifactCheck("pipeline.image_reference", str(pipeline.image_reference_path),
                                    pipeline.image, recorded, status))
    return checks


def profile_blockers(config: AppConfig) -> list[str]:
    """Configuration-level reasons the selected model cannot be run (no file access)."""
    selected = config.models.selected
    if selected is None:
        return ["No runtime detector profile is selected"]
    blockers = []
    if selected.runtime_profile is None:
        blockers.append(f"Selected detector {selected.id} has no runtime_profile")
    if config.models.pipeline is None:
        blockers.append("configs/models.yaml has no pipeline artifact identities")
    if not config.models.tracker.enabled:
        blockers.append("No pinned NVIDIA tracker configuration is enabled")
    return blockers
