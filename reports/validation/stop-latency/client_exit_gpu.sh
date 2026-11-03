cd /mnt/c/Users/Reetam/Desktop/projects/aditya_freelance/dsa/cctv
DEEPSTREAM_CONTAINER_NAME=aegis-clientexit bash scripts/deepstream.sh python3 /workspace/perception/deepstream/service_maker/extract_metadata.py --camera-epoch clientexit --output /workspace/.tmp/agent/stop/clientexit.jsonl >/dev/null 2>&1 &
PID=$!
for i in $(seq 1 60); do docker inspect -f '{{.State.Running}}' aegis-clientexit 2>/dev/null | grep -q true && break; sleep 0.5; done
sleep 5; docker exec aegis-clientexit sh -c "wc -l < /workspace/.tmp/agent/stop/clientexit.jsonl"; S=$(date +%s.%N); docker stop aegis-clientexit >/dev/null; M=$(date +%s.%N); wait $PID; RC=$?; E=$(date +%s.%N)
echo "inside_wsl: stop=$(echo "$M-$S"|bc) client_exit_after_stop=$(echo "$E-$M"|bc) rc=$RC"
docker ps -a --filter name=aegis-clientexit --format "{{.Names}} {{.Status}}"; echo "jsonl_lines_final=$(wc -l < .tmp/agent/stop/clientexit.jsonl)"
