"""Database retention: age limits, the size budget, and what is never pruned inside a run."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from backend.app.configuration import AppConfig
from backend.app.db.repository import SQLiteRepository
from backend.app.main import create_app
from backend.app.services.retention import BUDGET_ALERT_KEY, RetentionService
from backend.app.timeutils import to_iso

from .support import PROJECT_ROOT, UnavailableAdapter

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def ago(**delta: float) -> str:
    return to_iso(NOW - timedelta(**delta))


def add_run(repository: SQLiteRepository, run_id: str, *, started: str, status: str = "RUNNING",
            stopped: str | None = None) -> None:
    with repository.transaction() as connection:
        connection.execute("INSERT INTO runs(id, status, started_at, stopped_at, profile, configuration_revision) "
                           "VALUES (?, ?, ?, ?, 'demo', 'rev')", (run_id, status, started, stopped))


def add_metric(repository: SQLiteRepository, run_id: str | None, stamp: str, sequence: int = 0) -> None:
    with repository.transaction() as connection:
        connection.execute("INSERT INTO system_metrics(run_id, timestamp, scope, sequence) VALUES (?, ?, 'host', ?)",
                           (run_id, stamp, sequence))


def add_count_sample(repository: SQLiteRepository, run_id: str, bucket: int, stamp: str, padding: str = "") -> None:
    with repository.transaction() as connection:
        connection.execute(
            """INSERT INTO camera_count_samples(run_id, camera_id, camera_epoch, source_segment, bucket_index, source_time_s,
               visible_count, visible_mean, visible_max, frames, replay_mode, received_at_utc, count_basis)
               VALUES (?, 'cam-01', 'e1', 0, ?, ?, 1, 1, 1, 1, ?, ?, 'test')""",
            (run_id, bucket, bucket * 0.5, padding or "paced", stamp))


def add_congestion(repository: SQLiteRepository, run_id: str, zone: str, stamp: str) -> None:
    with repository.transaction() as connection:
        connection.execute("INSERT INTO congestion_snapshots(run_id, zone_id, timestamp, level, units, rule_revision, quality) "
                           "VALUES (?, ?, ?, 'LOW', 'people', 'r', 'OK')", (run_id, zone, stamp))


def add_crossing(repository: SQLiteRepository, run_id: str, event_id: str, stamp: str) -> None:
    with repository.transaction() as connection:
        connection.execute(
            """INSERT INTO events(event_id, schema_version, event_type, run_id, producer_id, producer_sequence,
               idempotency_key, occurred_at, emitted_at, payload_json) VALUES (?, '1', 'line_crossing', ?, 'p', 1, ?, ?, ?, '{}')""",
            (event_id, run_id, event_id, stamp, stamp))
        connection.execute("INSERT INTO line_crossings(id, event_id, run_id, boundary_id, direction, track_key, occurred_at) "
                           "VALUES (?, ?, ?, 'b1', 'IN', 'cam-01:e1:1', ?)", (f"x-{event_id}", event_id, run_id, stamp))


def add_event(repository: SQLiteRepository, run_id: str, event_id: str, stamp: str) -> None:
    with repository.transaction() as connection:
        connection.execute(
            """INSERT INTO events(event_id, schema_version, event_type, run_id, producer_id, producer_sequence,
               idempotency_key, occurred_at, emitted_at, payload_json) VALUES (?, '1', 'health', ?, 'p', 1, ?, ?, ?, '{}')""",
            (event_id, run_id, event_id, stamp, stamp))


def count(repository: SQLiteRepository, table: str, where: str = "1=1", *parameters: object) -> int:
    with repository._connection() as connection:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", parameters).fetchone()[0])


def service(config: AppConfig, repository: SQLiteRepository, **retention: object) -> RetentionService:
    copy = config.model_copy(deep=True)
    for key, value in retention.items():
        setattr(copy.analytics.retention, key, value)
    return RetentionService(copy, repository, batch_rows=3, clock=lambda: NOW)


def test_time_series_expire_but_the_newest_snapshot_per_scope_stays(config: AppConfig, repository: SQLiteRepository) -> None:
    add_run(repository, "live", started=ago(days=10))
    for n, stamp in enumerate([ago(days=5), ago(days=4), ago(days=3), ago(hours=1)]):
        add_metric(repository, "live", stamp, n)
        add_count_sample(repository, "live", n, stamp)
    add_congestion(repository, "live", "z1", ago(days=5))
    add_congestion(repository, "live", "z1", ago(days=4))
    add_congestion(repository, "live", "z2", ago(days=6))  # the only z2 row: still the current value

    result = service(config, repository, metrics_days=2).run_once()

    assert count(repository, "system_metrics") == 1 and count(repository, "camera_count_samples") == 1
    assert {row["zone_id"] for row in repository.latest_analytics("live", "congestion")} == {"z1", "z2"}
    assert count(repository, "congestion_snapshots") == 2
    assert result["deleted"] == {"system_metrics": 3, "camera_count_samples": 3, "congestion_snapshots": 1}
    assert result["over_budget"] is False


def test_crossing_totals_survive_but_other_old_history_expires(config: AppConfig, repository: SQLiteRepository) -> None:
    add_run(repository, "live", started=ago(days=30))
    add_crossing(repository, "live", "cross-old", ago(days=20))
    add_crossing(repository, "live", "cross-new", ago(days=1))
    add_event(repository, "live", "health-old", ago(days=20))
    add_event(repository, "live", "health-new", ago(days=1))
    for stamp in (ago(days=20), ago(days=19), ago(days=1)):
        repository.update_camera_health("live", "cam-01", "e1", "HEALTHY", "MISSING", None)
        with repository.transaction() as connection:  # backdate the row just written
            connection.execute("UPDATE camera_health SET changed_at = ? WHERE id = (SELECT MAX(id) FROM camera_health)", (stamp,))

    service(config, repository, event_history_days=7).run_once()

    assert repository.count_crossings("live", "IN") == 2  # run totals are exact for the whole run
    assert count(repository, "events", "event_id = 'health-old'") == 0
    assert count(repository, "events", "event_id IN ('cross-old', 'cross-new', 'health-new')") == 3
    assert count(repository, "camera_health") == 1


def test_old_stopped_runs_go_but_not_the_newest_or_one_owning_a_container(config: AppConfig, repository: SQLiteRepository) -> None:
    add_run(repository, "old", started=ago(days=30), status="STOPPED", stopped=ago(days=29))
    add_run(repository, "stuck", started=ago(days=28), status="STOPPED", stopped=ago(days=27))
    add_run(repository, "recent", started=ago(days=3), status="STOPPED", stopped=ago(days=2))
    add_crossing(repository, "old", "old-crossing", ago(days=29))
    repository.record_resource("stuck", "cam-01", "e1", "deepstream", "container", "aegis-x", "docker")
    repository.update_resource_state("stuck", "cam-01", "e1", "UNRESOLVED", "unknown")

    result = service(config, repository, event_history_days=7).run_once()

    assert result["deleted"]["runs"] == 1
    assert repository.get_run("old") is None and count(repository, "line_crossings") == 0
    assert repository.get_run("stuck") is not None and repository.get_run("recent") is not None

    add_run(repository, "latest", started=ago(days=20), status="STOPPED", stopped=ago(days=19))
    with repository.transaction() as connection:
        connection.execute("DELETE FROM runs WHERE id = 'recent'")
    service(config, repository, event_history_days=7).run_once()
    assert repository.get_run("latest") is not None  # the newest run always stays


def test_size_budget_shortens_the_series_window_then_alerts(config: AppConfig, repository: SQLiteRepository) -> None:
    add_run(repository, "live", started=ago(days=3))
    padding = "x" * 2000
    for n in range(600):  # ~1.3 MiB of count samples spread over the last 2 hours
        add_count_sample(repository, "live", n, ago(minutes=120 - n * 0.2), padding)

    result = service(config, repository, max_database_mib=1).run_once()

    assert result["series_age_hours"] == 0.5  # halved from 2 days down to the 30-minute floor
    assert count(repository, "camera_count_samples", "received_at_utc < ?", ago(minutes=30)) == 0
    assert result["over_budget"] == (result["used_mib"] > 1)

    for n in range(600, 1400):  # recent data alone over budget: alert, keep the data
        add_count_sample(repository, "live", n, ago(minutes=1), padding)
    result = service(config, repository, max_database_mib=1).run_once()
    assert result["over_budget"] is True
    alerts = [a for a in repository.list_alerts(run_id="live") if a["stable_key"] == BUDGET_ALERT_KEY]
    assert len(alerts) == 1 and alerts[0]["category"] == "storage"


def test_failed_pass_is_logged_not_raised(config: AppConfig, repository: SQLiteRepository, caplog) -> None:
    retention = service(config, repository)
    repository.close()
    repository.database_path = repository.database_path.parent / "missing-dir" / "x.db"
    assert asyncio.run(retention.run_async()) is None
    assert "Database retention pass failed" in caplog.text


def test_server_runs_retention_at_startup_and_reports_it(tmp_path) -> None:
    app = create_app(database_path=tmp_path / "app.db", perception_adapter=UnavailableAdapter(),
                     evidence_root=tmp_path / "evidence")
    with TestClient(app) as client:
        body = None
        for _ in range(100):
            body = client.get("/api/v1/system/retention").json()
            if body["last_result"] is not None:
                break
            asyncio.run(asyncio.sleep(0.05))
        assert body is not None and body["last_result"] is not None
        assert body["settings"]["cleanup_interval_minutes"] == 10
        assert body["last_result"]["over_budget"] is False and "evidence" in body["last_result"]
