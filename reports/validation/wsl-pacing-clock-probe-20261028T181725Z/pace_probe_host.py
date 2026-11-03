import subprocess, sys, time, json
root = "/mnt/c/Users/Reetam/Desktop/projects/aditya_freelance/dsa/cctv"
frames = int(sys.argv[1])
p = subprocess.Popen(["wsl.exe", "-d", "Ubuntu-22.04", "-e", "python3", "-u", f"{root}/.tmp/pace_probe_guest.py",
                      f"{root}/perception/deepstream/service_maker/extract_metadata_v2.py", str(frames)],
                     stdout=subprocess.PIPE, text=True, bufsize=1)
rows = []; end = None
for line in p.stdout:
    t = time.time()
    if line.startswith("END"): end = line.strip(); break
    rows.append((int(line), t))
p.wait()
frame_s = 1001 / 30000
t0 = rows[0][1]
ahead = [(n * frame_s) - (t - t0) for n, t in rows]
n = len(rows)
xs = [r[0] * frame_s for r in rows]; ys = [r[1] - t0 for r in rows]
mx, my = sum(xs) / n, sum(ys) / n
rate = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
settle = [a for (nn, t), a in zip(rows, ahead) if t - t0 > 70]
print(json.dumps({"frames": n, "source_s": round(xs[-1], 2), "host_s": round(ys[-1], 2), "rate_host_per_source": round(rate, 5),
                  "max_ahead_s": round(max(ahead), 3), "max_ahead_after_70s": round(max(settle), 3) if settle else None,
                  "min_ahead_s": round(min(ahead), 3), "final_ahead_s": round(ahead[-1], 3), "guest_end": end}))
