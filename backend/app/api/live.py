"""Reconnectable WebSocket with initial snapshot and coalesced latest-state updates."""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..timeutils import iso_now

router = APIRouter(tags=["live"])


@router.websocket("/api/v1/live")
async def live(websocket: WebSocket) -> None:
    await websocket.accept()
    service = websocket.app.state.services
    interval = service.config.runtime.telemetry.interval_seconds
    last_sequence = -1
    last_metrics = 0.0
    last_heartbeat = time.monotonic()
    try:
        initial = await asyncio.to_thread(service.snapshots.snapshot)
        await websocket.send_json({"type": "snapshot", "data": initial})
        last_sequence = initial["sequence"]
        while True:
            await asyncio.sleep(min(0.5, interval))
            sequence = service.sequence.current
            if sequence != last_sequence:
                snapshot = await asyncio.to_thread(service.snapshots.snapshot)
                await websocket.send_json({"type": "snapshot", "data": snapshot})
                last_sequence = snapshot["sequence"]
            now = time.monotonic()
            if now - last_metrics >= interval:
                await websocket.send_json({
                    "type": "metrics",
                    "data": service.snapshots.metrics(),
                    "sequence": service.sequence.current,
                    "watermark": service.sequence.watermark,
                })
                last_metrics = now
            if now - last_heartbeat >= 15:
                await websocket.send_json({
                    "type": "heartbeat", "timestamp": iso_now(),
                    "sequence": service.sequence.current, "watermark": service.sequence.watermark,
                })
                last_heartbeat = now
    except (WebSocketDisconnect, RuntimeError):
        # A disconnected browser has no effect on authoritative runtime state.
        return
