"""Liveness, readiness, environment, and measured telemetry endpoints."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..errors import APIError
from ..schemas import ReadinessResponse, SystemMetrics
from ..timeutils import iso_now
from .dependencies import services

router = APIRouter(tags=["system"])


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok", "timestamp": iso_now()}


@router.get("/health/ready", response_model=ReadinessResponse)
async def ready(request: Request) -> JSONResponse:
    is_ready, checks = await asyncio.to_thread(services(request).environment.readiness)
    response = ReadinessResponse(status="ready" if is_ready else "not_ready", checks=checks)
    return JSONResponse(status_code=200 if is_ready else 503, content=response.model_dump(mode="json"))


@router.get("/api/v1/system/environment")
async def environment(request: Request) -> dict[str, object]:
    return await asyncio.to_thread(services(request).environment.environment)


@router.get("/api/v1/system/artifacts")
async def artifacts(request: Request) -> dict[str, object]:
    """Expected (recorded) vs current (recomputed) identity of every runtime artifact, plus capability-cache state."""
    adapter = services(request).adapter
    checks = getattr(adapter, "artifact_checks", None)
    capability = getattr(adapter, "capability", None)
    return {
        "artifacts": await asyncio.to_thread(checks) if checks else [],
        "capability": capability.status() if capability is not None else None,
        "basis": "expected_sha256 is recorded in configs/models.yaml; current_sha256 is recomputed from disk; "
                 "status MATCH only when both exist and are equal",
    }


@router.post("/api/v1/system/capability/invalidate")
async def invalidate_capability(request: Request) -> dict[str, object]:
    """Discard the cached container capability so the next start re-probes."""
    adapter = services(request).adapter
    capability = getattr(adapter, "capability", None)
    if capability is None:
        raise APIError(409, "CAPABILITY_CACHE_UNSUPPORTED", "This perception adapter has no capability cache")
    await asyncio.to_thread(capability.invalidate, "operator request")
    return capability.status()


@router.get("/api/v1/system/capabilities")
async def capabilities(request: Request) -> dict[str, object]:
    """Which analytics are available now for each camera/zone, with the exact reason for each that is not."""
    return await asyncio.to_thread(services(request).supervisor.capabilities)


@router.get("/api/v1/system/retention")
async def retention(request: Request) -> dict[str, object]:
    """The retention limits in force and the result of the last pass (null until the first pass finishes)."""
    service = services(request).retention
    return {"settings": service.settings.model_dump(), "last_result": service.last_result}


@router.get("/api/v1/system/metrics", response_model=SystemMetrics)
async def metrics(request: Request) -> dict[str, object]:
    return await asyncio.to_thread(services(request).snapshots.metrics)
