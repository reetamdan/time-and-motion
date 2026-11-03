#!/usr/bin/env python3
"""Static local-artifact checks. This does not test network isolation or acceptance."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import sys
from html.parser import HTMLParser
from typing import Any

try:
    import yaml  # type: ignore
except ImportError:
    yaml = None

CONFIG_NAMES = ("cameras", "floors", "zones", "portals", "analytics", "models", "runtime")
MEDIA_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm", ".mpg", ".mpeg", ".ts"}
MODEL_SUFFIXES = {".engine", ".plan", ".onnx", ".pt", ".pth", ".bin", ".safetensors", ".weights"}
MODEL_PATH_KEYS = {"path", "local_path", "checkpoint", "checkpoint_path", "onnx", "onnx_path", "engine", "engine_path", "weights_path"}


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def local_path(root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def scalar_id(item: Any) -> str | None:
    return item.get("id") if isinstance(item, dict) and isinstance(item.get("id"), str) else None


class ExternalAssets(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.references: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        attribute = "src" if tag in {"script", "img", "source", "video", "audio"} else "href" if tag == "link" else None
        if not attribute:
            return
        value = values.get(attribute)
        if value and (value.startswith(("http://", "https://", "//"))):
            self.references.append(value)


class Checks:
    def __init__(self, strict: bool) -> None:
        self.strict = strict
        self.items: list[dict[str, Any]] = []

    def add(self, name: str, status: str, detail: str, *, strict_failure: bool = False, **evidence: Any) -> None:
        if status == "WARN" and self.strict and strict_failure:
            status = "FAIL"
            detail += " (required in strict/frozen mode)"
        item = {"name": name, "status": status, "detail": detail}
        if evidence:
            item["evidence"] = evidence
        self.items.append(item)
        print(f"[{status}] {name}: {detail}")

    @property
    def failures(self) -> int:
        return sum(item["status"] == "FAIL" for item in self.items)

    @property
    def warnings(self) -> int:
        return sum(item["status"] == "WARN" for item in self.items)


def parse_document(path: Path, checks: Checks, label: str) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        checks.add(label, "FAIL", f"Cannot read UTF-8 document: {exc}")
        return None
    try:
        if path.suffix.lower() == ".json":
            data = json.loads(text)
        else:
            if yaml is None:
                checks.add(label, "FAIL", "PyYAML is not installed in the selected project interpreter; config was not parsed")
                return None
            data = yaml.safe_load(text)
    except Exception as exc:
        checks.add(label, "FAIL", f"Parse error: {exc}")
        return None
    if not isinstance(data, dict):
        checks.add(label, "FAIL", "Top-level document must be a mapping/object")
        return None
    checks.add(label, "PASS", "Document parsed as a mapping/object")
    return data


def unique_ids(items: Any, label: str, checks: Checks) -> set[str]:
    if not isinstance(items, list) or not items:
        checks.add(f"{label}_entries", "FAIL", "Expected a nonempty list")
        return set()
    ids: list[str] = []
    for index, item in enumerate(items):
        value = scalar_id(item)
        if value:
            ids.append(value)
        else:
            checks.add(f"{label}_{index}_id", "FAIL", "Every entry needs a string id")
    duplicates = sorted({value for value in ids if ids.count(value) > 1})
    if duplicates:
        checks.add(f"{label}_unique_ids", "FAIL", f"Duplicate ids: {', '.join(duplicates)}")
    else:
        checks.add(f"{label}_unique_ids", "PASS", f"{len(ids)} unique id(s)")
    return set(ids)


def check_file(root: Path, value: str, label: str, checks: Checks, *, hashing: bool = True) -> tuple[Path | None, str | None]:
    if "${" in value:
        checks.add(label, "FAIL", "Unresolved environment placeholder in a required local path")
        return None, None
    path = local_path(root, value)
    if not path.is_file():
        checks.add(label, "FAIL", f"Local file is missing: {path}")
        return path, None
    if not os.access(path, os.R_OK):
        checks.add(label, "FAIL", f"Local file is not readable: {path}")
        return path, None
    if path.stat().st_size == 0:
        checks.add(label, "FAIL", f"Local file is empty: {path}")
        return path, None
    digest = sha256(path) if hashing else None
    checks.add(label, "PASS", f"Readable nonempty local file ({path.stat().st_size} bytes)", path=str(path), sha256=digest)
    return path, digest


def check_replay_profile(root: Path, profile: Path, reference: Path | None, checks: Checks) -> None:
    _, profile_hash = check_file(root, str(profile), "replay_profile_file", checks)
    data = parse_document(profile, checks, "replay_profile_parse")
    if data is not None:
        found: list[tuple[str, str]] = []

        def walk(value: Any, key: str = "") -> None:
            if isinstance(value, dict):
                for child_key, child in value.items():
                    walk(child, str(child_key))
            elif isinstance(value, list):
                for child in value:
                    walk(child, key)
            elif isinstance(value, str) and key.lower() in {"source", "clip", "video", "fixture", "fixture_path", "source_path"}:
                if Path(value).suffix.lower() in MEDIA_SUFFIXES:
                    found.append((key, value))

        walk(data)
        if not found:
            checks.add("replay_profile_sources", "FAIL", "No recognizable local media source keys were found in the profile")
        for index, (key, value) in enumerate(found):
            check_file(root, value, f"replay_source_{index}_{key}", checks)
    if reference is not None:
        check_file(root, str(reference), "replay_reference_file", checks)
    checks.add("replay_profile_provenance", "PASS" if profile_hash else "FAIL", "Profile identity is tied to the observed SHA-256", sha256=profile_hash)


def sidecar_sha256(source: Path) -> tuple[str | None, str]:
    """Read standard SHA256SUMS entries without changing the strict camera schema."""
    manifest = source.parent / "SHA256SUMS"
    if not manifest.is_file():
        return None, f"missing sidecar {manifest}"
    try:
        lines = manifest.read_text(encoding="utf-8").splitlines()
    except Exception as exc:
        return None, f"cannot read sidecar {manifest}: {exc}"
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split(maxsplit=1)
        if len(parts) != 2 or not re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            continue
        candidate = parts[1].strip().lstrip("*")
        if (manifest.parent / candidate).resolve() == source.resolve():
            return parts[0].lower(), f"entry in {manifest}"
    return None, f"no entry for {source.name} in {manifest}"


def safe_error(exc: Exception) -> str:
    return re.sub(r"(rtsp://)[^/\s@]+@", r"\1<redacted>@", str(exc))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--strict", action="store_true", help="Require frozen hashes/revisions and other acceptance-ready artifacts")
    parser.add_argument("--operator-isolation-confirmed", action="store_true")
    parser.add_argument("--isolation-method", default="")
    parser.add_argument("--require-isolation-attestation", action="store_true")
    parser.add_argument("--replay-profile")
    parser.add_argument("--reference")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    output = Path(args.output_dir).resolve()
    if output.exists():
        print(f"ERROR: refusing to overwrite evidence directory: {output}", file=sys.stderr)
        return 1
    output.mkdir(parents=True)
    checks = Checks(args.strict)
    started = utcnow()
    checks.add("project_root", "PASS" if root.is_dir() else "FAIL", str(root))

    documents: dict[str, Any] = {}
    for name in CONFIG_NAMES:
        path = root / "configs" / f"{name}.yaml"
        if not path.is_file():
            checks.add(f"config_{name}_exists", "FAIL", f"Missing {path}")
            continue
        checks.add(f"config_{name}_exists", "PASS", f"Found {path}")
        documents[name] = parse_document(path, checks, f"config_{name}_parse")

    try:
        from backend.app.configuration import load_configuration  # type: ignore
    except ImportError as exc:
        checks.add("backend_config_schema", "FAIL", f"Project configuration validator is unavailable: {safe_error(exc)}")
    else:
        try:
            load_configuration(root / "configs", root)
        except Exception as exc:
            checks.add("backend_config_schema", "FAIL", f"Project schema/cross-reference validation failed: {safe_error(exc)}")
        else:
            checks.add("backend_config_schema", "PASS", "Project configuration loader accepted all schemas and cross-references")

    cameras = documents.get("cameras", {}).get("cameras") if isinstance(documents.get("cameras"), dict) else None
    floors = documents.get("floors", {}).get("floors") if isinstance(documents.get("floors"), dict) else None
    zones = documents.get("zones", {}).get("zones") if isinstance(documents.get("zones"), dict) else None
    floor_ids = unique_ids(floors, "floors", checks) if "floors" in documents else set()
    camera_ids = unique_ids(cameras, "cameras", checks) if "cameras" in documents else set()
    zone_ids = unique_ids(zones, "zones", checks) if "zones" in documents else set()
    if cameras is not None and "cameras" in documents:
        enabled_count = 0
        for index, camera in enumerate(cameras):
            if not isinstance(camera, dict):
                continue
            camera_id = camera.get("id", f"index_{index}")
            enabled = camera.get("enabled", True)
            if not isinstance(enabled, bool):
                checks.add(f"camera_{camera_id}_enabled", "FAIL", "enabled must be boolean")
                continue
            if not enabled:
                continue
            enabled_count += 1
            floor = camera.get("floor", camera.get("floor_id"))
            if floor_ids and floor not in floor_ids:
                checks.add(f"camera_{camera_id}_floor", "FAIL", f"Unknown floor reference: {floor!r}")
            source_type = camera.get("source_type")
            source = camera.get("source")
            if source_type == "file":
                if not isinstance(source, str) or not source:
                    checks.add(f"camera_{camera_id}_source", "FAIL", "Enabled file camera needs a source path")
                    continue
                source_path, actual_hash = check_file(root, source, f"camera_{camera_id}_source", checks)
                declared_hash, hash_origin = sidecar_sha256(source_path or local_path(root, source))
                if declared_hash is None:
                    checks.add(f"camera_{camera_id}_source_hash", "WARN", f"Source checksum is not pinned: {hash_origin}", strict_failure=True)
                elif actual_hash and declared_hash.lower() != actual_hash:
                    checks.add(f"camera_{camera_id}_source_hash", "FAIL", f"Sidecar SHA-256 from {hash_origin} does not match the file")
                elif actual_hash:
                    checks.add(f"camera_{camera_id}_source_hash", "PASS", f"Sidecar SHA-256 matches ({hash_origin})")
                else:
                    checks.add(f"camera_{camera_id}_source_hash", "FAIL", "Source is unavailable, so its sidecar SHA-256 could not be compared")
            elif source_type == "rtsp":
                checks.add(f"camera_{camera_id}_rtsp", "WARN", "RTSP connectivity, local-network scope, credential redaction, and clock quality require runtime/operator verification")
            else:
                checks.add(f"camera_{camera_id}_source_type", "FAIL", f"Unsupported or missing source_type: {source_type!r}")
        checks.add("enabled_camera_count", "PASS" if enabled_count > 0 else "FAIL", f"{enabled_count} enabled camera(s); no stream-capacity claim is made")

    if zones is not None:
        for index, zone in enumerate(zones):
            if isinstance(zone, dict):
                floor = zone.get("floor", zone.get("floor_id"))
                if floor_ids and floor not in floor_ids:
                    checks.add(f"zone_{zone.get('id', index)}_floor", "FAIL", f"Unknown floor reference: {floor!r}")
    if floor_ids and zone_ids and camera_ids:
        checks.add("config_cross_references", "PASS", "Available camera/zone floor references and unique ids were checked")
    else:
        checks.add("config_cross_references", "FAIL", "Cross-reference validation is incomplete because required collections/ids are missing or invalid")

    if isinstance(documents.get("floors"), dict):
        for index, floor in enumerate(floors or []):
            if isinstance(floor, dict) and isinstance(floor.get("geometry_ref"), str):
                check_file(root, floor["geometry_ref"], f"floor_{floor.get('id', index)}_geometry", checks)

    models_doc = documents.get("models")
    if isinstance(models_doc, dict):
        models = models_doc.get("models")
        if isinstance(models, dict):
            models = [dict(value, id=key) if isinstance(value, dict) and "id" not in value else value for key, value in models.items()]
        if not isinstance(models, list) or not models:
            checks.add("models_entries", "FAIL", "models.yaml must contain a nonempty models list or mapping")
        else:
            selected_id = models_doc.get("selected_model")
            selected_active = any(isinstance(model, dict) and model.get("id") == selected_id and model.get("enabled") is True for model in models)
            checks.add("selected_model", "PASS" if selected_active else "FAIL", "selected_model must reference an enabled model for the demo")
            for index, model in enumerate(models):
                if not isinstance(model, dict):
                    checks.add(f"model_{index}", "FAIL", "Model entry must be a mapping")
                    continue
                name = str(model.get("id", model.get("name", index)))
                if model.get("enabled") is not True or model.get("id") != selected_id:
                    checks.add(f"model_{name}_disabled", "WARN", "Disabled/unselected candidate is not required by this demo; its accuracy and runtime remain unevaluated")
                    continue
                revision = model.get("source_revision", model.get("revision"))
                if not isinstance(revision, str) or not revision.strip() or revision.lower() in {"latest", "main", "master", "head"}:
                    checks.add(f"model_{name}_revision", "WARN", "Missing immutable source_revision/revision", strict_failure=True)
                else:
                    checks.add(f"model_{name}_revision", "PASS", f"Pinned revision: {revision}")
                for provenance_key in ("source_repository", "license_ref", "precision", "parser_revision"):
                    if not model.get(provenance_key):
                        checks.add(f"model_{name}_{provenance_key}", "WARN", f"Active model is missing {provenance_key}", strict_failure=True)
                artifacts = [(key, value) for key, value in model.items() if key in MODEL_PATH_KEYS and isinstance(value, str) and (Path(value).suffix.lower() in MODEL_SUFFIXES or "/" in value or "\\" in value)]
                if not artifacts:
                    checks.add(f"model_{name}_artifacts", "FAIL", "No recognized local model artifact path was configured")
                for key, value in artifacts:
                    _, actual_hash = check_file(root, value, f"model_{name}_{key}", checks)
                    if key in {"checkpoint", "checkpoint_path", "weights_path"}:
                        declared = model.get("sha256", model.get(f"{key}_sha256"))
                    elif key in {"engine", "engine_path"}:
                        declared = model.get("engine_sha256", model.get(f"{key}_sha256"))
                    else:
                        declared = model.get(f"{key}_sha256")
                    if declared is None:
                        checks.add(f"model_{name}_{key}_hash", "WARN", "No artifact SHA-256 is pinned", strict_failure=True)
                    elif not isinstance(declared, str) or actual_hash is None or declared.lower() != actual_hash:
                        checks.add(f"model_{name}_{key}_hash", "FAIL", "Artifact SHA-256 is invalid, mismatched, or unavailable for comparison")
                    else:
                        checks.add(f"model_{name}_{key}_hash", "PASS", "Artifact SHA-256 matches")
                if not any(key in {"engine", "engine_path"} for key, _ in artifacts):
                    checks.add(f"model_{name}_engine", "WARN", "No TensorRT engine path is configured for the selected model", strict_failure=True)
            tracker = models_doc.get("tracker")
            if isinstance(tracker, dict):
                if tracker.get("enabled") is not True:
                    checks.add("tracker_enabled", "WARN", "Authoritative tracker is disabled; tracking is not ready", strict_failure=True)
                else:
                    if not tracker.get("revision"):
                        checks.add("tracker_revision", "WARN", "Enabled tracker needs an immutable revision", strict_failure=True)
                    if isinstance(tracker.get("config_path"), str):
                        check_file(root, tracker["config_path"], "tracker_config", checks)
                    else:
                        checks.add("tracker_config", "FAIL", "Enabled tracker needs config_path")

    dist_index = root / "frontend" / "dist" / "index.html"
    if not dist_index.is_file():
        checks.add("frontend_local_build", "FAIL", f"Missing local production build: {dist_index}")
    else:
        checks.add("frontend_local_build", "PASS", f"Found {dist_index}", sha256=sha256(dist_index))
        parser_html = ExternalAssets()
        try:
            parser_html.feed(dist_index.read_text(encoding="utf-8"))
            if parser_html.references:
                checks.add("frontend_html_external_assets", "FAIL", f"External asset references found: {parser_html.references}")
            else:
                checks.add("frontend_html_external_assets", "PASS", "No external script/style/media assets found in index.html")
        except Exception as exc:
            checks.add("frontend_html_external_assets", "FAIL", f"Could not inspect built index.html: {exc}")
        checks.add("frontend_dynamic_network_review", "WARN", "Static HTML inspection cannot prove built JavaScript, maps, fonts, workers, or APIs make no external requests")

    runtime_doc = documents.get("runtime")
    if isinstance(runtime_doc, dict):
        serialized = json.dumps(runtime_doc, default=str)
        if re.search(r"https?://", serialized):
            checks.add("runtime_remote_url_review", "WARN", "runtime.yaml contains HTTP(S) values; review each for local-only behavior and redact credentials")
        for key in ("database_path", "db_path"):
            value = runtime_doc.get(key)
            if isinstance(value, str):
                database = local_path(root, value)
                checks.add("runtime_database_parent", "PASS" if database.parent.is_dir() else "WARN", f"Database parent: {database.parent}", strict_failure=True)

    if args.replay_profile:
        profile = local_path(root, args.replay_profile)
        reference = local_path(root, args.reference) if args.reference else None
        check_replay_profile(root, profile, reference, checks)
    elif args.reference:
        checks.add("replay_reference_argument", "FAIL", "--reference requires --replay-profile")

    if args.operator_isolation_confirmed:
        if args.isolation_method.strip():
            isolation = "OPERATOR ATTESTED; INDEPENDENT VERIFICATION NOT PERFORMED"
            checks.add("network_isolation_attestation", "WARN", f"Operator attestation only; method: {args.isolation_method.strip()}")
        else:
            isolation = "NOT VERIFIED"
            checks.add("network_isolation_attestation", "FAIL", "Isolation confirmation requires a nonempty --isolation-method")
    else:
        isolation = "NOT VERIFIED"
        status = "FAIL" if args.require_isolation_attestation else "WARN"
        checks.add("network_isolation", status, "No operator attestation supplied; this script performs no external-network probe and proves no isolation")

    failed = checks.failures
    static_result = "FAIL" if failed else "PASS"
    payload = {
        "schema_version": 1,
        "utc_started": started,
        "utc_finished": utcnow(),
        "project_root": str(root),
        "mode": "strict_frozen_static_preflight" if args.strict else "development_static_preflight",
        "python": platform.python_version(),
        "yaml_parser": getattr(yaml, "__version__", None) if yaml else None,
        "static_preflight_result": static_result,
        "failure_count": failed,
        "warning_count": checks.warnings,
        "network_isolation": isolation,
        "offline_acceptance": "NOT DETERMINED BY THIS STATIC CHECK",
        "checks": checks.items,
    }
    (output / "checklist.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    report = [
        "# Local/offline preflight checklist", "",
        f"- Started (UTC): `{started}`",
        f"- Mode: `{payload['mode']}`",
        f"- Static preflight result: **{static_result}**",
        f"- Failures: **{failed}**; warnings: **{checks.warnings}**",
        f"- Network isolation: **{isolation}**",
        "- Offline acceptance: **NOT DETERMINED BY THIS STATIC CHECK**", "",
        "Static file checks do not prove cold-start behavior, GPU inference, four-feed operation, network isolation, or acceptance.", "",
        "## Checks", "",
    ]
    for item in checks.items:
        detail = item["detail"].replace("\n", " ")
        report.append(f"- **{item['status']}** — `{item['name']}`: {detail}")
    report.extend(["", "Machine-readable evidence, including observed file hashes where collected: `checklist.json`.", ""])
    (output / "report.md").write_text("\n".join(report), encoding="utf-8")
    print(f"Evidence: {output}")
    print(f"STATIC PREFLIGHT: {static_result}; OFFLINE ACCEPTANCE: NOT DETERMINED")
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
