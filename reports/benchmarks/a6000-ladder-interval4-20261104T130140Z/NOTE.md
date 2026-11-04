# Analysis interval 4: 8 and 12 feeds PASS; 16 failed at startup, not under load

`--analysis-frame-interval 4` (every 4th decoded frame analysed, 7.49 fps per camera; a recorded deviation).
At 16 feeds all sixteen pipelines loaded their TensorRT engine at the same moment and most failed
deserialization with CUDA `OutOfMemory (Requested size was 31137792 bytes)` while ~4 GB of 48 GB was in use;
nvinfer's fallback ONNX rebuild failed the same way, Service Maker printed "Unable to set the pipeline to the
playing state" and blocked, and every camera hit the 60 s first-frame deadline. GPU utilisation stayed at 0 %.
So 12 is not this profile's capacity; it is the last step before a startup failure.

Fixed in f59e25b (cameras load at most `replay.startup_concurrency` = 4 at a time; a start fails as soon as the
log shows the pipeline could not reach PLAYING). The 16+ steps were rerun after the fix in
`a6000-ladder-interval4-waves-*`. Summaries only; the raw logs stayed on the workstation.
