# cam01 media assessment

## Source and authorization

- Source filename: `cam01.mp4`
- Project path: `demo/cam01.mp4`
- Size: **17,687,764 bytes**
- SHA-256: `4201f7162eb0b9bcd3910f87b1a67a5bedac89c2b0a0bb4222e67d12f816b503`
- Authorization statement: the user states that the video was copied from a **user-authorized source** for this CCTV project. This records the user's statement only; it is not an independent verification, a license claim, or a grant of redistribution rights.

The supplied file's size and checksum matched those stated in the task. The source video was not modified.

## Actual media metadata

| Property | Observed value |
|---|---|
| Codec | H.264 / AVC; OpenCV FourCC `h264` |
| Decode backend | FFmpeg through `opencv-python-headless 5.0.0` |
| Dimensions | 1920 × 1080 pixels |
| Reported FPS | 29.97002997002997 |
| Container-reported frames | 710 |
| Sequentially decoded frames | 710 |
| Duration | 23.690333333333335 seconds at reported FPS |
| First / last frame timestamps | 0.0 ms / 23,656.96666666667 ms |
| Timestamp spacing | Strictly increasing; approximately 33.3667 ms between frames |
| Decode result | Clean EOF; decoded count matches container report; no decode errors observed |

This was a full sequential OpenCV decode check, not a formal compressed-bitstream conformance audit. Raw probe output is in `reports/media/cam01/media_probe.json`.

## Visual assessment

Six representative frames at approximately **0.000, 4.705, 9.443, 14.181, 18.919, and 23.657 seconds** were visually inspected, along with their contact sheet.

- **Scene:** A densely occupied outdoor pedestrian area with unpaved, rough ground in bright, hazy daylight.
- **Camera angle:** Elevated, oblique downward-looking view. No obvious large viewpoint change is visible across the six samples; this is not a camera-calibration assessment.
- **Crowd and occlusion:** Dense crowding throughout the samples, frequent person-to-person occlusion, partially visible bodies, and edge cropping. No exact or approximate numerical crowd count is asserted.
- **Motion:** People's positions change across the time-spread samples, with varied orientations and close interactions. No frame-by-frame motion annotation or track ground truth was created.

### Validation suitability

- **Known-positive person-detection smoke test: suitable.** Multiple clearly visible people are present in every inspected sample. The clip can support a smoke assertion that a detector produces some person detections, but it does not establish detection accuracy, recall, precision, or tracking quality.
- **Entrance validation: not supported.** No bounded doorway, gate, entrance threshold, or unambiguous entry/exit crossing zone is visible.
- **Queue validation: not supported.** Dense crowding alone does not establish an ordered queue; no stable queue and associated service point can be reliably identified from the samples.
- **Stair validation: not supported.** No staircase, stair landing, or vertical circulation transition is visible.
- **Multi-camera validation: not supported.** Only one short clip was supplied, calibration remains `MISSING`, and no overlap or floor-transition evidence is available.

No person identification or sensitive-trait inference was performed. No ground-truth person counts, tracks, entrance events, queue metrics, or stair events were created.

## Evidence files

All extracted images are below **3,000,000 bytes** and retain the source's 1920 × 1080 frame dimensions, except the separately sized contact sheet.

- `reports/media/cam01/contact_sheet.jpg` — 1800 × 876; 577,273 bytes
- `reports/media/cam01/frame_000000_t000000ms.jpg` — frame 0; 478,127 bytes
- `reports/media/cam01/frame_000141_t004705ms.jpg` — frame 141; 513,557 bytes
- `reports/media/cam01/frame_000283_t009443ms.jpg` — frame 283; 503,902 bytes
- `reports/media/cam01/frame_000425_t014181ms.jpg` — frame 425; 540,312 bytes
- `reports/media/cam01/frame_000567_t018919ms.jpg` — frame 567; 515,214 bytes
- `reports/media/cam01/frame_000709_t023657ms.jpg` — frame 709; 510,267 bytes
- `reports/media/cam01/provenance.json` — machine-readable provenance and assessment
- `reports/media/cam01/media_probe.json` — detailed decode and extraction results
- `reports/media/cam01/inspect_media.py` — reproducible probe/extraction script

## Configuration outcome

`configs/cameras.yaml` retains four camera slots. `cam-01` is named **Outdoor Pedestrian Area**, uses `./demo/cam01.mp4`, and is the only enabled camera. `cam-02`, `cam-03`, and `cam-04` are disabled for this single-source phase; their prior source paths and other settings are preserved for later authorized clips. Every camera's calibration status remains `MISSING`.

## Backend test result

Command run from the project root:

```text
.venv/Scripts/python.exe -m pytest tests --basetemp=backend/var/pytest -q
```

Result: **53 passed, 6 failed, 1 deprecation warning** in 9.70 seconds. No tests or backend files were edited.

1. `tests/test_api.py::test_snapshot_cameras_floors_and_real_host_metrics` — expected four enabled cameras; observed one, matching the requested single-source phase.
2. `tests/test_api.py::test_demo_start_is_idempotent_ack_stop_and_reset` — the assertion that every camera is `OFFLINE` failed under the new source/enablement state.
3. `tests/test_api.py::test_source_preview_range_and_spa_fallback` — expected disabled `cam-02` preview to return 404 `SOURCE_UNAVAILABLE`; observed HTTP 409.
4. `tests/test_configuration.py::test_missing_assets_do_not_create_fake_availability` — asserts that all camera source paths are missing; `cam-01` now intentionally exists.
5. `tests/test_runtime.py::test_available_source_is_isolated_from_missing_sources` — expected run status `DEGRADED`; observed `RUNNING`.
6. `tests/test_runtime.py::test_readiness_reports_actual_missing_sources_models_and_perception` — expected source readiness to be false; observed true with the existing enabled source.

The owned configuration was not reverted or distorted to satisfy assertions that conflict with the requested single-source setup.
