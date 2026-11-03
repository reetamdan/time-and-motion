import ast, sys, time
src = open(sys.argv[1], encoding="utf-8").read()
nodes = [n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name in ("GuestHostClock", "SourcePacer")]
ns = {"time": time}
exec(compile(ast.Module(nodes, []), "x", "exec"), ns)
pacer = ns["SourcePacer"]("source", 0.2)
frame_ns = 1_000_000_000 * 1001 // 30000
for n in range(int(sys.argv[2])):
    pacer.wait(n * frame_ns)
    sys.stdout.write(f"{n}\n"); sys.stdout.flush()
print("END", pacer.clock.sync_steps, pacer.clock.rate(), pacer.reanchors, pacer.implausible_reads, flush=True)
