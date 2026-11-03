from __future__ import annotations

import sqlite3

from backend.app.timeutils import iso_now

from .support import event_envelope


def test_migration_wal_indexes_and_health(repository) -> None:
    assert repository.health_check() and repository.schema_version == 4
    with sqlite3.connect(repository.database_path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        indexes = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert "idx_events_run_time" in indexes and "idx_occupancy_scope_time" in indexes


def test_event_and_crossing_idempotency(repository) -> None:
    run = repository.create_run("config-revision")
    event = event_envelope(run["id"], "cross-1")
    assert repository.record_event(event)
    assert not repository.record_event(event)
    crossing_event = event_envelope(run["id"], "cross-2") | {"event_id": "crossing-event"}
    crossing = {
        "id": "crossing-1", "boundary_id": "entry", "direction": "IN",
        "track_key": "cam-01:epoch:1", "continuity_id": None,
    }
    assert repository.record_crossing(crossing_event, crossing)
    assert not repository.record_crossing(crossing_event, crossing)
    assert repository.count_crossings(run["id"], "IN") == 1


def test_alert_acknowledgment_is_audited_and_deduplicated(repository) -> None:
    run = repository.create_run("config-revision")
    first = repository.open_alert(
        run["id"], "camera-offline:cam-01", "CRITICAL", "camera_offline", "Camera offline",
        "Source missing", "camera", "cam-01", {"attempt": 1}, "rule-v1",
    )
    duplicate = repository.open_alert(
        run["id"], "camera-offline:cam-01", "CRITICAL", "camera_offline", "Camera offline",
        "Source still missing", "camera", "cam-01", {"attempt": 2}, "rule-v1",
    )
    assert duplicate["id"] == first["id"] and duplicate["status"] == "OPEN"
    acknowledged = repository.acknowledge_alert(first["id"])
    assert acknowledged["status"] == "ACKNOWLEDGED" and acknowledged["acknowledged_at"]
    again = repository.acknowledge_alert(first["id"])
    assert again["acknowledged_at"] == acknowledged["acknowledged_at"]
    changes = repository.alert_state_changes(first["id"])
    assert [change["to_status"] for change in changes] == ["OPEN", "ACKNOWLEDGED"]
    resolved = repository.resolve_alert(first["id"])
    assert resolved["status"] == "RESOLVED" and resolved["acknowledged_at"]


def test_run_scoped_reset_preserves_other_runs_configuration_and_evidence(repository) -> None:
    first = repository.create_run("config-revision")
    second = repository.create_run("config-revision")
    for run in (first, second):
        repository.record_event(event_envelope(run["id"], f"event-{run['id']}"))
        repository.record_occupancy(run["id"], "building", "facility", iso_now(), expected=1, estimated=1, quality="ESTIMATED")
    evidence_id = repository.register_evidence({
        "run_id": first["id"], "path": "clip.mp4", "media_type": "video/mp4", "kind": "SOURCE",
    })
    repository.reset_run_scoped(first["id"])
    assert repository.count_unresolved_associations(first["id"]) is None
    assert repository.history("occupancy", None, None, 10, run_id=first["id"])["points"] == []
    assert len(repository.history("occupancy", None, None, 10, run_id=second["id"])["points"]) == 1
    assert repository.get_evidence(evidence_id)["path"] == "clip.mp4"
    assert repository.get_run(first["id"]) is not None


def test_history_quality_pagination_and_metrics(repository) -> None:
    run = repository.create_run("config-revision")
    repository.record_occupancy(run["id"], "building", "facility", "2026-01-01T00:00:00.000Z",
                                expected=2, estimated=2, visible=None, quality="ESTIMATED")
    repository.record_occupancy(run["id"], "building", "facility", "2026-01-01T00:00:01.000Z",
                                expected=3, estimated=3, visible=2, quality="ESTIMATED")
    page = repository.history("occupancy", None, None, 1)
    assert len(page["points"]) == 1 and page["next_cursor"] == "1"
    second = repository.history("occupancy", None, None, 1, int(page["next_cursor"]))
    assert second["points"][0]["value"] == 2
    repository.record_metrics({"timestamp": iso_now(), "scope": "unavailable", "gpu_utilization": None}, run["id"], 1)
    point = repository.history("gpu_utilization", None, None, 10)["points"][0]
    assert point["value"] is None and point["quality"] == "UNAVAILABLE"


def test_association_candidates_and_unresolved_count(repository) -> None:
    run = repository.create_run("config-revision")
    record = repository.record_association({
        "run_id": run["id"], "portal_id": "stair-a", "source_track_key": "cam-03:e:1",
        "candidate_track_key": None, "status": "UNKNOWN", "decision_reason": "Ambiguous",
        "candidates": [{"track_key": "cam-04:e:2", "score": 0.8, "status": "CANDIDATE", "reasons": []}],
    })
    assert repository.count_unresolved_associations(run["id"]) == 1
    assert repository.list_associations(run_id=run["id"])[0]["id"] == record["id"]
    with sqlite3.connect(repository.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM association_candidates").fetchone()[0] == 1


def test_rtsp_credentials_are_not_persisted(repository, config) -> None:
    import json

    camera = config.cameras[0].model_copy(update={
        "source_type": "rtsp",
        "source": "rtsp://user:secret@camera.local/stream?token=secret",
    })
    seeded_config = config.model_copy(update={"cameras": [camera, *config.cameras[1:]]})
    repository.seed_configuration(seeded_config)
    with sqlite3.connect(repository.database_path) as connection:
        stored = connection.execute("SELECT config_json FROM cameras WHERE id = ?", (camera.id,)).fetchone()[0]
    assert "secret" not in stored
    assert json.loads(stored)["source"] == "RTSP camera.local"
