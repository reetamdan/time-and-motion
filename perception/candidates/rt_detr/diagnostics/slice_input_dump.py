#!/usr/bin/env python3
"""Slice selected frames out of nvinfer's ip_tensor_dump.bin (CHW float32 per batch)."""
import argparse, hashlib, json
from pathlib import Path



def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dump", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--frames", default="141,283")
    parser.add_argument("--network-size", default="640", help="square edge (640, 512) or HEIGHTxWIDTH (1080x1920)")
    args = parser.parse_args()
    height, width = (int(v) for v in args.network_size.split("x")) if "x" in args.network_size else (int(args.network_size),) * 2
    FRAME_BYTES = 3 * height * width * 4
    record = {"schema_version": 1, "dump_present": args.dump.is_file(), "frame_bytes": FRAME_BYTES,
              "layout": "CHW float32 little-endian, first batch item, appended once per nvinfer batch", "frames": []}
    if record["dump_present"]:
        size = args.dump.stat().st_size
        record.update({"dump_size_bytes": size, "dump_batches": size // FRAME_BYTES,
                       "dump_size_is_whole_batches": size % FRAME_BYTES == 0})
        with args.dump.open("rb") as handle:
            for index in (int(v) for v in args.frames.split(",")):
                handle.seek(index * FRAME_BYTES)
                data = handle.read(FRAME_BYTES)
                if len(data) != FRAME_BYTES:
                    record["frames"].append({"frame_index": index, "error": "short read"})
                    continue
                path = args.out_dir / f"ds_input_{index:06d}.f32le"
                if path.exists():
                    raise SystemExit(f"Refusing to overwrite {path}")
                path.write_bytes(data)
                record["frames"].append({"frame_index": index, "path": path.name,
                                         "sha256": hashlib.sha256(data).hexdigest()})
    (args.out_dir / "input-dump.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2))
    if not record["dump_present"] or not record.get("dump_size_is_whole_batches"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
