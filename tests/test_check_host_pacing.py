import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("check_host_pacing", ROOT / "scripts" / "benchmark" / "check_host_pacing.py")
check_host_pacing = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_host_pacing)

FRAME_NS = 33_366_667
T0 = 1_793_000_000_000_000_000


def _write(path: Path, frames: int, host_per_source: float, *, release: bool, lead_ns: int = 200_000_000) -> Path:
    lines = []
    for i in range(frames):
        pts = i * FRAME_NS
        emitted = T0 + int(pts * host_per_source) - lead_ns
        record = {"frame_number": i, "pts_ns": pts, "emitted_unix_ns": emitted, "objects": [{"bbox": [1, 2, 3, 4]}]}
        if release:
            record["read_unix_ns"] = emitted + 30_000_000
        lines.append(json.dumps(record))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_real_time_release_records_pass(tmp_path):
    result = check_host_pacing.check_file(_write(tmp_path / "cam.release.jsonl", 9000, 1.0, release=True))
    assert result["verdict"] == "PASS" and result["rate_judged"]
    assert abs(result["rate_host_per_source"] - 1) < 1e-4


def test_producer_ahead_of_host_time_fails_even_with_full_fps(tmp_path):
    # the pre-fix defect: guest monotonic 3.5 % fast, so the producer delivers 3.5 % more source per host second
    result = check_host_pacing.check_file(_write(tmp_path / "cam.release.jsonl", 9000, 0.965, release=True))
    assert result["verdict"] == "FAIL"
    assert result["drift_s"]["max_ahead"] > 10


def test_producer_records_are_corrected_by_the_step_clock_offset(tmp_path):
    # guest wall clock gained 0.6 s on the host over the step: host-time rate is slower than the guest-wall rate
    path = _write(tmp_path / "bench-01.jsonl", 18000, 1.0, release=False)
    (tmp_path / "summary.json").write_text(json.dumps({"clock_offset": {
        "before": {"wsl_minus_host_ns": 0, "uncertainty_ns": 30_000_000},
        "after": {"wsl_minus_host_ns": 600_000_000, "uncertainty_ns": 30_000_000}}}), encoding="utf-8")
    result = check_host_pacing.check_file(path)
    assert result["rate_guest_wall_per_source"] == 1.0
    assert result["rate_host_per_source"] < 1.0
    assert result["clock_correction"]["offset_change_s"] == 0.6
    assert result["verdict"] == "PASS"  # 0.6 s over 600 s is 0.1 %, inside 0.2 %


def test_producer_records_without_a_measured_offset_are_not_judged(tmp_path):
    result = check_host_pacing.check_file(_write(tmp_path / "bench-01.jsonl", 9000, 1.0, release=False))
    assert result["verdict"] == "NO_HOST_CLOCK"


def test_short_runs_are_judged_on_lead_only(tmp_path):
    result = check_host_pacing.check_file(_write(tmp_path / "cam.release.jsonl", 900, 0.98, release=True))
    assert not result["rate_judged"] and result["verdict"] == "PASS"
    assert check_host_pacing.check_file(_write(tmp_path / "short.release.jsonl", 300, 1.0, release=True))["verdict"] == "TOO_SHORT"


def test_directories_prefer_release_records_over_producer_records(tmp_path):
    _write(tmp_path / "cam-01.jsonl", 900, 1.0, release=False)
    _write(tmp_path / "cam-01.release.jsonl", 900, 1.0, release=True)
    (tmp_path / "resources.samples.jsonl").write_text('{"t": 1}\n', encoding="utf-8")
    assert [p.name for p in check_host_pacing.collect([tmp_path])] == ["cam-01.release.jsonl"]


def _write_wsl_sawtooth(path: Path, frames: int, host_per_source: float) -> Path:
    """Producer records whose emit times come from a WSL guest wall clock: 4.9 % fast, stepped back to host
    time every 30.5 host seconds (measured 2026-10-28). Host time of frame n = n * frame * host_per_source."""
    lines = []
    for i in range(frames):
        host = i * FRAME_NS / 1e9 * host_per_source
        since_sync = host % 30.5
        wall = host + since_sync * 0.049 - 0.85
        lines.append(json.dumps({"frame_number": i, "pts_ns": i * FRAME_NS, "emitted_unix_ns": T0 + int(wall * 1e9)}))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_wsl_sawtooth_producer_records_are_judged_at_sync_points(tmp_path):
    good = check_host_pacing.check_file(_write_wsl_sawtooth(tmp_path / "bench-01.jsonl", 21600, 1.0))
    assert good["sync_points"] >= 20 and good["verdict"] == "PASS"
    assert abs(good["rate_host_per_source"] - 1) < 1e-3 and good["drift_s"]["max_ahead"] < 0.1
    fast = check_host_pacing.check_file(_write_wsl_sawtooth(tmp_path / "bench-02.jsonl", 21600, 1 / 1.04))
    assert fast["verdict"] == "FAIL" and fast["drift_s"]["max_ahead"] > 20
