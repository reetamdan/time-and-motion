#!/usr/bin/env python3
"""Validate a configuration directory and print which analytics it can support.

Loads every YAML file with the same strict loader the backend uses (cross-references, geometry
bounds, calibration files/revisions/validation, model/tracker identities, selection rules), then
prints the capability matrix assuming every enabled camera were running. Frame-size checks happen at
runtime, so calibrations are reported as "valid pending frame-size check".

Run: .venv/Scripts/python.exe scripts/config/validate_config.py [configs]
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.app.analytics.facility import FacilityAnalytics, capability_matrix  # noqa: E402
from backend.app.configuration import ConfigurationError, load_configuration  # noqa: E402
from backend.app.perception.artifacts import HashMemo, runtime_artifact_checks  # noqa: E402


def main() -> int:
    directory = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "configs"
    try:
        config = load_configuration(directory, ROOT)
    except ConfigurationError as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, indent=1))
        return 1
    facility = FacilityAnalytics(config)
    running = {camera.id for camera in config.cameras if camera.enabled}
    for camera_id in running:
        facility.reset_camera(camera_id)
    checks = runtime_artifact_checks(config, HashMemo())
    print(json.dumps({
        "valid": True, "configuration_revision": config.configuration_revision,
        "selected_model": config.models.selected_model, "selection_mode": config.models.selection_mode,
        "artifact_identities": {c.name: c.status for c in checks},
        "calibrations": {camera_id: calibration.validity()[1] for camera_id, calibration in config.calibrations.items()},
        "capabilities_if_enabled_cameras_run": capability_matrix(config, facility.geometry, running),
    }, indent=1))
    return 0 if all(c.ok for c in checks) else 2


if __name__ == "__main__":
    raise SystemExit(main())
