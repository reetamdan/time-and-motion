#!/usr/bin/env bash
# INSTALLATION-TIME, TEST-ONLY: a local RTSP server used to exercise the live-source path with a
# controlled stream (scripts/validate_rtsp_source.py). Not part of the runtime.
#   .install/wsl/mediamtx/mediamtx   mediamtx v1.21.2 (MIT), checksum-verified
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
DEST="${CCTV_WSL_TOOLS:-$ROOT/.install/wsl}"
URL="https://github.com/bluenviron/mediamtx/releases/download/v1.21.2/mediamtx_v1.21.2_linux_amd64.tar.gz"
SHA="121be6e00397e473a95d83c75f4fcd3819a6af3b0e1a50889cba43ab4fab6a27"
[[ "$(uname -s)" == "Linux" ]] || { echo "ERROR: run inside WSL/Linux" >&2; exit 2; }
mkdir -p "$DEST/downloads" "$DEST/mediamtx"
archive="$DEST/downloads/$(basename "$URL")"
if [[ ! -f "$archive" ]] || ! echo "$SHA  $archive" | sha256sum -c --quiet - 2>/dev/null; then
  curl -fL --retry 3 -o "$archive.part" "$URL" && mv "$archive.part" "$archive"
fi
echo "$SHA  $archive" | sha256sum -c --quiet - || { echo "ERROR: checksum mismatch" >&2; rm -f "$archive"; exit 3; }
tar -xzf "$archive" -C "$DEST/mediamtx" mediamtx LICENSE
echo "mediamtx ready: $("$DEST/mediamtx/mediamtx" --version 2>&1 | head -1)"
