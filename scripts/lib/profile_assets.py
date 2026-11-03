#!/usr/bin/env python3
"""Validate explicit project-root-relative local media in a workload profile."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

try:
    import yaml  # type: ignore
except ImportError:
    yaml = None

MEDIA_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm", ".mpg", ".mpeg", ".ts"}
SOURCE_KEYS = {"source", "sources", "clip", "clips", "video", "videos", "fixture", "fixture_path", "source_path", "path"}


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--streams", type=int)
    parser.add_argument("--reference")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    profile = Path(args.profile).resolve()
    output = Path(args.output).resolve()
    errors: list[str] = []
    sources: list[dict[str, Any]] = []
    profile_hash: str | None = None
    document: Any = None

    try:
        profile_hash = digest(profile)
        text = profile.read_text(encoding="utf-8")
        if not text.strip():
            raise ValueError("profile is empty")
        if profile.suffix.lower() == ".json":
            document = json.loads(text)
        else:
            if yaml is None:
                raise ValueError("PyYAML is required in the selected project interpreter")
            document = yaml.safe_load(text)
        if not isinstance(document, dict):
            raise ValueError("profile top level must be a mapping/object")
    except Exception as exc:
        errors.append(f"profile read/parse failed: {exc}")

    declared_streams = document.get("streams", document.get("stream_count")) if isinstance(document, dict) else None
    if args.streams is not None and declared_streams is not None:
        if isinstance(declared_streams, bool) or not isinstance(declared_streams, int) or declared_streams != args.streams:
            errors.append(f"profile stream count {declared_streams!r} does not match requested {args.streams}")

    found: list[str] = []

    def walk(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for child_key, child in value.items():
                walk(child, str(child_key).lower())
        elif isinstance(value, list):
            for child in value:
                walk(child, key)
        elif isinstance(value, str) and key in SOURCE_KEYS and Path(value).suffix.lower() in MEDIA_SUFFIXES:
            found.append(value)

    if document is not None:
        walk(document)
    if not found:
        errors.append("no explicit local media paths found; use source/clip/video/fixture keys with media filenames")

    seen: set[Path] = set()
    for value in found:
        candidate = Path(value).expanduser()
        path = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
        if path in seen:
            continue
        seen.add(path)
        item: dict[str, Any] = {"configured_path": value, "resolved_path": str(path)}
        try:
            if not path.is_file() or path.stat().st_size == 0:
                raise ValueError("missing or empty local media file")
            item["size_bytes"] = path.stat().st_size
            item["sha256"] = digest(path)
            item["status"] = "PASS"
        except Exception as exc:
            item["status"] = "FAIL"
            item["error"] = str(exc)
            errors.append(f"{value}: {exc}")
        sources.append(item)

    reference_evidence = None
    if args.reference:
        reference = Path(args.reference).resolve()
        try:
            reference_evidence = {"path": str(reference), "sha256": digest(reference), "status": "PASS"}
        except Exception as exc:
            reference_evidence = {"path": str(reference), "status": "FAIL", "error": str(exc)}
            errors.append(f"reference: {exc}")

    payload = {
        "schema_version": 1,
        "checked_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "result": "FAIL" if errors else "PASS",
        "scope": "profile and local media identity only; not a benchmark or replay result",
        "profile": str(profile),
        "profile_sha256": profile_hash,
        "requested_streams": args.streams,
        "sources": sources,
        "reference": reference_evidence,
        "errors": errors,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        print(f"Profile asset validation failed; evidence: {output}", file=sys.stderr)
        return 2
    print(f"Profile and {len(sources)} local media asset(s) validated; evidence: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
