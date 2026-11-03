#!/usr/bin/env bash
# Explicit INSTALLATION-TIME artifact downloader. Runtime scripts never call this file.
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
MANIFEST=""

usage() {
  cat <<'USAGE'
Usage: bash scripts/setup/download-models.sh --manifest PATH

The JSON manifest must contain an "artifacts" array. Every entry requires:
  url             Public HTTPS URL without credentials or query strings
  path            Destination relative to the project models/ directory
  revision        Immutable source revision (not "latest")
  sha256          Expected lowercase/uppercase hexadecimal SHA-256
  license         License identifier or exact local license reference
  provenance      Source repository/publisher and artifact description

Existing files are reused only when their hash matches; mismatches are never
overwritten. Downloads are atomic and verified before renaming. Record the pinned
artifact in THIRD_PARTY.md after installation.
USAGE
}

while (($#)); do
  case "$1" in
    --manifest) [[ $# -ge 2 && -n "$2" ]] || { echo "ERROR: --manifest requires a path" >&2; exit 64; }; MANIFEST="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage >&2; exit 64 ;;
  esac
done
[[ -n "$MANIFEST" && -f "$MANIFEST" ]] || { echo "ERROR: a readable JSON --manifest is required; no default model is downloaded" >&2; exit 64; }

PYTHON_BIN="${PYTHON:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3 || command -v python || true)"
elif ! [[ "$PYTHON_BIN" == */* ]]; then
  PYTHON_BIN="$(command -v -- "$PYTHON_BIN" || true)"
fi
[[ -n "$PYTHON_BIN" && -x "$PYTHON_BIN" ]] || { echo "ERROR: Python 3 is required for verified downloads" >&2; exit 2; }

exec "$PYTHON_BIN" - "$ROOT" "$MANIFEST" <<'PY'
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

root = Path(sys.argv[1]).resolve()
manifest_path = Path(sys.argv[2]).resolve()
models_root = (root / "models").resolve()
try:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
except Exception as exc:
    raise SystemExit(f"ERROR: cannot parse manifest {manifest_path}: {exc}")
artifacts = manifest.get("artifacts")
if not isinstance(artifacts, list) or not artifacts:
    raise SystemExit('ERROR: manifest "artifacts" must be a nonempty array')

def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

validated = []
seen = set()
for index, item in enumerate(artifacts):
    label = f"artifacts[{index}]"
    if not isinstance(item, dict):
        raise SystemExit(f"ERROR: {label} must be an object")
    missing = [key for key in ("url", "path", "revision", "sha256", "license", "provenance")
               if not isinstance(item.get(key), str) or not item.get(key).strip()]
    if missing:
        raise SystemExit(f"ERROR: {label} missing/invalid fields: {', '.join(missing)}")
    if item["revision"].strip().lower() in {"latest", "main", "master", "head"}:
        raise SystemExit(f"ERROR: {label}.revision must be immutable, not {item['revision']!r}")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", item["sha256"]):
        raise SystemExit(f"ERROR: {label}.sha256 must contain 64 hexadecimal characters")
    parsed = urlsplit(item["url"])
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise SystemExit(f"ERROR: {label}.url must be public HTTPS without credentials, query, or fragment")
    relative = Path(item["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise SystemExit(f"ERROR: {label}.path must stay below models/ without '..'")
    destination = (models_root / relative).resolve()
    if models_root != destination and models_root not in destination.parents:
        raise SystemExit(f"ERROR: {label}.path escapes models/")
    if destination in seen:
        raise SystemExit(f"ERROR: duplicate destination: {destination}")
    seen.add(destination)
    validated.append((item, destination))

receipts = []
for item, destination in validated:
    expected = item["sha256"].lower()
    if destination.exists():
        if not destination.is_file():
            raise SystemExit(f"ERROR: destination exists but is not a file: {destination}")
        actual = sha256(destination)
        if actual != expected:
            raise SystemExit(f"ERROR: existing file hash mismatch; refusing to overwrite: {destination}")
        action = "reused_verified"
        actual_size = destination.stat().st_size
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=destination.name + ".", suffix=".partial", delete=False) as handle:
                temporary = Path(handle.name)
                request = Request(item["url"], headers={"User-Agent": "cctv-install-artifact-verifier/1"})
                with urlopen(request, timeout=60) as response:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        handle.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            actual = digest.hexdigest()
            if actual != expected:
                raise RuntimeError(f"downloaded SHA-256 {actual} does not match expected {expected}")
            os.replace(temporary, destination)
            temporary = None
            action = "downloaded_verified"
            actual_size = size
        except Exception as exc:
            raise SystemExit(f"ERROR: download/verification failed for {destination}: {exc}")
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    receipt = dict(item)
    receipt.update({"path": str(destination.relative_to(root)), "sha256": expected,
                    "size_bytes": actual_size, "action": action})
    receipts.append(receipt)
    print(f"{action}: {destination}")

receipt_dir = models_root / "receipts"
receipt_dir.mkdir(parents=True, exist_ok=True)
timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
receipt_path = receipt_dir / f"download-{timestamp}.json"
if receipt_path.exists():
    raise SystemExit(f"ERROR: refusing to overwrite receipt: {receipt_path}")
receipt_path.write_text(json.dumps({
    "schema_version": 1,
    "utc_finished": dt.datetime.now(dt.timezone.utc).isoformat(),
    "manifest": str(manifest_path),
    "manifest_sha256": sha256(manifest_path),
    "artifacts": receipts,
    "note": "Verified installation artifacts only; this is not inference or acceptance evidence."
}, indent=2) + "\n", encoding="utf-8")
print(f"Receipt: {receipt_path}")
print("Record these exact artifacts and licenses in THIRD_PARTY.md. No model execution was validated.")
PY
