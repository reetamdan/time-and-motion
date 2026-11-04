"""Importable FastAPI application: ``backend.app.main:app``."""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import uvicorn
from fastapi import FastAPI

from .api import demo, facility, live, static, system
from .configuration import AppConfig, load_configuration
from .db.repository import SQLiteRepository
from .errors import install_error_handlers
from .perception.base import PerceptionAdapter
from .perception.deepstream import DeepStreamAdapter
from .services.context import AppServices
from .services.environment import EnvironmentService
from .services.evidence import EvidenceService, MediaTools
from .services.instance_lock import InstanceLock
from .services.retention import RetentionService
from .services.runtime import CameraRuntimeSupervisor
from .services.sequence import SequenceState
from .services.snapshots import SnapshotService
from .services.telemetry import TelemetryCollector, TelemetryService

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def create_app(
    *,
    config_dir: Path | str | None = None,
    database_path: Path | str | None = None,
    frontend_dist: Path | str | None = None,
    perception_adapter: PerceptionAdapter | None = None,
    telemetry_collector: TelemetryCollector | None = None,
    media_tools: MediaTools | None = None,
    evidence_root: Path | str | None = None,
) -> FastAPI:
    root = PROJECT_ROOT
    selected_config = Path(config_dir) if config_dir else Path(os.getenv("CCTV_CONFIG_DIR", root / "configs"))
    config = load_configuration(selected_config, project_root=root)
    if database_path or os.getenv("CCTV_DATABASE_PATH"):
        config.runtime.database_path = Path(database_path or os.environ["CCTV_DATABASE_PATH"]).resolve()
    if frontend_dist:
        config.runtime.frontend_dist = Path(frontend_dist).resolve()
    if evidence_root or os.getenv("CCTV_EVIDENCE_ROOT"):
        config.runtime.evidence_root = Path(evidence_root or os.environ["CCTV_EVIDENCE_ROOT"]).resolve()
    if os.getenv("CCTV_CAPABILITY_CACHE"):
        config.runtime.capability.cache_path = Path(os.environ["CCTV_CAPABILITY_CACHE"]).resolve()
    logging.getLogger().setLevel(config.runtime.log_level)

    repository = SQLiteRepository(config.runtime.database_path)
    instance_lock = InstanceLock(config.runtime.database_path)
    adapter = perception_adapter or DeepStreamAdapter(config)
    sequence = SequenceState()
    media_tools = media_tools or MediaTools(config.runtime.evidence.tool_timeout_seconds)
    supervisor = CameraRuntimeSupervisor(config, repository, adapter, sequence, frame_counter=media_tools.frame_count)
    telemetry = TelemetryService(telemetry_collector or TelemetryCollector(config.runtime.telemetry))
    snapshots = SnapshotService(config, repository, supervisor, telemetry, sequence)
    environment = EnvironmentService(config, repository, adapter, telemetry)
    evidence = EvidenceService(config, repository, media_tools)
    retention = RetentionService(config, repository, evidence)
    app_services = AppServices(
        config=config,
        repository=repository,
        adapter=adapter,
        sequence=sequence,
        supervisor=supervisor,
        telemetry=telemetry,
        snapshots=snapshots,
        environment=environment,
        evidence=evidence,
        retention=retention,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Refuse to start (before migrations and recovery) while another backend owns this database.
        instance_lock.acquire()
        try:
            async with _serve():
                yield
        finally:
            instance_lock.release()

    @asynccontextmanager
    async def _serve() -> AsyncIterator[None]:
        try:
            repository.migrate()
            repository.seed_configuration(config)
        except sqlite3.Error:
            logging.getLogger("cctv.persistence").exception("Persistence initialization failed; readiness will be unavailable")
        try:
            # Reconcile resources owned by a previous process before any new run can start.
            await supervisor.recover()
        except sqlite3.Error:
            logging.getLogger("cctv.persistence").exception("Ownership recovery could not read persistence")
        # Database and evidence retention in the background: a first pass now, then on a timer (failures are
        # logged, never fatal), so a large first clean-up never delays readiness.
        retention.start(config.analytics.retention.cleanup_interval_minutes * 60)
        await telemetry.collect_now()
        telemetry.start(config.runtime.telemetry.interval_seconds, snapshots.metrics_collected)
        try:
            yield
        finally:
            await telemetry.stop()
            await retention.stop()
            try:
                await supervisor.shutdown()
            finally:
                repository.close()

    application = FastAPI(
        title="Local Crowd Intelligence API",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    application.state.services = app_services
    install_error_handlers(application)
    application.include_router(system.router)
    application.include_router(facility.router)
    application.include_router(demo.router)
    application.include_router(live.router)

    dist = config.runtime.frontend_dist
    if config.runtime.serve_frontend and (dist / "index.html").is_file():
        application.mount("/", static.SPAStaticFiles(directory=dist, html=True), name="frontend")
    else:
        @application.get("/", include_in_schema=False)
        async def root() -> dict[str, object]:
            return {
                "service": "local-crowd-intelligence",
                "api_prefix": "/api/v1",
                "frontend_available": False,
                "detail": "frontend/dist/index.html is not installed",
            }
    return application


app = create_app()


if __name__ == "__main__":
    uvicorn.run(app, host=app.state.services.config.runtime.host, port=app.state.services.config.runtime.port)
