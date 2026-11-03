#!/usr/bin/env python3
"""Freeze and verify immutable upstream RT-DETR provenance (stdlib only)."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / "models" / "rt-detr-person-candidate"
OUT = ROOT / "reports" / "model-evaluation" / "rt-detr" / "provenance"
REVISION = "5650961749fa93567c0d46fc7f43ea4f9e914107"
UPSTREAM_REVISION = "df2a96c8c3a098567a1149bf0248da6efe6d495d"
EXPECTED_CHECKPOINT_SHA256 = "d18309d0d7ea57048138885c4c6ecfcb1e24506fc6153b94ad484f8ab62c7115"
EXPECTED_CHECKPOINT_SIZE = 80_904_640
HF_API = f"https://huggingface.co/api/models/PekingU/rtdetr_v2_r18vd/revision/{REVISION}"
HF_TREE = f"https://huggingface.co/api/models/PekingU/rtdetr_v2_r18vd/tree/{REVISION}?recursive=true&expand=true"
GITHUB_LICENSE_API = f"https://api.github.com/repos/lyuwenyu/RT-DETR/contents/LICENSE?ref={UPSTREAM_REVISION}"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "cctv-rt-detr-provenance/1", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=90) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status} for {url}")
        return response.read()


def write_new(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() == data:
            return  # Resumable only when the already-frozen bytes are identical.
        raise FileExistsError(f"Refusing to overwrite different evidence: {path}")
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_bytes(data)
    temporary.replace(path)


def main() -> None:
    result_path = OUT / "provenance.json"
    if result_path.exists():
        raise SystemExit(f"Refusing to overwrite {result_path}; already frozen")
    model_response = fetch(HF_API)
    tree_response = fetch(HF_TREE)
    model = json.loads(model_response)
    tree = json.loads(tree_response)
    assert model["id"] == "PekingU/rtdetr_v2_r18vd"
    assert model["sha"] == REVISION
    assert model["config"]["architectures"] == ["RtDetrV2ForObjectDetection"]
    assert model["cardData"]["license"] == "apache-2.0"
    siblings = {item["rfilename"] for item in model["siblings"]}
    assert "LICENSE" not in siblings and "LICENSE.txt" not in siblings
    entries = {item["path"]: item for item in tree}
    checkpoint = entries["model.safetensors"]
    assert checkpoint["size"] == EXPECTED_CHECKPOINT_SIZE
    assert checkpoint["lfs"]["oid"] == EXPECTED_CHECKPOINT_SHA256
    assert checkpoint["lfs"]["size"] == EXPECTED_CHECKPOINT_SIZE

    write_new(OUT / "hf-model-api.json", model_response)
    write_new(OUT / "hf-tree-api.json", tree_response)
    sources = []
    for name in ("README.md", "config.json", "preprocessor_config.json"):
        url = f"https://huggingface.co/PekingU/rtdetr_v2_r18vd/raw/{REVISION}/{name}"
        data = fetch(url)
        assert git_blob_sha1(data) == entries[name]["oid"], name
        assert len(data) == entries[name]["size"], name
        destination = MODEL / "upstream" / name
        write_new(destination, data)
        sources.append({"path": str(destination.relative_to(ROOT)), "url": url, "size_bytes": len(data), "sha256": sha256(data), "git_blob_sha1": git_blob_sha1(data)})

    config = json.loads((MODEL / "upstream" / "config.json").read_text(encoding="utf-8"))
    preprocessor = json.loads((MODEL / "upstream" / "preprocessor_config.json").read_text(encoding="utf-8"))
    assert config["id2label"]["0"] == "person" and config["label2id"]["person"] == 0
    assert config["num_queries"] == 300 and len(config["id2label"]) == 80
    assert preprocessor["do_resize"] is True and preprocessor["size"] == {"height": 640, "width": 640}
    assert preprocessor["do_rescale"] is True and preprocessor["rescale_factor"] == 1 / 255
    assert preprocessor["do_normalize"] is False and preprocessor["do_pad"] is False

    license_response = fetch(GITHUB_LICENSE_API)
    license_record = json.loads(license_response)
    assert license_record["type"] == "file" and license_record["encoding"] == "base64"
    license_data = base64.b64decode("".join(license_record["content"].split()), validate=True)
    assert git_blob_sha1(license_data) == license_record["sha"]
    assert b"Apache License" in license_data and b"Version 2.0" in license_data
    license_path = MODEL / "licenses" / "RT-DETR-upstream-Apache-2.0.LICENSE"
    write_new(license_path, license_data)
    write_new(OUT / "github-license-api.json", license_response)

    provenance = {
        "schema_version": 1,
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        "result": "PASS",
        "model": {
            "id": model["id"], "revision": REVISION,
            "architecture": model["config"]["architectures"][0],
            "checkpoint": {
                "path": "models/rt-detr-person-candidate/upstream/model.safetensors",
                "sha256": EXPECTED_CHECKPOINT_SHA256, "size_bytes": EXPECTED_CHECKPOINT_SIZE,
                "download_url": f"https://huggingface.co/PekingU/rtdetr_v2_r18vd/resolve/{REVISION}/model.safetensors",
                "verification": "HF expanded tree LFS oid/size matched supplied immutable expectations; downloader separately verifies local bytes"
            },
            "declared_license": model["cardData"]["license"],
            "coco_person_logit_slot": 0,
            "num_queries": config["num_queries"], "num_classes": len(config["id2label"]),
            "upstream_config_transformers_version": config.get("transformers_version")
        },
        "preprocessing": {
            "input_shape_chw": [3, 640, 640], "color": "RGB",
            "resize": "direct to 640x640; no aspect preservation",
            "rescale_factor": preprocessor["rescale_factor"], "do_normalize": False,
            "resample_bilinear_pil_enum": preprocessor["resample"]
        },
        "license_caveat": "HF metadata declares Apache-2.0, but the pinned HF tree contains no LICENSE file. The separately pinned upstream RT-DETR commit contains Apache-2.0; these are distinct sources and the caveat is preserved.",
        "upstream_license": {
            "repository": "lyuwenyu/RT-DETR", "revision": UPSTREAM_REVISION,
            "path": str(license_path.relative_to(ROOT)), "sha256": sha256(license_data),
            "git_blob_sha1": license_record["sha"], "api_url": GITHUB_LICENSE_API
        },
        "hf_source_files": sources,
        "official_api_evidence": ["hf-model-api.json", "hf-tree-api.json", "github-license-api.json"],
        "checkpoint_local_verification": "PENDING"
    }
    write_new(result_path, (json.dumps(provenance, indent=2) + "\n").encode())
    print(json.dumps({"result": "PASS", "output": str(result_path), "checkpoint_sha256": EXPECTED_CHECKPOINT_SHA256}, indent=2))


if __name__ == "__main__":
    main()
