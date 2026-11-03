#!/usr/bin/env python3
"""Move only the new generic-builder report into RT-DETR candidate evidence."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "reports" / "model-evaluation" / "rt-detr" / "engine"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-rc", type=int, required=True)
    args = parser.parse_args()
    console = OUT / "build-engines-console.log"
    text = console.read_text(encoding="utf-8", errors="replace")
    ids = sorted(set(re.findall(r"/workspace/reports/engines/([0-9]{8}T[0-9]{6}Z)", text)))
    moved = None
    if ids:
        if len(ids) != 1:
            raise SystemExit(f"Ambiguous generic builder report IDs: {ids}")
        source = ROOT / "reports" / "engines" / ids[0]
        destination = OUT / ids[0]
        if destination.exists():
            raise SystemExit(f"Refusing to overwrite {destination}")
        if not source.is_dir():
            raise SystemExit(f"Expected new builder report is missing: {source}")
        os.replace(source, destination)
        moved = str(destination.relative_to(ROOT))
        try:
            source.parent.rmdir()
        except OSError:
            pass
    engine = ROOT / "models" / "rt-detr-person-candidate" / "rt_detr_r18vd_raw_fp32_sm120.engine"
    record = {
        "schema_version": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "build_process_exit": args.build_rc,
        "command": "bash scripts/setup/build-engines.sh --onnx /workspace/models/rt-detr-person-candidate/rt_detr_r18vd_raw.onnx --engine /workspace/models/rt-detr-person-candidate/rt_detr_r18vd_raw_fp32_sm120.engine --validator /workspace/scripts/rt-detr-candidate/validate_engine.sh -- --stronglyTyped --noTF32",
        "builder": "scripts/setup/build-engines.sh", "builder_sha256": sha256(ROOT / "scripts" / "setup" / "build-engines.sh"),
        "precision": "FP32 strongly typed; --noTF32; no --fp16/--int8/--best",
        "generic_report_moved_to": moved,
        "move_reason": "The generic builder is otherwise compatible and non-overwriting, but its fixed reports/engines output is relocated to the owned candidate report tree.",
        "engine_exists_nonempty": engine.is_file() and engine.stat().st_size > 0,
        "engine_sha256": sha256(engine) if engine.is_file() and engine.stat().st_size else None,
        "note": "trtexec exit alone is not detection evidence; inspect validator and DeepStream result separately."
    }
    destination = OUT / "build-command.json"
    if destination.exists():
        raise SystemExit(f"Refusing to overwrite {destination}")
    destination.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
