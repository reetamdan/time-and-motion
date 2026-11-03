#!/usr/bin/env bash
# One-container capability probe for the pinned DeepStream runtime (run through scripts/deepstream.sh).
# Prints key=value lines; the backend parses them and caches the result by fingerprint.
# Usage: bash scripts/deepstream.sh bash /workspace/scripts/lib/capability-probe.sh EXTRACTOR_PATH
set -u
EXTRACTOR="${1:?extractor path required}"
echo "probe_version=1"
if version="$(deepstream-app --version 2>/dev/null)"; then
  echo "deepstream_app=ok"
  echo "deepstream_version=$(printf '%s' "$version" | grep -iE 'version' | tr '\n' ' ' | cut -c1-200)"
else
  echo "deepstream_app=failed"
fi
for element in nvstreammux nvinfer nvtracker nvv4l2decoder; do
  if gst-inspect-1.0 "$element" >/dev/null 2>&1; then echo "gst_$element=ok"; else echo "gst_$element=missing"; fi
done
if python3 "$EXTRACTOR" --help >/dev/null 2>&1; then echo "extractor=ok"; else echo "extractor=failed"; fi
gpu="$(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader 2>/dev/null | head -n 1)"
if [[ -n "$gpu" ]]; then echo "gpu=ok"; echo "gpu_detail=$gpu"; else echo "gpu=failed"; fi
trt="$(dpkg-query -W -f='${Version}' libnvinfer10 2>/dev/null || true)"
echo "tensorrt_version=${trt:-unknown}"
echo "probe_complete=1"
