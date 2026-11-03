#!/usr/bin/env python3
"""Create an isolated export venv from exact pins and inventoried local wheels."""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
INSTALL = ROOT / ".install" / "rt-detr-export"
VENV = INSTALL / "venv"
WHEELS = INSTALL / "wheels"
REPORT = ROOT / "reports" / "model-evaluation" / "rt-detr" / "export"
REQUIREMENTS = INSTALL / "requirements.txt"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def log_path(name: str) -> Path:
    candidate = REPORT / f"{name}.log"
    attempt = 2
    while candidate.exists():
        candidate = REPORT / f"{name}-attempt-{attempt}.log"
        attempt += 1
    return candidate


def run_logged(command: list[str], name: str, env: dict[str, str]) -> None:
    path = log_path(name)
    with path.open("w", encoding="utf-8", newline="\n") as log:
        log.write("COMMAND: " + json.dumps(command) + "\n")
        log.flush()
        process = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, check=False)
    print(f"{name}: exit {process.returncode}; log={path}")
    if process.returncode:
        raise SystemExit(process.returncode)


def main() -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    WHEELS.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({"PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_CACHE_DIR": str(INSTALL / "pip-cache"), "HF_HUB_OFFLINE": "0"})
    if not (VENV / "Scripts" / "python.exe").exists():
        if VENV.exists():
            raise SystemExit(f"Incomplete venv directory exists; inspect before resume: {VENV}")
        run_logged([sys.executable, "-m", "venv", str(VENV)], "venv-create", env)
    python = str(VENV / "Scripts" / "python.exe")
    requirements_hash = sha256(REQUIREMENTS)
    run_logged([python, "-m", "pip", "download", "--dest", str(WHEELS), "--requirement", str(REQUIREMENTS), "--extra-index-url", "https://download.pytorch.org/whl/cpu"], "package-download", env)
    distributions = sorted(path for path in WHEELS.iterdir() if path.is_file() and path.suffix in {".whl", ".zip", ".gz"})
    if not distributions:
        raise SystemExit("No downloaded distributions found")
    inventory = {"schema_version": 1, "requirements_sha256": requirements_hash, "files": [{"name": path.name, "size_bytes": path.stat().st_size, "sha256": sha256(path)} for path in distributions]}
    inventory_path = INSTALL / "downloaded-files.json"
    serialized = json.dumps(inventory, indent=2) + "\n"
    if inventory_path.exists() and inventory_path.read_text(encoding="utf-8") != serialized:
        raise SystemExit("Downloaded distribution inventory changed; refusing to overwrite")
    inventory_path.write_text(serialized, encoding="utf-8")
    run_logged([python, "-m", "pip", "install", "--no-index", "--no-deps", "--find-links", str(WHEELS), "--requirement", str(REQUIREMENTS)], "package-install", env)
    run_logged([python, "-m", "pip", "check"], "pip-check", env)
    freeze = subprocess.run([python, "-m", "pip", "freeze", "--all"], cwd=ROOT, env=env, check=True, capture_output=True, text=True).stdout
    freeze_path = INSTALL / "installed-freeze.txt"
    if freeze_path.exists() and freeze_path.read_text(encoding="utf-8") != freeze:
        raise SystemExit("Installed freeze changed; refusing to overwrite")
    freeze_path.write_text(freeze, encoding="utf-8", newline="\n")
    code = "import json,torch,torchvision,transformers,onnx,onnxruntime,numpy,PIL; print(json.dumps({'torch':torch.__version__,'torchvision':torchvision.__version__,'transformers':transformers.__version__,'onnx':onnx.__version__,'onnxruntime':onnxruntime.__version__,'numpy':numpy.__version__,'pillow':PIL.__version__}))"
    versions = subprocess.run([python, "-c", code], cwd=ROOT, env={**env, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}, check=True, capture_output=True, text=True).stdout.strip()
    result = {
        "schema_version": 1, "result": "PASS", "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "venv": str(VENV.relative_to(ROOT)), "requirements": str(REQUIREMENTS.relative_to(ROOT)),
        "requirements_sha256": requirements_hash, "download_inventory": str(inventory_path.relative_to(ROOT)),
        "download_inventory_sha256": sha256(inventory_path), "installed_freeze": str(freeze_path.relative_to(ROOT)),
        "installed_freeze_sha256": sha256(freeze_path), "versions": json.loads(versions),
        "commands": [
            f"{sys.executable} -m venv {VENV}",
            f"{python} -m pip download --dest {WHEELS} --requirement {REQUIREMENTS} --extra-index-url https://download.pytorch.org/whl/cpu",
            f"{python} -m pip install --no-index --no-deps --find-links {WHEELS} --requirement {REQUIREMENTS}",
            f"{python} -m pip check"
        ]
    }
    result_path = REPORT / "environment.json"
    if result_path.exists():
        raise SystemExit(f"Refusing to overwrite {result_path}")
    result_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
