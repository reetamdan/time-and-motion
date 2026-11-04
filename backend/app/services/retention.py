"""Periodic database retention (configs/analytics.yaml ``retention``).

Age limits, then the size budget:

* ``metrics_days``: system metrics, per-camera visible-count history, and analytics snapshots (the newest
  snapshot per scope always stays, so the dashboard's current values never disappear).
* ``event_history_days``: camera health changes (newest per camera stays), per-camera crossing records,
  events that no facility crossing references, associations; and whole runs stopped longer than this
  that own no unreleased container. The newest run is always kept.
* ``hypotheses_minutes``: expired global track hypotheses.
* ``max_database_mib``: when the data in use still exceeds the budget, the time-series cutoff is halved
  (down to ``MIN_SERIES_AGE``) until it fits; if it still does not, an alert is raised.

Run-cumulative records (facility crossings behind the entries/exits totals, alerts, corrections,
provenance) are never pruned inside a run, so totals stay exact. Evidence files follow their own limits
(``EvidenceService.enforce_retention``), which this job also runs. Deletes run in short batches so the
analytics writer is never blocked for long.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
import time
from typing import Any, Callable

from ..configuration import AppConfig
from ..db.repository import SQLiteRepository
from ..timeutils import to_iso, utc_now

LOGGER = logging.getLogger("cctv.retention")
SERIES = ("system_metrics", "camera_count_samples", "occupancy_snapshots", "queue_snapshots", "flow_snapshots",
          "congestion_snapshots")
HISTORY = ("camera_health", "camera_line_crossings", "events", "associations")
MIN_SERIES_AGE = timedelta(minutes=30)
BUDGET_ALERT_KEY = "storage:database-budget"


class RetentionService:
    def __init__(self, config: AppConfig, repository: SQLiteRepository, evidence: Any | None = None,
                 batch_rows: int = 2000, clock: Callable[[], datetime] = utc_now) -> None:
        self.config = config
        self.repository = repository
        self.evidence = evidence
        self.batch_rows = batch_rows
        self.clock = clock
        self.last_result: dict[str, Any] | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def settings(self):
        return self.config.analytics.retention

    def _prune(self, kind: str, cutoff: datetime, deleted: dict[str, int]) -> None:
        stamp = to_iso(cutoff)
        while True:
            count = self.repository.prune(kind, stamp, self.batch_rows)
            if count:
                deleted[kind] = deleted.get(kind, 0) + count
            if count < self.batch_rows:
                return

    def run_once(self, now: datetime | None = None) -> dict[str, Any]:
        """One full pass. Synchronous: call it from a worker thread inside the server."""
        started = time.monotonic()
        now = now or self.clock()
        settings = self.settings
        deleted: dict[str, int] = {}
        series_age = timedelta(days=settings.metrics_days)
        for kind in SERIES:
            self._prune(kind, now - series_age, deleted)
        history_cutoff = now - timedelta(days=settings.event_history_days)
        for kind in HISTORY:
            self._prune(kind, history_cutoff, deleted)
        self._prune("global_track_hypotheses", now - timedelta(minutes=settings.hypotheses_minutes), deleted)
        self._prune("runs", history_cutoff, deleted)

        budget = settings.max_database_mib * 1024 * 1024
        usage = self.repository.database_usage()
        while usage["used_bytes"] > budget and series_age > MIN_SERIES_AGE:
            series_age = max(series_age / 2, MIN_SERIES_AGE)
            for kind in SERIES:
                self._prune(kind, now - series_age, deleted)
            usage = self.repository.database_usage()
        over_budget = usage["used_bytes"] > budget
        result = {
            "ran_at": to_iso(now), "deleted": deleted, "used_mib": round(usage["used_bytes"] / 1048576, 1),
            "file_mib": round(usage["file_bytes"] / 1048576, 1), "budget_mib": settings.max_database_mib,
            "series_age_hours": round(series_age.total_seconds() / 3600, 2), "over_budget": over_budget,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
        }
        if self.evidence is not None:
            result["evidence"] = self.evidence.enforce_retention(now=now)
        if over_budget:
            self._alert(result)
        if deleted or over_budget:
            LOGGER.log(logging.WARNING if over_budget else logging.INFO, "Retention: %s", result)
        self.last_result = result
        return result

    def _alert(self, result: dict[str, Any]) -> None:
        run = self.repository.latest_run(active_only=True)
        message = (f"Database data uses {result['used_mib']} MiB, over its {result['budget_mib']} MiB budget, after "
                   f"pruning time series older than {result['series_age_hours']} h. Raise retention.max_database_mib "
                   "or shorten event_history_days.")
        LOGGER.warning(message)
        if run is not None:
            self.repository.open_alert(run["id"], BUDGET_ALERT_KEY, "WARNING", "storage", "Database over its size budget",
                                       message, "system", None, {k: result[k] for k in ("used_mib", "budget_mib")},
                                       "retention-v1")

    async def run_async(self) -> dict[str, Any] | None:
        try:
            return await asyncio.to_thread(self.run_once)
        except Exception:  # retention must never take the server down; the next pass retries
            LOGGER.exception("Database retention pass failed")
            return None

    def start(self, interval_seconds: float) -> None:
        if self._task is not None:
            return

        async def loop() -> None:
            while True:
                await self.run_async()
                await asyncio.sleep(interval_seconds)

        self._task = asyncio.create_task(loop(), name="cctv-retention")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
