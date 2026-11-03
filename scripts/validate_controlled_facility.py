#!/usr/bin/env python3
"""CONTROLLED evidence for the facility criteria that physical footage cannot yet exercise (AC-09..AC-19).

Runs the named controlled tests (synthetic calibrated tracks, synthetic scaling calibration, supplied
observations; several run through the real runtime, persistence and HTTP API) and records, per
acceptance criterion, which behaviour was exercised and the result. This is evidence scope
"unit/controlled" only: it proves the software path, NOT real calibrated multi-camera accuracy,
real crossings, or physical scenes. Physical acceptance stays blocked on footage and calibration.

Run: .venv/Scripts/python.exe scripts/validate_controlled_facility.py
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
from evidence_dirs import EvidenceExistsError, resolve_run_dir  # noqa: E402

CRITERIA: dict[str, dict] = {
    "AC-09": {"behaviour": "directional entry/exit with dead band, debounce, confirmation, idempotency; floor-map crossings feed entries/exits and the ledger",
              "tests": ["tests/test_crossing.py", "tests/test_facility_analytics.py::test_floor_boundary_crossing_feeds_entries_and_the_ledger"]},
    "AC-10": {"behaviour": "zone/floor occupancy from calibrated positions; nested/partially overlapping zones counted once per floor; stale/ended/failed cameras withdraw figures",
              "tests": ["tests/test_facility_analytics.py::test_zone_occupancy_capabilities_and_resolution_invalidation",
                        "tests/test_facility_freshness.py"]},
    "AC-11": {"behaviour": "ledger: entries/exits, reconciliation, explicit discrepancies, audited reinitialisation; building figure withdrawn when boundary cameras are not current",
              "tests": ["tests/test_occupancy.py", "tests/test_facility_freshness.py::test_all_cameras_stale_makes_every_facility_figure_unavailable"]},
    "AC-12": {"behaviour": "two local tracks of one person in an overlap count once; negative merge checks (same camera, time/distance gates, uncalibrated overlap)",
              "tests": ["tests/test_fusion.py", "tests/test_facility_analytics.py::test_overlap_fusion_counts_one_person_seen_by_two_cameras",
                        "tests/test_facility_freshness.py::test_overlapping_camera_views_and_zones_deduplicate_once"]},
    "AC-13": {"behaviour": "floors are independent maps and scopes; building sums de-duplicated floors",
              "tests": ["tests/test_facility_freshness.py::test_building_sums_deduplicated_floors",
                        "tests/test_facility_freshness.py::test_floor_capability_reports_dedup_policy"]},
    "AC-14": {"behaviour": "stair transition reaches association + flow through the runtime; flow is inferred, not observed; balanced internal floor transfer in the ledger",
              "tests": ["tests/test_facility_freshness.py::test_runtime_stair_transition_records_unknown_association_and_inferred_flow",
                        "tests/test_occupancy.py::test_internal_floor_transfer_conserves_building_totals", "tests/test_freshness_flow.py"]},
    "AC-15": {"behaviour": "ambiguous / unsupported association stays UNKNOWN; topology/time/staleness hard gates; revocation audited",
              "tests": ["tests/test_association.py"]},
    "AC-16": {"behaviour": "queue membership with dwell and movement, observed-service wait only, occlusion/expiry never become zero",
              "tests": ["tests/test_queue_congestion.py"]},
    "AC-17": {"behaviour": "congestion persistence, hysteresis, recovery, density needs a valid area; alerts opened/resolved through the runtime",
              "tests": ["tests/test_queue_congestion.py::test_congestion_persistence_hysteresis_and_recovery",
                        "tests/test_facility_analytics.py::test_congestion_needs_persistence_and_recovers_with_hysteresis"]},
    "AC-19": {"behaviour": "calibration revision/validation enforced at load; resolution/crop change invalidates geometry at runtime; time-quality gates",
              "tests": ["tests/test_facility_analytics.py::test_valid_calibration_requires_a_matching_validating_file",
                        "tests/test_facility_analytics.py::test_image_line_on_a_different_frame_size_is_disabled",
                        "tests/test_facility_analytics.py::test_calibration_projection_rejects_points_off_the_floor",
                        "tests/test_facility_freshness.py::test_source_discontinuity_resets_time_dependent_state"]},
}


def main() -> int:
    try:
        evidence = resolve_run_dir(ROOT, "controlled-facility", "AEGIS_CONTROLLED_RUN_DIR")
    except EvidenceExistsError as exc:
        raise SystemExit(str(exc)) from exc
    out = evidence.directory
    results = {}
    (out / "pytest").mkdir()
    for criterion, spec in CRITERIA.items():
        junit = out / f"{criterion}.xml"
        completed = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--basetemp", str(out / "pytest" / criterion),
                                    f"--junitxml={junit}", *spec["tests"]], cwd=ROOT, capture_output=True, text=True, check=False)
        (out / f"{criterion}.log").write_text(completed.stdout + completed.stderr, encoding="utf-8")
        cases = []
        if junit.is_file():
            for case in ET.parse(junit).getroot().iter("testcase"):
                failed = case.find("failure") is not None or case.find("error") is not None
                skipped = case.find("skipped") is not None
                cases.append({"test": f"{case.get('classname')}::{case.get('name')}",
                              "result": "FAIL" if failed else "SKIPPED" if skipped else "PASS"})
        results[criterion] = {"behaviour": spec["behaviour"], "selection": spec["tests"], "cases": cases,
                              "result": "PASS" if cases and completed.returncode == 0 and all(c["result"] == "PASS" for c in cases) else "FAIL"}
        print(criterion, results[criterion]["result"], len(cases), "cases", flush=True)
    report = {"schema_version": 1, "generated_at_utc": datetime.now(timezone.utc).isoformat(), "command": evidence.command,
              "run_dir": str(out.relative_to(ROOT)), "evidence_scope": "unit/controlled",
              "result": "PASS" if all(r["result"] == "PASS" for r in results.values()) else "FAIL", "criteria": results,
              "limits": "Synthetic tracks and a synthetic scaling calibration. Proves the implemented software path and its "
                        "unavailable/UNKNOWN semantics. It is not physical acceptance: no real fixed-camera crossings, surveyed "
                        "geometry, calibrated overlap footage, stair views, or queue scenes were used."}
    (out / "result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"result": report["result"], "run_dir": report["run_dir"]}, indent=1))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
