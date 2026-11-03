"""Application-owned services exposed through FastAPI state."""

from __future__ import annotations

from dataclasses import dataclass

from ..configuration import AppConfig
from ..db.repository import SQLiteRepository
from ..perception.base import PerceptionAdapter
from .environment import EnvironmentService
from .evidence import EvidenceService
from .runtime import CameraRuntimeSupervisor
from .sequence import SequenceState
from .snapshots import SnapshotService
from .telemetry import TelemetryService


@dataclass(slots=True)
class AppServices:
    config: AppConfig
    repository: SQLiteRepository
    adapter: PerceptionAdapter
    sequence: SequenceState
    supervisor: CameraRuntimeSupervisor
    telemetry: TelemetryService
    snapshots: SnapshotService
    environment: EnvironmentService
    evidence: EvidenceService
