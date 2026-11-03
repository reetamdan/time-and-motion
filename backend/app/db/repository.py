"""PostgreSQL-ready repository interface and transactional SQLite implementation."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Protocol

from ..configuration import AppConfig
from ..timeutils import iso_now
from .schema import MIGRATIONS, SCHEMA_VERSION


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _row(row: sqlite3.Row | None, *json_columns: str) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    for column in json_columns:
        if column in result:
            result[column] = json.loads(result[column])
    return result


class Repository(Protocol):
    """Storage boundary used by services; SQL dialect details stay in implementations."""

    @property
    def schema_version(self) -> int: ...
    def migrate(self) -> None: ...
    def health_check(self) -> bool: ...
    def seed_configuration(self, config: AppConfig) -> None: ...
    def create_run(self, configuration_revision: str, profile: str = "demo") -> dict[str, Any]: ...
    def get_run(self, run_id: str) -> dict[str, Any] | None: ...
    def latest_run(self, active_only: bool = False) -> dict[str, Any] | None: ...
    def update_run_status(self, run_id: str, status: str, reason: str | None = None) -> None: ...
    def end_camera_sessions(self, run_id: str) -> None: ...
    def start_camera_session(self, run_id: str, camera_id: str, epoch: str, source_state: str, adapter_name: str) -> str: ...
    def update_camera_health(self, run_id: str, camera_id: str, epoch: str | None, health: str,
                             calibration_status: str, reason: str | None,
                             details: dict[str, Any] | None = None) -> None: ...
    def open_alert(self, run_id: str, stable_key: str, severity: str, category: str, title: str,
                   message: str, scope_type: str, scope_id: str | None, measurements: dict[str, Any],
                   rule_revision: str, evidence_id: str | None = None) -> dict[str, Any]: ...
    def get_alert(self, alert_id: str) -> dict[str, Any] | None: ...
    def acknowledge_alert(self, alert_id: str, actor: str = "operator") -> dict[str, Any] | None: ...
    def list_alerts(self, *, status: str | None = None, severity: str | None = None,
                    run_id: str | None = None, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]: ...
    def record_count_samples(self, run_id: str, samples: list[dict[str, Any]]) -> int: ...
    def record_resource(self, run_id: str, camera_id: str, camera_epoch: str, adapter_name: str,
                        kind: str | None, name: str | None, runtime: str | None) -> None: ...
    def update_resource_state(self, run_id: str, camera_id: str, camera_epoch: str, state: str,
                              error: str | None = None, attempted: bool = False) -> None: ...
    def unreleased_resources(self, run_id: str | None = None) -> list[dict[str, Any]]: ...
    def record_camera_crossing(self, run_id: str, crossing: dict[str, Any], event: dict[str, Any]) -> bool: ...
    def latest_camera_epoch(self, run_id: str, camera_id: str) -> str | None: ...
    def camera_count_history(self, run_id: str, camera_id: str, camera_epoch: str, limit: int = 2000) -> list[dict[str, Any]]: ...
    def camera_crossings(self, run_id: str, camera_id: str, camera_epoch: str, limit: int = 2000) -> list[dict[str, Any]]: ...
    def latest_analytics(self, run_id: str, kind: str) -> list[dict[str, Any]]: ...
    def count_crossings(self, run_id: str, direction: str | None = None) -> int: ...
    def count_unresolved_associations(self, run_id: str) -> int | None: ...
    def list_associations(self, *, run_id: str | None = None, status: str | None = None,
                          limit: int = 100, offset: int = 0) -> list[dict[str, Any]]: ...
    def record_metrics(self, metrics: dict[str, Any], run_id: str | None, sequence: int) -> None: ...
    def history(self, metric: str, start: str | None, end: str | None, limit: int, offset: int = 0,
                run_id: str | None = None) -> dict[str, Any]: ...
    def get_evidence(self, evidence_id: str) -> dict[str, Any] | None: ...
    def record_run_provenance(self, run_id: str, provenance: dict[str, Any]) -> None: ...
    def get_run_provenance(self, run_id: str) -> dict[str, Any] | None: ...
    def list_evidence(self, **filters: Any) -> list[dict[str, Any]]: ...
    def reset_run_scoped(self, run_id: str | None = None) -> None: ...


class SQLiteRepository:
    def __init__(self, database_path: Path | str, busy_timeout_ms: int = 5000) -> None:
        self.database_path = Path(database_path)
        self.busy_timeout_ms = busy_timeout_ms
        self._lock = threading.RLock()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = sqlite3.connect(
                self.database_path,
                timeout=self.busy_timeout_ms / 1000,
                isolation_level=None,
                check_same_thread=False,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(f"PRAGMA busy_timeout = {int(self.busy_timeout_ms)}")
            try:
                yield connection
            finally:
                connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()

    def migrate(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
        with self.transaction() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            applied = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
            for version in sorted(MIGRATIONS):
                if version in applied:
                    continue
                for statement in MIGRATIONS[version]:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)", (version, iso_now())
                )

    @property
    def schema_version(self) -> int:
        return SCHEMA_VERSION

    def health_check(self) -> bool:
        try:
            with self._connection() as connection:
                return connection.execute("SELECT 1").fetchone()[0] == 1
        except sqlite3.Error:
            return False

    def seed_configuration(self, config: AppConfig) -> None:
        now = iso_now()
        revision = config.configuration_revision
        with self.transaction() as connection:
            for camera in config.cameras:
                camera_data = camera.model_dump(mode="json")
                if camera.source_type == "rtsp":
                    camera_data["source"] = camera.source_label
                connection.execute(
                    """INSERT INTO cameras(id, floor_id, enabled, configuration_revision, config_json, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET floor_id=excluded.floor_id, enabled=excluded.enabled,
                       configuration_revision=excluded.configuration_revision, config_json=excluded.config_json,
                       updated_at=excluded.updated_at""",
                    (camera.id, camera.floor_id, int(camera.enabled), revision, _json(camera_data), now),
                )
            for table, records in (("floors", config.floors), ("zones", config.zones), ("portals", config.portals)):
                for record in records:
                    connection.execute(
                        f"INSERT OR REPLACE INTO {table}(id, configuration_revision, config_json) VALUES (?, ?, ?)",
                        (record.id, revision, record.model_dump_json()),
                    )
            for camera in config.cameras:
                connection.execute(
                    """INSERT INTO camera_calibrations(id, camera_id, revision, status, calibration_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET revision=excluded.revision, status=excluded.status,
                       calibration_json=excluded.calibration_json""",
                    (
                        camera.calibration_ref or f"{camera.id}-unavailable",
                        camera.id,
                        camera.calibration_revision,
                        camera.calibration_status,
                        None,
                        now,
                    ),
                )
            for name, checksum in config.file_revisions.items():
                connection.execute(
                    """INSERT OR IGNORE INTO provenance(run_id, kind, name, revision, checksum, metadata_json, created_at)
                       VALUES (NULL, 'configuration', ?, ?, ?, '{}', ?)""",
                    (name, revision, checksum, now),
                )

    def create_run(self, configuration_revision: str, profile: str = "demo") -> dict[str, Any]:
        run_id = str(uuid.uuid4())
        now = iso_now()
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO runs(id, status, started_at, profile, configuration_revision) VALUES (?, 'STARTING', ?, ?, ?)",
                (run_id, now, profile, configuration_revision),
            )
        return self.get_run(run_id)  # type: ignore[return-value]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            return _row(connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone())

    def latest_run(self, active_only: bool = False) -> dict[str, Any] | None:
        query = "SELECT * FROM runs"
        parameters: tuple[Any, ...] = ()
        if active_only:
            query += " WHERE status IN ('STARTING', 'RUNNING', 'DEGRADED', 'STOP_FAILED')"
        query += " ORDER BY started_at DESC LIMIT 1"
        with self._connection() as connection:
            return _row(connection.execute(query, parameters).fetchone())

    def update_run_status(self, run_id: str, status: str, reason: str | None = None) -> None:
        stopped = status == "STOPPED"
        with self.transaction() as connection:
            connection.execute(
                """UPDATE runs SET status = ?, stopped_at = CASE WHEN ? THEN COALESCE(stopped_at, ?) ELSE stopped_at END,
                   stop_reason = CASE WHEN ? THEN ? ELSE stop_reason END WHERE id = ?""",
                (status, int(stopped), iso_now(), int(stopped), reason, run_id),
            )

    def start_camera_session(
        self, run_id: str, camera_id: str, epoch: str, source_state: str, adapter_name: str
    ) -> str:
        session_id = str(uuid.uuid4())
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO camera_sessions(id, run_id, camera_id, camera_epoch, source_state, adapter_name, started_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (session_id, run_id, camera_id, epoch, source_state, adapter_name, iso_now()),
            )
        return session_id

    def end_camera_sessions(self, run_id: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                "UPDATE camera_sessions SET ended_at = COALESCE(ended_at, ?) WHERE run_id = ?",
                (iso_now(), run_id),
            )

    def update_camera_health(
        self,
        run_id: str,
        camera_id: str,
        epoch: str | None,
        health: str,
        calibration_status: str,
        reason: str | None,
        details: dict[str, Any] | None = None,
    ) -> None:
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO camera_health(run_id, camera_id, camera_epoch, health, calibration_status, reason, changed_at, details_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (run_id, camera_id, epoch, health, calibration_status, reason, iso_now(), _json(details or {})),
            )

    def _insert_event(self, connection: sqlite3.Connection, event: dict[str, Any]) -> bool:
        cursor = connection.execute(
            """INSERT OR IGNORE INTO events(event_id, schema_version, event_type, run_id, producer_id,
               producer_sequence, idempotency_key, occurred_at, emitted_at, camera_id, camera_epoch,
               payload_json, supersedes_event_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event["event_id"], event.get("schema_version", "1.0"), event["event_type"], event["run_id"],
                event["producer_id"], event["producer_sequence"], event["idempotency_key"], event["occurred_at"],
                event["emitted_at"], event.get("camera_id"), event.get("camera_epoch"), _json(event.get("payload", {})),
                event.get("supersedes_event_id"),
            ),
        )
        return cursor.rowcount == 1

    def record_event(self, event: dict[str, Any]) -> bool:
        with self.transaction() as connection:
            return self._insert_event(connection, event)

    def record_crossing(self, event: dict[str, Any], crossing: dict[str, Any]) -> bool:
        with self.transaction() as connection:
            if not self._insert_event(connection, event):
                return False
            connection.execute(
                """INSERT INTO line_crossings(id, event_id, run_id, boundary_id, direction, track_key,
                   continuity_id, occurred_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    crossing["id"], event["event_id"], event["run_id"], crossing["boundary_id"],
                    crossing["direction"], crossing["track_key"], crossing.get("continuity_id"), event["occurred_at"],
                ),
            )
            return True

    def record_occupancy(
        self, run_id: str, scope_type: str, scope_id: str, timestamp: str, *,
        expected: int | None = None, visible: int | None = None, estimated: int | None = None,
        unresolved: int | None = None, discrepancy: int | None = None,
        allocation_discrepancy: int | None = None, quality: str = "UNAVAILABLE",
        coverage: dict[str, Any] | None = None, balances: dict[str, Any] | None = None,
    ) -> None:
        with self.transaction() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO occupancy_snapshots(run_id, scope_type, scope_id, timestamp, expected,
                   visible, estimated, unresolved, discrepancy, allocation_discrepancy, quality, coverage_json, balances_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (run_id, scope_type, scope_id, timestamp, expected, visible, estimated, unresolved, discrepancy,
                 allocation_discrepancy, quality, _json(coverage or {}), _json(balances or {})),
            )

    def record_queue(
        self, run_id: str, queue_id: str, timestamp: str, *, queue_size: int | None,
        joins: int = 0, leaves: int = 0, throughput: float | None = None,
        max_wait_seconds: float | None = None, quality: str = "UNAVAILABLE",
        details: dict[str, Any] | None = None,
    ) -> None:
        with self.transaction() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO queue_snapshots(run_id, queue_id, timestamp, queue_size, joins, leaves,
                   throughput, max_wait_seconds, quality, details_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (run_id, queue_id, timestamp, queue_size, joins, leaves, throughput, max_wait_seconds, quality,
                 _json(details or {})),
            )

    def record_flow(
        self, run_id: str, portal_id: str, direction: str, timestamp: str, *, rate_per_minute: float | None,
        window_seconds: float, observed_count: int = 0, inferred_count: int = 0, quality: str = "UNAVAILABLE",
    ) -> None:
        with self.transaction() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO flow_snapshots(run_id, portal_id, direction, timestamp, rate_per_minute,
                   window_seconds, observed_count, inferred_count, quality) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (run_id, portal_id, direction, timestamp, rate_per_minute, window_seconds, observed_count,
                 inferred_count, quality),
            )

    def record_congestion(
        self, run_id: str, zone_id: str, timestamp: str, *, level: str, value: float | None,
        units: str = "count", measurements: dict[str, Any] | None = None,
        thresholds: dict[str, Any] | None = None, rule_revision: str, quality: str = "OBSERVED",
    ) -> None:
        with self.transaction() as connection:
            connection.execute(
                """INSERT OR REPLACE INTO congestion_snapshots(run_id, zone_id, timestamp, level, value, units,
                   measurements_json, thresholds_json, rule_revision, quality) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (run_id, zone_id, timestamp, level, value, units, _json(measurements or {}),
                 _json(thresholds or {}), rule_revision, quality),
            )

    def record_resource(self, run_id: str, camera_id: str, camera_epoch: str, adapter_name: str,
                        kind: str | None, name: str | None, runtime: str | None) -> None:
        """Record ownership *before* launch, so a crash or failed start can never orphan the resource."""
        now = iso_now()
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO owned_resources(run_id, camera_id, camera_epoch, adapter_name, resource_kind, resource_name,
                   runtime, state, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'LAUNCHING', ?, ?)""",
                (run_id, camera_id, camera_epoch, adapter_name, kind, name, runtime, now, now),
            )

    def update_resource_state(self, run_id: str, camera_id: str, camera_epoch: str, state: str,
                              error: str | None = None, attempted: bool = False) -> None:
        with self.transaction() as connection:
            connection.execute(
                """UPDATE owned_resources SET state = ?, last_error = ?, attempts = attempts + ?, updated_at = ?
                   WHERE run_id = ? AND camera_id = ? AND camera_epoch = ?""",
                (state, error, 1 if attempted else 0, iso_now(), run_id, camera_id, camera_epoch),
            )

    def unreleased_resources(self, run_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM owned_resources WHERE state != 'RELEASED'"
        parameters: tuple[Any, ...] = ()
        if run_id is not None:
            query += " AND run_id = ?"
            parameters = (run_id,)
        with self._connection() as connection:
            return [dict(row) for row in connection.execute(query + " ORDER BY created_at", parameters)]

    def record_count_samples(self, run_id: str, samples: list[dict[str, Any]]) -> int:
        """Idempotent: a replayed sample (same run/camera/epoch/segment/bucket) is ignored."""
        inserted = 0
        with self.transaction() as connection:
            for sample in samples:
                cursor = connection.execute(
                    """INSERT OR IGNORE INTO camera_count_samples(run_id, camera_id, camera_epoch, source_segment,
                       bucket_index, source_time_s, frame_number, visible_count, visible_mean, visible_max, frames,
                       replay_mode, received_at_utc, count_basis) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (run_id, sample["camera_id"], sample["camera_epoch"], sample["source_segment"], sample["bucket_index"],
                     sample["source_time_s"], sample.get("frame_number"), sample["visible_count"], sample["visible_mean"],
                     sample["visible_max"], sample["frames"], sample.get("replay_mode"), sample["received_at_utc"],
                     sample["count_basis"]),
                )
                inserted += cursor.rowcount
        return inserted

    def record_camera_crossing(self, run_id: str, crossing: dict[str, Any], event: dict[str, Any]) -> bool:
        """Idempotent camera-local crossing plus its audit event; never touches building line_crossings."""
        with self.transaction() as connection:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO camera_line_crossings(event_id, idempotency_key, run_id, camera_id, camera_epoch,
                   source_segment, line_id, coordinate_space, direction, track_key, source_time_s, received_at_utc)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (crossing["event_id"], crossing["idempotency_key"], run_id, crossing["camera_id"], crossing["camera_epoch"],
                 crossing["source_segment"], crossing["line_id"], crossing["coordinate_space"], crossing["direction"],
                 crossing["track_key"], crossing["source_time_s"], crossing["received_at_utc"]),
            )
            if cursor.rowcount != 1:
                return False
            self._insert_event(connection, event)
            return True

    def latest_camera_epoch(self, run_id: str, camera_id: str) -> str | None:
        with self._connection() as connection:
            row = connection.execute(
                """SELECT camera_epoch FROM camera_sessions WHERE run_id = ? AND camera_id = ?
                   ORDER BY started_at DESC LIMIT 1""", (run_id, camera_id),
            ).fetchone()
        return row[0] if row else None

    def camera_count_history(self, run_id: str, camera_id: str, camera_epoch: str, limit: int = 2000) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT camera_epoch, source_segment, bucket_index, source_time_s, frame_number, visible_count,
                   visible_mean, visible_max, frames, replay_mode, received_at_utc, count_basis
                   FROM camera_count_samples WHERE run_id = ? AND camera_id = ? AND camera_epoch = ?
                   ORDER BY source_segment, source_time_s LIMIT ?""",
                (run_id, camera_id, camera_epoch, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def camera_count_history_window(
        self, run_id: str, camera_id: str, camera_epoch: str, *, limit: int, window: str = "recent",
        from_source_s: float | None = None, to_source_s: float | None = None, after: tuple[int, int] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """recent: the newest `limit` samples (chronological). range: ascending from the `after`
        (segment, bucket) cursor and/or source-time bounds, with a next cursor when more exist."""
        columns = """camera_epoch, source_segment, bucket_index, source_time_s, frame_number, visible_count,
                   visible_mean, visible_max, frames, replay_mode, received_at_utc, count_basis"""
        scope = "run_id = ? AND camera_id = ? AND camera_epoch = ?"
        parameters: list[Any] = [run_id, camera_id, camera_epoch]
        with self._connection() as connection:
            stats = dict(connection.execute(
                f"""SELECT COUNT(*) AS total_samples, COUNT(DISTINCT source_segment) AS segments,
                    MIN(source_time_s) AS first_source_s, MAX(source_time_s) AS last_source_s
                    FROM camera_count_samples WHERE {scope}""", parameters).fetchone())
            if window == "recent":
                rows = connection.execute(
                    f"""SELECT {columns} FROM camera_count_samples WHERE {scope}
                        ORDER BY source_segment DESC, bucket_index DESC LIMIT ?""", [*parameters, limit]).fetchall()
                samples = [dict(row) for row in reversed(rows)]
                stats["next_cursor"] = None
            else:
                clauses = [scope]
                if after is not None:
                    clauses.append("(source_segment > ? OR (source_segment = ? AND bucket_index > ?))")
                    parameters += [after[0], after[0], after[1]]
                if from_source_s is not None:
                    clauses.append("source_time_s >= ?")
                    parameters.append(from_source_s)
                if to_source_s is not None:
                    clauses.append("source_time_s <= ?")
                    parameters.append(to_source_s)
                rows = connection.execute(
                    f"""SELECT {columns} FROM camera_count_samples WHERE {' AND '.join(clauses)}
                        ORDER BY source_segment, bucket_index LIMIT ?""", [*parameters, limit + 1]).fetchall()
                samples = [dict(row) for row in rows[:limit]]
                last = samples[-1] if samples and len(rows) > limit else None
                stats["next_cursor"] = f"{last['source_segment']}:{last['bucket_index']}" if last else None
        return samples, stats

    def camera_crossings(self, run_id: str, camera_id: str, camera_epoch: str, limit: int = 2000) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT event_id, source_segment, line_id, coordinate_space, direction, track_key, source_time_s,
                   received_at_utc FROM camera_line_crossings WHERE run_id = ? AND camera_id = ? AND camera_epoch = ?
                   ORDER BY source_segment, source_time_s LIMIT ?""",
                (run_id, camera_id, camera_epoch, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_analytics(self, run_id: str, kind: str) -> list[dict[str, Any]]:
        tables = {
            "occupancy": ("occupancy_snapshots", "scope_type || ':' || scope_id", "coverage_json,balances_json"),
            "queue": ("queue_snapshots", "queue_id", "details_json"),
            "flow": ("flow_snapshots", "portal_id || ':' || direction", ""),
            "congestion": ("congestion_snapshots", "zone_id", "measurements_json,thresholds_json"),
        }
        if kind not in tables:
            raise ValueError("Unknown analytics snapshot kind")
        table, scope, json_columns = tables[kind]
        # The correlated max timestamp selects only the latest row per scope without a dialect-specific window.
        query = f"""SELECT current.* FROM {table} current
                    JOIN (SELECT {scope} AS scope_key, MAX(timestamp) AS latest_timestamp
                          FROM {table} WHERE run_id = ? GROUP BY {scope}) latest
                    ON {scope} = latest.scope_key AND current.timestamp = latest.latest_timestamp
                    WHERE current.run_id = ? ORDER BY current.timestamp"""
        with self._connection() as connection:
            rows = connection.execute(query, (run_id, run_id)).fetchall()
        return [_row(row, *filter(None, json_columns.split(","))) for row in rows]  # type: ignore[misc]

    def count_crossings(self, run_id: str, direction: str | None = None) -> int:
        query = "SELECT COUNT(*) FROM line_crossings WHERE run_id = ?"
        parameters: list[Any] = [run_id]
        if direction:
            query += " AND direction = ?"
            parameters.append(direction)
        with self._connection() as connection:
            return int(connection.execute(query, parameters).fetchone()[0])

    def open_alert(
        self, run_id: str, stable_key: str, severity: str, category: str, title: str, message: str,
        scope_type: str, scope_id: str | None, measurements: dict[str, Any], rule_revision: str,
        evidence_id: str | None = None,
    ) -> dict[str, Any]:
        now = iso_now()
        alert_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"cctv-alert:{run_id}:{stable_key}"))
        with self.transaction() as connection:
            existing = connection.execute("SELECT * FROM alerts WHERE run_id = ? AND stable_key = ?", (run_id, stable_key)).fetchone()
            if existing is None:
                connection.execute(
                    """INSERT INTO alerts(id, stable_key, run_id, severity, category, title, message, scope_type,
                       scope_id, status, triggered_at, last_triggered_at, measurements_json, rule_revision, evidence_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, ?)""",
                    (alert_id, stable_key, run_id, severity, category, title, message, scope_type, scope_id,
                     now, now, _json(measurements), rule_revision, evidence_id),
                )
                connection.execute(
                    """INSERT INTO alert_state_changes(alert_id, run_id, from_status, to_status, changed_at, reason, actor)
                       VALUES (?, ?, NULL, 'OPEN', ?, ?, 'analytics')""",
                    (alert_id, run_id, now, message),
                )
            else:
                previous_status = existing["status"]
                status = "OPEN" if previous_status == "RESOLVED" else previous_status
                acknowledged_at = None if previous_status == "RESOLVED" else existing["acknowledged_at"]
                connection.execute(
                    """UPDATE alerts SET severity=?, category=?, title=?, message=?, scope_type=?, scope_id=?, status=?,
                       last_triggered_at=?, acknowledged_at=?, resolved_at=NULL, measurements_json=?, rule_revision=?,
                       evidence_id=? WHERE id=?""",
                    (severity, category, title, message, scope_type, scope_id, status, now, acknowledged_at,
                     _json(measurements), rule_revision, evidence_id, existing["id"]),
                )
                if previous_status == "RESOLVED":
                    connection.execute(
                        """INSERT INTO alert_state_changes(alert_id, run_id, from_status, to_status, changed_at, reason, actor)
                           VALUES (?, ?, 'RESOLVED', 'OPEN', ?, ?, 'analytics')""",
                        (existing["id"], run_id, now, message),
                    )
                alert_id = existing["id"]
        return self.get_alert(alert_id)  # type: ignore[return-value]

    def _alert_row(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        result = _row(row, "measurements_json")
        if result is None:
            return None
        result["measurements"] = result.pop("measurements_json")
        return result

    def get_alert(self, alert_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            return self._alert_row(connection.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone())

    def list_alerts(
        self, *, status: str | None = None, severity: str | None = None, run_id: str | None = None,
        limit: int = 100, offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[Any] = []
        for column, value in (("status", status), ("severity", severity), ("run_id", run_id)):
            if value is not None:
                clauses.append(f"{column} = ?")
                parameters.append(value)
        query = "SELECT * FROM alerts" + (" WHERE " + " AND ".join(clauses) if clauses else "")
        query += " ORDER BY triggered_at DESC, id LIMIT ? OFFSET ?"
        parameters.extend((limit, offset))
        with self._connection() as connection:
            return [self._alert_row(row) for row in connection.execute(query, parameters)]  # type: ignore[misc]

    def acknowledge_alert(self, alert_id: str, actor: str = "operator") -> dict[str, Any] | None:
        now = iso_now()
        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
            if row is None:
                return None
            if row["status"] == "OPEN":
                connection.execute(
                    "UPDATE alerts SET status='ACKNOWLEDGED', acknowledged_at=? WHERE id=?", (now, alert_id)
                )
                connection.execute(
                    """INSERT INTO alert_state_changes(alert_id, run_id, from_status, to_status, changed_at, reason, actor)
                       VALUES (?, ?, 'OPEN', 'ACKNOWLEDGED', ?, 'Operator acknowledgment', ?)""",
                    (alert_id, row["run_id"], now, actor),
                )
        return self.get_alert(alert_id)

    def resolve_alert_by_key(self, run_id: str, stable_key: str, reason: str = "Condition cleared") -> dict[str, Any] | None:
        return self.resolve_alert(str(uuid.uuid5(uuid.NAMESPACE_URL, f"cctv-alert:{run_id}:{stable_key}")), reason)

    def resolve_alert(self, alert_id: str, reason: str = "Condition cleared") -> dict[str, Any] | None:
        now = iso_now()
        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
            if row is None:
                return None
            if row["status"] != "RESOLVED":
                connection.execute("UPDATE alerts SET status='RESOLVED', resolved_at=? WHERE id=?", (now, alert_id))
                connection.execute(
                    """INSERT INTO alert_state_changes(alert_id, run_id, from_status, to_status, changed_at, reason, actor)
                       VALUES (?, ?, ?, 'RESOLVED', ?, ?, 'analytics')""",
                    (alert_id, row["run_id"], row["status"], now, reason),
                )
        return self.get_alert(alert_id)

    def alert_state_changes(self, alert_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM alert_state_changes WHERE alert_id = ? ORDER BY changed_at, id", (alert_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def record_association(self, record: dict[str, Any]) -> dict[str, Any]:
        now = iso_now()
        association_id = record.get("id") or str(uuid.uuid4())
        created_at = record.get("created_at", now)
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO associations(id, run_id, portal_id, source_track_key, candidate_track_key,
                   temporary_global_track_id, status, score, margin, decision_reason, candidates_json,
                   created_at, updated_at, supersedes_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    association_id, record["run_id"], record.get("portal_id"), record["source_track_key"],
                    record.get("candidate_track_key"), record.get("temporary_global_track_id"), record["status"],
                    record.get("score"), record.get("margin"), record["decision_reason"],
                    _json(record.get("candidates", [])), created_at, now, record.get("supersedes_id"),
                ),
            )
            for candidate in record.get("candidates", []):
                connection.execute(
                    """INSERT INTO association_candidates(association_id, candidate_track_key, score, status, reasons_json)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        association_id, candidate["track_key"], candidate.get("score"), candidate["status"],
                        _json(candidate.get("reasons", [])),
                    ),
                )
        return self.get_association(association_id)  # type: ignore[return-value]

    def _association_row(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        result = _row(row, "candidates_json")
        if result is not None:
            result["candidates"] = result.pop("candidates_json")
        return result

    def get_association(self, association_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            return self._association_row(
                connection.execute("SELECT * FROM associations WHERE id = ?", (association_id,)).fetchone()
            )

    def list_associations(
        self, *, run_id: str | None = None, status: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if run_id:
            clauses.append("run_id = ?")
            parameters.append(run_id)
        if status:
            clauses.append("status = ?")
            parameters.append(status)
        query = "SELECT * FROM associations" + (" WHERE " + " AND ".join(clauses) if clauses else "")
        query += " ORDER BY created_at DESC, id LIMIT ? OFFSET ?"
        parameters.extend((limit, offset))
        with self._connection() as connection:
            return [self._association_row(row) for row in connection.execute(query, parameters)]  # type: ignore[misc]

    def count_unresolved_associations(self, run_id: str) -> int | None:
        with self._connection() as connection:
            total = connection.execute("SELECT COUNT(*) FROM associations WHERE run_id = ?", (run_id,)).fetchone()[0]
            if not total:
                return None
            return int(connection.execute(
                "SELECT COUNT(*) FROM associations WHERE run_id = ? AND status IN ('UNKNOWN', 'EXPIRED')", (run_id,)
            ).fetchone()[0])

    def record_metrics(self, metrics: dict[str, Any], run_id: str | None, sequence: int) -> None:
        columns = (
            "timestamp", "scope", "gpu_name", "gpu_utilization", "vram_used_mib", "vram_total_mib",
            "gpu_temperature_c", "nvdec_utilization", "cpu_utilization", "ram_used_mb", "ram_total_mb",
            "process_rss_mb", "unavailable_reason",
        )
        with self.transaction() as connection:
            connection.execute(
                f"INSERT INTO system_metrics(run_id, {', '.join(columns)}, sequence) VALUES (?, {', '.join('?' for _ in columns)}, ?)",
                (run_id, *(metrics.get(column) for column in columns), sequence),
            )

    def history(
        self, metric: str, start: str | None, end: str | None, limit: int, offset: int = 0,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        occupancy_columns = {
            "occupancy": "estimated", "building_expected": "expected", "building_visible": "visible",
            "building_estimated": "estimated", "discrepancy": "discrepancy",
        }
        base: str
        parameters: list[Any] = []
        if metric in occupancy_columns:
            base = f"""SELECT timestamp, {occupancy_columns[metric]} AS value, quality, run_id
                       FROM occupancy_snapshots WHERE scope_type='building'"""
        elif metric == "queue_total":
            base = "SELECT timestamp, queue_size AS value, quality, run_id FROM queue_snapshots WHERE 1=1"
        elif metric == "max_wait_seconds":
            base = "SELECT timestamp, max_wait_seconds AS value, quality, run_id FROM queue_snapshots WHERE 1=1"
        elif metric in ("entries", "exits"):
            base = f"""SELECT occurred_at AS timestamp, 1.0 AS value, 'OBSERVED' AS quality, run_id
                       FROM line_crossings WHERE direction='{'IN' if metric == 'entries' else 'OUT'}'"""
        elif metric in ("stair_up_per_min", "stair_down_per_min"):
            base = f"""SELECT timestamp, rate_per_minute AS value, quality, run_id FROM flow_snapshots
                       WHERE direction='{'UP' if metric == 'stair_up_per_min' else 'DOWN'}'"""
        elif metric in ("cpu_utilization", "gpu_utilization", "vram_used_mib"):
            base = f"""SELECT timestamp, {metric} AS value,
                       CASE WHEN {metric} IS NULL THEN 'UNAVAILABLE' ELSE 'OBSERVED' END AS quality,
                       run_id FROM system_metrics WHERE 1=1"""
        else:
            raise ValueError(f"Unsupported history metric: {metric}")
        if run_id:
            base += " AND run_id = ?"
            parameters.append(run_id)
        if start:
            base += " AND timestamp >= ?"
            parameters.append(start)
        if end:
            base += " AND timestamp <= ?"
            parameters.append(end)
        base += " ORDER BY timestamp DESC, run_id DESC LIMIT ? OFFSET ?"
        parameters.extend((limit + 1, offset))
        with self._connection() as connection:
            rows = [dict(row) for row in connection.execute(base, parameters)]
        has_more = len(rows) > limit
        points = list(reversed(rows[:limit]))
        return {
            "metric": metric,
            "points": points,
            "gaps": [],
            "next_cursor": str(offset + limit) if has_more else None,
        }

    def register_evidence(self, record: dict[str, Any]) -> str:
        evidence_id = record.get("id") or str(uuid.uuid4())
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO evidence(id, run_id, camera_id, path, media_type, kind, timestamp_basis, start_at,
                   end_at, checksum, codec, metadata_json, created_at, camera_epoch, source_start_s, source_end_s,
                   source_marked_s, size_bytes, label, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    evidence_id, record.get("run_id"), record.get("camera_id"), record["path"], record["media_type"],
                    record["kind"], record.get("timestamp_basis"), record.get("start_at"), record.get("end_at"),
                    record.get("checksum"), record.get("codec"), _json(record.get("metadata", {})), iso_now(),
                    record.get("camera_epoch"), record.get("source_start_s"), record.get("source_end_s"),
                    record.get("source_marked_s"), record.get("size_bytes"), record.get("label"),
                    record.get("status", "AVAILABLE"),
                ),
            )
        return evidence_id

    def get_evidence(self, evidence_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            return _row(connection.execute("SELECT * FROM evidence WHERE id = ?", (evidence_id,)).fetchone(), "metadata_json")

    def list_evidence(self, *, run_id: str | None = None, camera_id: str | None = None, group: str | None = None,
                      limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        clauses, parameters = [], []
        for column, value in (("run_id", run_id), ("camera_id", camera_id)):
            if value is not None:
                clauses.append(f"{column} = ?")
                parameters.append(value)
        if group is not None:
            clauses.append("json_extract(metadata_json, '$.group_id') = ?")
            parameters.append(group)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT * FROM evidence {where} ORDER BY created_at DESC, id LIMIT ? OFFSET ?",
                (*parameters, limit, offset)).fetchall()
        return [_row(row, "metadata_json") for row in rows]  # type: ignore[misc]

    def evidence_usage(self) -> dict[str, int]:
        with self._connection() as connection:
            row = connection.execute("SELECT COUNT(*), COALESCE(SUM(size_bytes), 0) FROM evidence").fetchone()
        return {"records": int(row[0]), "bytes": int(row[1])}

    def evidence_for_retention(self) -> list[dict[str, Any]]:
        """Oldest first; the caller decides how many to remove (age and count/size limits)."""
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT id, path, size_bytes, created_at FROM evidence ORDER BY created_at, id").fetchall()
        return [dict(row) for row in rows]

    def delete_evidence(self, evidence_id: str) -> None:
        with self.transaction() as connection:
            connection.execute("UPDATE alerts SET evidence_id = NULL WHERE evidence_id = ?", (evidence_id,))
            connection.execute("DELETE FROM evidence WHERE id = ?", (evidence_id,))

    def update_evidence_status(self, evidence_id: str, status: str) -> None:
        with self.transaction() as connection:
            connection.execute("UPDATE evidence SET status = ? WHERE id = ?", (status, evidence_id))

    def record_run_provenance(self, run_id: str, provenance: dict[str, Any]) -> None:
        with self.transaction() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO run_provenance(run_id, recorded_at, provenance_json) VALUES (?, ?, ?)",
                (run_id, iso_now(), _json(provenance)))

    def get_run_provenance(self, run_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            return _row(connection.execute("SELECT * FROM run_provenance WHERE run_id = ?", (run_id,)).fetchone(),
                        "provenance_json")

    def reset_run_scoped(self, run_id: str | None = None) -> None:
        # Evidence and provenance are deliberately retained; reset never deletes source/model/calibration files.
        tables = (
            "camera_count_samples", "camera_line_crossings",
            "alert_state_changes", "association_candidates", "track_observations", "line_crossings",
            "occupancy_snapshots", "queue_snapshots", "flow_snapshots", "congestion_snapshots",
            "counter_corrections", "system_metrics", "camera_health", "camera_sessions", "local_tracks",
            "global_track_hypotheses", "associations", "alerts", "events",
        )
        with self.transaction() as connection:
            for table in tables:
                if run_id is None:
                    connection.execute(f"DELETE FROM {table}")
                elif table == "association_candidates":
                    connection.execute(
                        "DELETE FROM association_candidates WHERE association_id IN (SELECT id FROM associations WHERE run_id = ?)",
                        (run_id,),
                    )
                else:
                    connection.execute(f"DELETE FROM {table} WHERE run_id = ?", (run_id,))
