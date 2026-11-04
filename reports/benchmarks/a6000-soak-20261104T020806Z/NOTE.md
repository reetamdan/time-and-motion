# FAIL under the protocol as run; every performance gate passed

Rerun after 2cb9140, which started the probes once every camera reported a source position. That condition
was already true at t=0 (demo start returns at each camera's first frame, source position 0.067 s), while
history is sampled every 0.5 s of source time, so the same four t=0 history probes failed again. Corrected in
bd4be90 (probes start at 1 s of source time). Summaries only; the raw logs stayed on the workstation.
