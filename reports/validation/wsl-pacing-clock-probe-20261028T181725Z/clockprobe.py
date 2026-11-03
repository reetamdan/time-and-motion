import time
t_end = time.monotonic() + 75
prev = None; first = None; steps = []
while time.monotonic() < t_end:
    m = time.monotonic(); w = time.time(); d = w - m
    if first is None: first = (m, w)
    if prev is not None and abs(d - prev[2]) > 0.05:
        steps.append((round(m - first[0], 2), round(d - prev[2], 3)))
    prev = (m, w, d)
    time.sleep(0.01)
span_m = prev[0] - first[0]; span_w = prev[1] - first[1]
print("steps (t_mono, size_s):", steps)
print("mono span", round(span_m, 3), "wall span", round(span_w, 3), "wall/mono", round(span_w / span_m, 5))
