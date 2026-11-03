import subprocess, sys, threading, time
mode = sys.argv[1]
cmd = ["wsl.exe", "-d", "Ubuntu-22.04", "--", "env", "DEEPSTREAM_CONTAINER_NAME=aegis-ce3", "bash",
       "/mnt/c/Users/Reetam/Desktop/projects/aditya_freelance/dsa/cctv/scripts/deepstream.sh", "python3",
       "/workspace/perception/deepstream/service_maker/extract_metadata.py", "--camera-epoch", "ce3", "--output", "/workspace/.tmp/agent/stop/ce3.jsonl"]
log = open(".tmp/agent/stop/ce3.log", "w")
p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=log, stdin=subprocess.DEVNULL if mode == "devnull" else None)
lines = [0]; first = threading.Event()
def reader():
    for line in p.stdout:
        if line.startswith(b"AEGIS_FRAME_V1:"):
            lines[0] += 1; first.set()
threading.Thread(target=reader, daemon=True).start()
first.wait(60); time.sleep(0.5)
t = time.monotonic()
subprocess.run(["wsl.exe", "-d", "Ubuntu-22.04", "--", "docker", "stop", "--time", "5", "aegis-ce3"], capture_output=True)
m = time.monotonic()
try:
    rc = p.wait(15)
except subprocess.TimeoutExpired:
    rc = "timeout"; p.kill(); p.wait()
print(f"mode={mode} frames_read_at_stop~{lines[0]} stop={m-t:.2f} wrapper_exit_after_stop={time.monotonic()-m:.2f} rc={rc}")
