from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app

from .support import PROJECT_ROOT, UnavailableAdapter, copy_configuration, update_yaml
from .support import requires_local_artifacts


@pytest.fixture(scope="module")
def app_client(tmp_path_factory):
    database = tmp_path_factory.mktemp("api") / "api.db"
    app = create_app(database_path=database, perception_adapter=UnavailableAdapter())
    with TestClient(app) as client:
        yield client


@pytest.fixture
def client(app_client):
    response = app_client.post("/api/v1/demo/reset")
    assert response.status_code == 200
    return app_client


def assert_error(response, status: int, code: str) -> None:
    assert response.status_code == status
    body = response.json()
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str)
    assert isinstance(body["error"]["details"], dict)


def test_health_readiness_environment_and_versions(client) -> None:
    live = client.get("/health/live")
    assert live.status_code == 200 and live.json()["status"] == "ok"
    ready = client.get("/health/ready")
    assert ready.status_code == 503 and ready.json()["status"] == "not_ready"
    checks = {check["name"]: check for check in ready.json()["checks"]}
    assert checks["persistence"]["ready"] and not checks["perception"]["ready"]
    environment = client.get("/api/v1/system/environment")
    assert environment.status_code == 200
    assert environment.json()["host"]["python"]
    assert set(environment.json()["runtime"]["capabilities"]) == {
        "deepstream", "gstreamer", "nvstreammux", "nvinfer", "nvv4l2decoder"
    }
    versions = client.get("/api/v1/versions").json()
    assert len(versions["configuration_revision"]) == 64
    assert versions["model_revision"] == "deepstream-9.2-bundled-resnet18-trafficcamnet-pruned"
    assert versions["model_selection_mode"] == "provisional_smoke"
    assert versions["evaluated_baseline_selected"] is False
    assert versions["tracker_revision"] == "deepstream-9.2-nvdcf-perf-13662e73754e"
    assert set(versions["calibration_revisions"]) == {"cam-01", "cam-02", "cam-03", "cam-04"}


def test_snapshot_cameras_floors_and_real_host_metrics(client) -> None:
    response = client.get("/api/v1/facility/snapshot")
    assert response.status_code == 200
    snapshot = response.json()
    assert snapshot["schema_version"] == "1.0" and snapshot["run_status"] == "IDLE"
    assert snapshot["sequence"] == snapshot["watermark"] and snapshot["sequence"] > 0
    assert len(snapshot["cameras"]) == 4 and len(snapshot["floors"]) == 2
    assert snapshot["kpis"]["enabled_cameras"] == 1
    assert [camera["id"] for camera in snapshot["cameras"] if camera["enabled"]] == ["cam-01"]
    assert snapshot["kpis"]["building_expected"] is None
    assert all(camera["visible_count"] is None for camera in snapshot["cameras"])
    assert all(floor["quality"] == "UNAVAILABLE" for floor in snapshot["floors"])
    assert snapshot["floors"][0]["id"] != snapshot["floors"][1]["id"]
    assert any("UNCALIBRATED" in notice["message"] for notice in snapshot["notices"])
    metrics = snapshot["system_metrics"]
    assert metrics["cpu_utilization"] is not None
    assert metrics["ram_total_mb"] > 0 and metrics["process_rss_mb"] > 0
    assert len(client.get("/api/v1/cameras").json()) == 4
    assert len(client.get("/api/v1/floors").json()) == 2
    assert len(client.get("/api/v1/zones").json()) == 6


@requires_local_artifacts
def test_demo_start_is_idempotent_ack_stop_and_reset(client) -> None:
    started = client.post("/api/v1/demo/start")
    assert started.status_code == 200
    snapshot = started.json()
    assert snapshot["run_status"] == "DEGRADED" and snapshot["run_id"]
    cameras = {camera["id"]: camera for camera in snapshot["cameras"]}
    assert cameras["cam-01"]["health"] == "DEGRADED"
    assert "perception runtime unavailable" in cameras["cam-01"]["unavailable_reason"]
    for camera_id in ("cam-02", "cam-03", "cam-04"):
        assert cameras[camera_id]["health"] == "OFFLINE"
        assert cameras[camera_id]["unavailable_reason"] == "Camera disabled in configuration"
    assert all(camera["visible_count"] is None for camera in snapshot["cameras"])
    assert len(snapshot["alerts"]) == 1
    alert = snapshot["alerts"][0]
    assert alert["category"] == "perception" and alert["severity"] == "WARNING"
    assert alert["status"] == "OPEN"
    run_id = snapshot["run_id"]
    assert client.post("/api/v1/demo/start").json()["run_id"] == run_id

    alert_id = alert["id"]
    acknowledged = client.post(f"/api/v1/alerts/{alert_id}/acknowledge")
    assert acknowledged.status_code == 200 and acknowledged.json()["status"] == "ACKNOWLEDGED"
    assert client.get("/api/v1/alerts", params={"status": "ACKNOWLEDGED"}).json()[0]["id"] == alert_id
    filtered = client.get("/api/v1/alerts", params={"severity": "WARNING", "limit": 2}).json()
    assert [item["id"] for item in filtered] == [alert_id]
    stopped = client.post("/api/v1/demo/stop").json()
    assert stopped["run_status"] == "STOPPED" and stopped["run_id"] == run_id
    reset = client.post("/api/v1/demo/reset").json()
    assert reset["run_status"] == "IDLE" and reset["run_id"] is None
    assert reset["alerts"] == [] and reset["associations"] == []
    assert client.get("/api/v1/alerts").json() == []


def test_snapshot_uses_configured_flow_rows_and_consistent_camera_health(client) -> None:
    run_id = client.post("/api/v1/demo/start").json()["run_id"]
    repository = client.app.state.services.repository
    repository.record_flow(
        run_id, "stair-a", "UP", "2026-01-01T00:00:00.000Z",
        rate_per_minute=4.5, window_seconds=60, observed_count=4, quality="OBSERVED",
    )
    repository.record_flow(
        run_id, "stair-a", "DOWN", "2026-01-01T00:00:00.000Z",
        rate_per_minute=2.5, window_seconds=60, observed_count=2, quality="OBSERVED",
    )
    repository.record_flow(
        run_id, "unconfigured-portal", "UP", "2026-01-01T00:00:00.000Z",
        rate_per_minute=100, window_seconds=60, observed_count=100, quality="OBSERVED",
    )
    snapshot = client.get("/api/v1/facility/snapshot").json()
    assert snapshot["kpis"]["stair_up_per_min"] == 4.5
    assert snapshot["kpis"]["stair_down_per_min"] == 2.5
    camera_health = {camera["id"]: camera["health"] for camera in snapshot["cameras"]}
    assert all(
        camera["health"] == camera_health[camera["id"]]
        for floor in snapshot["floors"] for camera in floor["cameras"]
    )


def test_structured_errors_history_bounds_and_empty_local_history(client) -> None:
    assert_error(client.post("/api/v1/alerts/not-present/acknowledge"), 404, "ALERT_NOT_FOUND")
    assert_error(client.get("/api/v1/cameras/not-present/preview"), 404, "CAMERA_NOT_FOUND")
    assert_error(client.get("/api/v1/analytics/history", params={"metric": "invented"}), 400, "INVALID_METRIC")
    assert_error(client.get("/api/v1/analytics/history", params={"metric": "occupancy", "limit": 0}), 422, "VALIDATION_ERROR")
    assert_error(client.get("/api/v1/analytics/history", params={"metric": "occupancy", "cursor": "-1"}), 422, "INVALID_CURSOR")
    assert_error(client.get("/api/v1/analytics/history", params={
        "metric": "occupancy", "from": "2026-01-01T00:00:00"
    }), 422, "TIMEZONE_REQUIRED")
    history = client.get("/api/v1/analytics/history", params={"metric": "occupancy", "limit": 200})
    assert history.status_code == 200
    assert history.json() == {"metric": "occupancy", "points": [], "gaps": [], "next_cursor": None}
    assert client.get("/api/v1/associations").json() == []
    assert_error(client.get("/api/v1/evidence/not-present"), 404, "EVIDENCE_NOT_FOUND")


def test_evidence_id_cannot_escape_authorized_root(client, app_client) -> None:
    repository = app_client.app.state.services.repository
    evidence_id = repository.register_evidence({
        "path": "../outside.mp4", "media_type": "video/mp4", "kind": "SOURCE",
    })
    assert_error(client.get(f"/api/v1/evidence/{evidence_id}"), 403, "EVIDENCE_PATH_FORBIDDEN")


def test_websocket_sends_initial_snapshot_then_metrics(client) -> None:
    with client.websocket_connect("/api/v1/live") as websocket:
        initial = websocket.receive_json()
        assert initial["type"] == "snapshot" and initial["data"]["schema_version"] == "1.0"
        for _ in range(5):
            message = websocket.receive_json()
            if message["type"] == "metrics":
                assert "sequence" in message and "watermark" in message
                assert message["data"]["ram_total_mb"] > 0
                break
        else:
            pytest.fail("WebSocket did not broadcast metrics")


def test_source_preview_range_and_spa_fallback(tmp_path: Path) -> None:
    directory = copy_configuration(tmp_path)
    clip = tmp_path / "cam01.mp4"
    content = b"0123456789abcdef"
    clip.write_bytes(content)
    update_yaml(directory, "cameras", lambda document: document["cameras"][0].update(source=str(clip)))
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>Local fixture</body></html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log('local')", encoding="utf-8")
    app = create_app(
        config_dir=directory, database_path=tmp_path / "preview.db",
        frontend_dist=dist, perception_adapter=UnavailableAdapter(),
    )
    with TestClient(app) as preview_client:
        response = preview_client.get("/api/v1/cameras/cam-01/preview", headers={"Range": "bytes=2-5"})
        assert response.status_code == 206 and response.content == content[2:6]
        assert response.headers["Content-Range"] == f"bytes 2-5/{len(content)}"
        assert response.headers["Accept-Ranges"] == "bytes"
        assert response.headers["X-Preview-Label"] == "SOURCE PREVIEW - ANALYTICS UNAVAILABLE"
        assert response.headers["X-Preview-Label-Encoded"] == "SOURCE%20PREVIEW%20%C2%B7%20ANALYTICS%20UNAVAILABLE"
        suffix = preview_client.get("/api/v1/cameras/cam-01/preview", headers={"Range": "bytes=-4"})
        assert suffix.status_code == 206 and suffix.content == content[-4:]
        assert_error(preview_client.get(
            "/api/v1/cameras/cam-01/preview", headers={"Range": "bytes=999-"}
        ), 416, "RANGE_NOT_SATISFIABLE")
        assert_error(preview_client.get("/api/v1/cameras/cam-02/preview"), 409, "CAMERA_DISABLED")
        assert preview_client.get("/").text == "<html><body>Local fixture</body></html>"
        assert preview_client.get("/client-side/route").text == "<html><body>Local fixture</body></html>"
        assert preview_client.get("/assets/app.js").status_code == 200
