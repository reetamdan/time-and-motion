"""The batch supervisor: a killed wrapper stops the batch, and the running step gets to clean up."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time

import psutil

ROOT = Path(__file__).resolve().parents[1]

STEP = """
import sys, time
from pathlib import Path
marker = Path(sys.argv[1])
marker.write_text("started")
try:
    while True:
        time.sleep(0.2)
finally:
    marker.write_text("cleaned-up")
"""


def _wait(predicate, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.2)
    return False


def _state(run_dir: Path) -> str | None:
    try:
        return json.loads((run_dir / "status.json").read_text(encoding="utf-8"))["state"]
    except (OSError, ValueError, KeyError):
        return None


def _batch(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    script = tmp_path / "step.py"
    script.write_text(STEP, encoding="utf-8")
    first, second = tmp_path / "first.marker", tmp_path / "second.marker"
    spec = {"steps": [{"name": "first", "python": sys.executable, "script": str(script), "args": [str(first)]},
                      {"name": "second", "python": sys.executable, "script": str(script), "args": [str(second)]}]}
    batch = tmp_path / "batch.json"
    batch.write_text(json.dumps(spec), encoding="utf-8")
    return batch, first, second, tmp_path / "run"


def test_killed_wrapper_stops_the_batch_after_step_cleanup(tmp_path):
    batch, first, second, run_dir = _batch(tmp_path)
    # wrapper = a python process that only waits on the supervisor, standing in for the shell wrapper
    wrapper = subprocess.Popen([sys.executable, "-c",
                                "import subprocess,sys; subprocess.run(sys.argv[1:])",
                                sys.executable, str(ROOT / "scripts" / "run_supervised_batch.py"), "--batch", str(batch),
                                "--run-dir", str(run_dir), "--no-container-check"], cwd=ROOT)
    try:
        assert _wait(lambda: first.exists() and first.read_text() == "started", 60)
        supervisor = [p for p in psutil.Process(wrapper.pid).children() if "run_supervised_batch" in " ".join(p.cmdline())]
        assert supervisor
        wrapper.kill()  # the wrapper dies; nothing kills the supervisor directly
        assert _wait(lambda: first.read_text() == "cleaned-up", 60), "the running step did not get to clean up"
        assert _wait(lambda: _state(run_dir) not in (None, "running"), 60), "the supervisor did not finish"
        supervisor[0].wait(30)
    finally:
        if wrapper.poll() is None:
            wrapper.kill()
    assert not second.exists(), "the batch continued after its wrapper was killed"
    status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    assert status["state"] == "INTERRUPTED"
    assert "wrapper killed" in status["stop_reason"]
    assert status["not_started"] == ["second"]
    assert (run_dir / "INTERRUPTED.json").is_file()


def test_killed_supervisor_interrupts_its_step(tmp_path):
    batch, first, second, run_dir = _batch(tmp_path)
    supervisor = subprocess.Popen([sys.executable, str(ROOT / "scripts" / "run_supervised_batch.py"), "--batch", str(batch),
                                   "--run-dir", str(run_dir), "--no-container-check"], cwd=ROOT)
    try:
        assert _wait(lambda: first.exists() and first.read_text() == "started", 60)
        supervisor.kill()  # hard kill: no supervisor cleanup code runs at all
        assert _wait(lambda: first.read_text() == "cleaned-up", 60), "orphaned step kept running"
    finally:
        if supervisor.poll() is None:
            supervisor.kill()
    time.sleep(2)
    assert not second.exists()
