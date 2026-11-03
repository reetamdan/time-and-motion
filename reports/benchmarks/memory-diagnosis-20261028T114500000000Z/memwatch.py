"""Diagnostic memory sampler: Windows RAM/commit, Vmmem, WSL guest meminfo, docker stats. Writes JSONL."""
import json, subprocess, sys, time
import psutil

out = open(sys.argv[1], "a", encoding="utf-8")
duration = float(sys.argv[2])
end = time.time() + duration
while time.time() < end:
    vm = psutil.virtual_memory(); sw = psutil.swap_memory()
    vmmem = sum(p.info["memory_info"].rss for p in psutil.process_iter(["name", "memory_info"]) if p.info["memory_info"] and (p.info["name"] or "").lower().startswith("vmmem"))
    py = {p.pid: round(p.info["memory_info"].rss / 2**20) for p in psutil.process_iter(["name", "memory_info"]) if p.info["memory_info"] and (p.info["name"] or "").lower() == "python.exe"}
    r = subprocess.run(["wsl.exe", "-d", "Ubuntu-22.04", "--", "bash", "-c",
                        "grep -E '^(MemTotal|MemAvailable|Cached|Dirty|Shmem|SwapFree|SwapTotal):' /proc/meminfo | tr -s ' ' | tr '\\n' ';'; echo; docker stats --no-stream --format '{{.Name}} {{.MemUsage}}' 2>/dev/null | tr '\\n' ';'"],
                       capture_output=True, text=True, timeout=60)
    lines = r.stdout.strip().splitlines()
    rec = {"t": round(time.time(), 1), "win_avail_mib": round(vm.available / 2**20), "win_used_pct": vm.percent,
           "pagefile_used_mib": round(sw.used / 2**20), "vmmem_rss_mib": round(vmmem / 2**20), "python_rss_mib": py,
           "wsl_meminfo": lines[0] if lines else "", "docker": lines[1] if len(lines) > 1 else ""}
    out.write(json.dumps(rec) + "\n"); out.flush()
    time.sleep(3)
