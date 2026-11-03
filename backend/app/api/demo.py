"""Operator-controlled local demo lifecycle."""

import asyncio

from typing import Literal

from fastapi import APIRouter, Query, Request

from ..errors import APIError
from ..perception.base import PerceptionCleanupError

from ..schemas import Snapshot
from .dependencies import services

router = APIRouter(prefix="/api/v1/demo", tags=["demo"])


def _cleanup_conflict(request: Request, exc: PerceptionCleanupError) -> APIError:
    """409 for every lifecycle request refused because owned-resource cleanup is not confirmed."""
    supervisor = services(request).supervisor
    run_id, status, _ = supervisor.current_run()
    return APIError(409, "CLEANUP_UNCONFIRMED", str(exc), {
        "run_id": run_id, "run_status": status, "cameras": sorted(supervisor.cleanup_failures()),
        "retry": "POST /api/v1/demo/stop"})


@router.post("/start", response_model=Snapshot)
async def start(
    request: Request, replay_mode: Literal["paced", "unpaced"] | None = Query(default=None),
) -> dict[str, object]:
    """replay_mode: paced (demo playback on the source timeline, the configured default) or unpaced (evaluation).

    A healthy active run is idempotent (200, no new epoch). STOP_FAILED or any unreleased owned
    resource is refused with 409 CLEANUP_UNCONFIRMED.
    """
    service = services(request)
    try:
        await service.supervisor.start(replay_mode)
    except PerceptionCleanupError as exc:
        raise _cleanup_conflict(request, exc) from exc
    return await asyncio.to_thread(service.snapshots.snapshot)


@router.post("/stop", response_model=Snapshot)
async def stop(request: Request) -> dict[str, object]:
    service = services(request)
    await service.supervisor.stop()
    return await asyncio.to_thread(service.snapshots.snapshot)


@router.post("/reset", response_model=Snapshot)
async def reset(request: Request) -> dict[str, object]:
    service = services(request)
    try:
        await service.supervisor.reset()
    except PerceptionCleanupError as exc:
        raise _cleanup_conflict(request, exc) from exc
    return await asyncio.to_thread(service.snapshots.snapshot)
