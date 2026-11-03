#!/usr/bin/env bash
# Measure docker stop latency for a long-running process under the pinned wrapper.
set -u
cd /mnt/c/Users/Reetam/Desktop/projects/aditya_freelance/dsa/cctv
NAME=aegis-stoptest-$1
DEEPSTREAM_CONTAINER_NAME=$NAME ${WRAPPER:-bash scripts/deepstream.sh} "${@:2}" >/dev/null 2>&1 &
for i in $(seq 1 60); do docker inspect -f '{{.State.Running}}' $NAME 2>/dev/null | grep -q true && break; sleep 0.5; done
if [ -n "${OUTFILE:-}" ]; then for i in $(seq 1 400); do [ -f "$OUTFILE" ] && [ "$(wc -l < "$OUTFILE")" -ge 60 ] && break; sleep 0.05; done; echo "lines_at_stop=$(wc -l < "$OUTFILE")"; else sleep 3; fi
docker exec $NAME cat /opt/nvidia/deepstream/deepstream/entrypoint.sh 2>/dev/null | tail -5
docker exec $NAME ps -o pid,comm,args 2>/dev/null | head -6
S=$(date +%s.%N); docker stop $NAME >/dev/null; E=$(date +%s.%N)
echo "docker_stop_seconds=$(echo "$E - $S" | bc)"
wait
