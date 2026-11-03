#!/usr/bin/env bash
# INSTALLATION-TIME: project-local toolchain for the WSL application route (backend + frontend build
# inside WSL). Installs nothing system-wide and changes no apt package of the distribution:
#   .install/wsl/uv        uv 0.12.24 (manages a CPython 3.12 under ~/.local/share/uv)
#   .install/wsl/node      Node.js 24.14.1 (same version as the Windows route)
#   .install/wsl/ffmpeg    static ffmpeg/ffprobe n9.0.2 (evidence extraction)
# Every archive is verified against a pinned SHA-256 before extraction; a mismatch aborts.
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
DEST="${CCTV_WSL_TOOLS:-$ROOT/.install/wsl}"
DOWNLOADS="$DEST/downloads"

UV_URL="https://github.com/astral-sh/uv/releases/download/0.12.24/uv-x86_64-unknown-linux-gnu.tar.gz"
UV_SHA="b4dfaef47d491a7296981f8374a4595f55dbf84e8937c8ecd2983574d8bb3da6"
NODE_URL="https://nodejs.org/dist/v24.14.1/node-v24.14.1-linux-x64.tar.xz"
NODE_SHA="84d38715d449447117d05c3e71acd78daa49d5b1bfa8aacf610303920c3322be"
FFMPEG_URL="https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-09-30-13-08/ffmpeg-n9.0.2-17-g2a571b6068-linux64-gpl-9.0.tar.xz"
FFMPEG_SHA="68ee646831adaae2495618346f3bba94ff207ff83bbd34d643e7004730d66269"
PYTHON_VERSION="3.12"

[[ "$(uname -s)" == "Linux" ]] || { echo "ERROR: run inside the WSL/Linux distribution" >&2; exit 2; }
for tool in curl tar xz sha256sum; do command -v "$tool" >/dev/null || { echo "ERROR: $tool is required" >&2; exit 2; }; done
mkdir -p "$DOWNLOADS"

fetch() {  # url sha target
  local url="$1" sha="$2" target="$DOWNLOADS/$(basename "$1")"
  if [[ ! -f "$target" ]] || ! echo "$sha  $target" | sha256sum -c --quiet - 2>/dev/null; then
    echo "Downloading $url" >&2
    curl -fL --retry 3 -o "$target.part" "$url"
    mv "$target.part" "$target"
  fi
  echo "$sha  $target" | sha256sum -c --quiet - || { echo "ERROR: checksum mismatch for $target" >&2; rm -f "$target"; exit 3; }
  printf '%s' "$target"
}

archive="$(fetch "$UV_URL" "$UV_SHA")"
rm -rf "$DEST/uv" && mkdir -p "$DEST/uv" && tar -xzf "$archive" -C "$DEST/uv" --strip-components=1
archive="$(fetch "$NODE_URL" "$NODE_SHA")"
rm -rf "$DEST/node" && mkdir -p "$DEST/node" && tar -xJf "$archive" -C "$DEST/node" --strip-components=1
archive="$(fetch "$FFMPEG_URL" "$FFMPEG_SHA")"
rm -rf "$DEST/ffmpeg" && mkdir -p "$DEST/ffmpeg" && tar -xJf "$archive" -C "$DEST/ffmpeg" --strip-components=1

"$DEST/uv/uv" python install "$PYTHON_VERSION"
PYTHON_BIN="$("$DEST/uv/uv" python find "$PYTHON_VERSION")"

cat > "$DEST/env.sh" <<EOF
# Source this file to use the project-local WSL toolchain (written by install-wsl-toolchain.sh).
export PATH="$DEST/node/bin:$DEST/ffmpeg/bin:$DEST/uv:\$PATH"
export CCTV_WSL_PYTHON="$PYTHON_BIN"
EOF
echo "WSL toolchain ready in $DEST"
echo "  uv:     $("$DEST/uv/uv" --version)"
echo "  python: $("$PYTHON_BIN" --version) ($PYTHON_BIN)"
echo "  node:   $("$DEST/node/bin/node" --version)"
echo "  ffmpeg: $("$DEST/ffmpeg/bin/ffmpeg" -hide_banner -version | head -1)"
echo "Next: source $DEST/env.sh && bash scripts/setup/setup.sh --isolated-frontend"
