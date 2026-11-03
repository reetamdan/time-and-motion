#!/usr/bin/env python3
"""Write exact HF pixel tensors as raw little-endian trtexec input files."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--export-run", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for index in (141, 283):
        source = args.export_run / f"hf_frame_{index:06d}.npz"
        with np.load(source) as data:
            pixels = np.ascontiguousarray(data["pixel_values"], dtype="<f4")
        if pixels.shape != (1, 3, 640, 640):
            raise SystemExit("Unexpected saved input shape")
        destination = args.out_dir / f"pixel_values_{index:06d}.f32le"
        if destination.exists():
            raise SystemExit(f"Refusing to overwrite {destination}")
        pixels.tofile(destination)
        records.append({"frame_index": index, "path": str(destination), "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
                        "shape": list(pixels.shape), "dtype": "little-endian float32", "size_bytes": destination.stat().st_size,
                        "source_npz": str(source), "source_npz_sha256": hashlib.sha256(source.read_bytes()).hexdigest()})
    result = {"schema_version": 1, "inputs": records}
    (args.out_dir / "inputs.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
