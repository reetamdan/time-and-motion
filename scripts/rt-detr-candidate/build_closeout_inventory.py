#!/usr/bin/env python3
"""Write a non-overwriting SHA-256 inventory for the closed RT-DETR attempt."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = ROOT / "reports/model-evaluation/rt-detr"
DESTINATION = REPORT_ROOT / "artifact-inventory.json"
OWNED_ROOTS = [
    ROOT / "models/rt-detr-person-candidate",
    ROOT / "perception/candidates/rt_detr",
    ROOT / "scripts/rt-detr-candidate",
    REPORT_ROOT,
]
ADDITIONAL_FILES = [ROOT / "perception/deepstream/service_maker/extract_metadata.py"]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    if DESTINATION.exists():
        raise SystemExit(f"Refusing to overwrite {DESTINATION}")
    paths: set[Path] = set(ADDITIONAL_FILES)
    for root in OWNED_ROOTS:
        for path in root.rglob("*"):
            if not path.is_file() or path == DESTINATION:
                continue
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            paths.add(path)
    entries = []
    for path in sorted(paths, key=lambda item: item.relative_to(ROOT).as_posix()):
        entries.append({
            "path": path.relative_to(ROOT).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": sha256(path),
        })
    report = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "hash_algorithm": "sha256",
        "entry_count": len(entries),
        "path_convention": "Paths are relative to the workspace root and use forward slashes.",
        "scope": [
            "models/rt-detr-person-candidate",
            "perception/candidates/rt_detr (excluding Python bytecode caches)",
            "scripts/rt-detr-candidate",
            "reports/model-evaluation/rt-detr",
            "perception/deepstream/service_maker/extract_metadata.py"
        ],
        "self_exclusion": "reports/model-evaluation/rt-detr/artifact-inventory.json",
        "note": "Current local byte identities only. Evidence outcomes remain defined by the individual reports and result.json.",
        "entries": entries,
    }
    with DESTINATION.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, indent=2)
        handle.write("\n")
    print(json.dumps({
        "inventory": DESTINATION.relative_to(ROOT).as_posix(),
        "entry_count": len(entries),
        "inventory_sha256": sha256(DESTINATION),
    }, indent=2))


if __name__ == "__main__":
    main()
