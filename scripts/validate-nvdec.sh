#!/usr/bin/env bash
# Headless NVDEC validation: count actual decoded/converted I420 frames, never infer success from EOS alone.
set -Eeuo pipefail
SOURCE="${1:-/workspace/demo/cam01.mp4}"
OUTPUT="${2:-/workspace/reports/validation/nvdec-cam01}"
mkdir -p "$OUTPUT"
if [[ ! -f "$SOURCE" ]]; then
  echo "Source video is unavailable inside the container: $SOURCE" >&2
  exit 1
fi
for plugin in nvv4l2decoder nvvideoconvert; do
  if ! gst-inspect-1.0 "$plugin" >/dev/null 2>&1; then
    echo "Required NVIDIA GStreamer plugin is unavailable: $plugin" >&2
    exit 1
  fi
done
set +e
set -o pipefail
bytes="$(gst-launch-1.0 -q \
  filesrc location="$SOURCE" ! qtdemux ! h264parse ! nvv4l2decoder ! nvvideoconvert ! \
  'video/x-raw,format=I420,width=1280,height=720' ! fdsink fd=3 3>&1 1>&2 | wc -c)"
rc=$?
set -e
if [[ ! "$bytes" =~ ^[0-9]+$ ]]; then
  echo "Decoder did not return a byte count." >&2
  exit 1
fi
frame_bytes=$((1280 * 720 * 3 / 2))
frames=$((bytes / frame_bytes))
remainder=$((bytes % frame_bytes))
printf 'pipeline_rc=%s bytes=%s frames=%s remainder=%s\n' "$rc" "$bytes" "$frames" "$remainder" | tee "$OUTPUT/result.txt"
python3 - "$SOURCE" "$rc" "$bytes" "$frames" "$remainder" "$OUTPUT/result.json" <<'PY'
import json, sys
from datetime import datetime, timezone
source, rc, byte_count, frames, remainder, destination = sys.argv[1:]
result = {
    'timestamp': datetime.now(timezone.utc).isoformat(),
    'source': source,
    'pipeline': 'filesrc ! qtdemux ! h264parse ! nvv4l2decoder ! nvvideoconvert ! I420 1280x720 ! fdsink',
    'pipeline_return_code': int(rc),
    'decoded_i420_bytes': int(byte_count),
    'decoded_frames': int(frames),
    'remainder_bytes': int(remainder),
    'passed': int(rc) == 0 and int(frames) > 0 and int(remainder) == 0,
}
with open(destination, 'w', encoding='utf-8') as handle:
    json.dump(result, handle, indent=2)
    handle.write('\n')
PY
[[ "$rc" -eq 0 && "$frames" -gt 0 && "$remainder" -eq 0 ]]
