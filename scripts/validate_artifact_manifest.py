#!/usr/bin/env python3
"""AC-24: verify version locks and artifact manifests against the files actually present.

Checks pinned dependency files, the frontend lockfile, the pinned DeepStream image digest, model and
engine hashes in configs/models.yaml, the tracker config identity, and calibration manifests.
Inference determinism is a separate concern and is not evaluated here.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
from evidence_dirs import resolve_run_dir  # noqa: E402


def sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def main() -> int:
    run = resolve_run_dir(ROOT, "artifact-manifest-check", "AEGIS_MANIFEST_RUN_DIR")
    results: dict[str, dict] = {}
    for name in ("backend/requirements.txt", "backend/requirements-dev.txt", "perception/deepstream/service_maker/requirements.txt"):
        lines = [l.strip() for l in (ROOT / name).read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
        unpinned = [l for l in lines if not l.startswith("-r") and "==" not in l]
        results[f"pins:{name}"] = {"status": "PASS" if not unpinned else "FAIL", "unpinned": unpinned, "hash_locked": any("--hash" in l for l in lines)}
    # The interpreter running this check is the backend environment under test (.venv on Windows, .venv-wsl / .venv on Linux).
    check = subprocess.run([sys.executable, "-m", "pip", "check"], capture_output=True, text=True)
    results["pip_check_backend_venv"] = {"status": "PASS" if check.returncode == 0 else "FAIL", "output": check.stdout.strip(),
                                         "interpreter": sys.executable}
    lock = ROOT / "frontend" / "package-lock.json"
    results["frontend_lockfile"] = {"status": "PASS" if lock.is_file() else "FAIL", "sha256": sha256(lock),
                                    "lockfileVersion": json.loads(lock.read_text(encoding="utf-8")).get("lockfileVersion") if lock.is_file() else None}
    image = (ROOT / "reports" / "environment" / "deepstream92" / "image-reference.txt").read_text(encoding="utf-8").strip()
    results["deepstream_image_digest_pinned"] = {"status": "PASS" if re.search(r"@sha256:[0-9a-f]{64}$", image) else "FAIL", "image": image}
    # Recorded expected identity vs recomputed current hash, compared per artifact (backend/app/perception/artifacts.py).
    sys.path.insert(0, str(ROOT))
    from backend.app.configuration import load_configuration
    from backend.app.perception.artifacts import HashMemo, check

    config = load_configuration(ROOT / "configs", ROOT)
    memo = HashMemo()
    for model in config.models.models:
        if not model.enabled:
            results[f"model:{model.id}"] = {"status": "NOT_APPLICABLE", "reason": "disabled"}
            continue
        profile = model.runtime_profile
        checks = [check(memo, "checkpoint", model.checkpoint_path, model.sha256),
                  check(memo, "engine", model.engine_path, model.engine_sha256)]
        if profile is not None:
            checks.append(check(memo, "infer_config", profile.infer_config_path, profile.infer_config_sha256))
            if profile.parser.kind == "custom_library":
                checks.append(check(memo, "parser_library", profile.parser.library_path, profile.parser.library_sha256))
        results[f"model:{model.id}"] = {
            "status": "PASS" if all(c.ok for c in checks) and model.revision else "FAIL", "revision": model.revision,
            "acceptance": model.acceptance, "selected": model.id == config.models.selected_model,
            "license_ref": model.license_ref, "identities": [c.as_dict() for c in checks]}
    tracker = config.models.tracker
    tracker_check = check(memo, "tracker_config", tracker.config_path, tracker.sha256)
    results["tracker_config"] = {"status": "PASS" if tracker_check.ok and tracker.revision else "FAIL",
                                 "revision": tracker.revision, "identity": tracker_check.as_dict()}
    pipeline = config.models.pipeline
    pipeline_checks = [check(memo, name, getattr(pipeline, f"{name}_path"), getattr(pipeline, f"{name}_sha256"))
                       for name in ("wrapper", "extractor", "probe_script")] if pipeline else []
    results["pipeline_artifacts"] = {"status": "PASS" if pipeline_checks and all(c.ok for c in pipeline_checks) else "FAIL",
                                     "identities": [c.as_dict() for c in pipeline_checks],
                                     "image": pipeline.image if pipeline else None}
    cameras = yaml.safe_load((ROOT / "configs" / "cameras.yaml").read_text(encoding="utf-8"))["cameras"]
    enabled = [c for c in cameras if c.get("enabled")]
    with_calibration = [c["id"] for c in enabled if c.get("calibration_status") == "VALID" and c.get("calibration_revision")]
    results["calibration_manifests"] = {
        "status": "BLOCKED" if not with_calibration else "PASS",
        "loaded_calibrations": sorted(config.calibrations),
        "reason": "No camera has a calibration yet (all MISSING); a calibration manifest cannot exist without calibration data."
        if not with_calibration else None,
        "enabled_cameras": [c["id"] for c in enabled]}
    statuses = {v["status"] for v in results.values()}
    overall = "FAIL" if "FAIL" in statuses else "BLOCKED" if "BLOCKED" in statuses else "PASS"
    report = {"schema_version": 1, "generated_at_utc": datetime.now(timezone.utc).isoformat(), "command": run.command,
              "run_dir": str(run.directory.relative_to(ROOT)), "result": overall, "items": results,
              "scope": "PRD AC-24 (locks, hashes, model/engine/calibration manifests). Inference run-to-run determinism is "
                       "a separate, reported observation, not an AC-24 prerequisite."}
    (run.directory / "result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"result": overall, "items": {k: v["status"] for k, v in results.items()}, "run_dir": report["run_dir"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
