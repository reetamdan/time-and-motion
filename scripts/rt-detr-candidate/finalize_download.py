#!/usr/bin/env python3
"""Verify the project downloader receipt and move it into candidate evidence."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "reports" / "model-evaluation" / "rt-detr" / "provenance"
CHECKPOINT = ROOT / "models" / "rt-detr-person-candidate" / "upstream" / "model.safetensors"
MANIFEST = ROOT / "models" / "rt-detr-person-candidate" / "download-manifest.json"
EXPECTED_SHA256 = "d18309d0d7ea57048138885c4c6ecfcb1e24506fc6153b94ad484f8ab62c7115"
EXPECTED_SIZE = 80_904_640


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    log = (REPORT / "download.log").read_text(encoding="utf-8", errors="replace")
    match = re.search(r"^Receipt: (.+)$", log, re.MULTILINE)
    if not match:
        raise SystemExit("Downloader did not report a receipt")
    receipt_path = Path(match.group(1).strip())
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["manifest_sha256"] == sha256(MANIFEST)
    assert len(receipt["artifacts"]) == 1
    artifact = receipt["artifacts"][0]
    assert artifact["revision"] == "5650961749fa93567c0d46fc7f43ea4f9e914107"
    actual_sha256 = sha256(CHECKPOINT)
    actual_size = CHECKPOINT.stat().st_size
    assert actual_sha256 == EXPECTED_SHA256 == artifact["sha256"]
    assert actual_size == EXPECTED_SIZE == artifact["size_bytes"]
    destination = REPORT / "download-receipt.json"
    verification_path = REPORT / "checkpoint-verification.json"
    if destination.exists() or verification_path.exists():
        raise SystemExit("Refusing to overwrite existing download evidence")
    os.replace(receipt_path, destination)
    try:
        receipt_path.parent.rmdir()
    except OSError:
        pass  # Preserve unrelated receipts if another installation created any.
    verification = {
        "schema_version": 1, "result": "PASS",
        "command": "bash scripts/setup/download-models.sh --manifest models/rt-detr-person-candidate/download-manifest.json",
        "downloader": "scripts/setup/download-models.sh",
        "downloader_sha256": sha256(ROOT / "scripts" / "setup" / "download-models.sh"),
        "manifest": str(MANIFEST.relative_to(ROOT)), "manifest_sha256": receipt["manifest_sha256"],
        "checkpoint": str(CHECKPOINT.relative_to(ROOT)), "sha256": actual_sha256,
        "size_bytes": actual_size, "downloader_action": artifact["action"],
        "receipt": str(destination.relative_to(ROOT)),
        "note": "Installation bytes only; this is not export, inference, quality, or DeepStream evidence."
    }
    verification_path.write_text(json.dumps(verification, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(verification, indent=2))


if __name__ == "__main__":
    main()
