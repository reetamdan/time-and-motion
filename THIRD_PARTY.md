# Third-party software, models, and media register

Status: installation/provenance register, documentation-verified **2026-10-26**, not a completed legal audit. Exact installed versions must come from lockfiles, package metadata, native/container runtime probes, image digests, and model receipts. No dependency or model license is inferred from popularity, and no model weight or footage license is granted by this document.

## Application dependencies

### Python

The backend manifest currently declares exact direct versions:

| Package | Declared version | Provenance | License/notice status |
| --- | --- | --- | --- |
| FastAPI | 0.115.12 | `backend/requirements.txt` / Python package index at installation | Review installed package license and bundled notices before distribution |
| Pydantic | 2.11.3 | Same | Review before distribution |
| PyYAML | 6.0.2 | Same | Review before distribution |
| psutil | 7.0.0 | Same | Review before distribution |
| Starlette | 0.46.2 | Same | Review before distribution |
| Uvicorn | 0.34.2 | Same | Review before distribution |
| websockets | 15.0.1 | Same | Review before distribution |

Development-only dependencies and transitive packages must be inventoried from `backend/requirements-dev.txt` and the resolved environment/lock strategy. Do not treat this direct-dependency table as a complete SBOM.

### Frontend

`frontend/package-lock.json` is the resolved installation source of truth; `frontend/package.json` declares the direct packages below. Ranges below are declarations, not claims about the exact installed transitive tree.

| Package | Manifest declaration | Purpose | License/notice status |
| --- | --- | --- | --- |
| React / React DOM | `^19.3.1` | UI | Review exact locked packages and licenses |
| Recharts | `^3.10.1` | Local charts | Review exact locked packages and licenses |
| Lucide React | `^1.61.0` | Icons | Review icon/font redistribution notices |
| `@fontsource-variable/plus-jakarta-sans` | `5.3.0` | Locally bundled UI font (the family OpenIntelligentUI loads from Google Fonts; bundled here instead) | SIL Open Font License 1.1 (`node_modules/@fontsource-variable/plus-jakarta-sans/LICENSE`); no remote font fetch at runtime |
| `@fontsource/spline-sans-mono` | `5.3.0` | Locally bundled monospace font (same source as above) | SIL Open Font License 1.1; bundled |
| Vite | `^8.3.5` | Development/build/preview | Development dependency; review exact lock |
| TypeScript | `^7.0.2` | Type checking/build | Development dependency; review exact lock |
| Vitest | `^5.0.5` | Tests | Development dependency; review exact lock |
| `@vitejs/plugin-react`, React type packages | See `frontend/package.json` | Build/type support | Review exact locked packages and licenses |

Runtime serves local built assets. A package being present in `node_modules` is not provenance or license clearance. The previous Inter font package was removed with the UI refactor (2026-10-27).

### Adapted source: OpenIntelligentUI

| Item | Value |
| --- | --- |
| Upstream | `CopilotKit/OpenIntelligentUI` (<https://github.com/CopilotKit/OpenIntelligentUI>) |
| Revision adopted | `8391a94c1c3939c679e40ddd7bfc08a9ef690e70` (main on 2026-10-27; verified with `gh api repos/CopilotKit/OpenIntelligentUI/commits/main`; reference clone in `.install/upstream/OpenIntelligentUI`, not shipped) |
| License | MIT, "Copyright (c) Atai Barkai" — full text and notice reproduced in `frontend/src/design-system/NOTICE.md` |
| Adapted files | `frontend/src/design-system/tokens.css`, `base.css`, `components.css`, `components.tsx`, `chart.ts`; Aegis layout `frontend/src/app.css` follows `apps/app/src/app/chat.css` shell structure |
| Upstream sources used | `packages/design-system/src/index.ts` (THEME_CSS semantic ramps, FORM_STYLES_CSS, SVG colour classes); `apps/app/src/app/globals.css` (brand tokens, dark tokens, spacing/radius/shadow/chart tokens, `.brand-glass-container`, `.cta-btn--primary`, `.cta-banner`, `.explainer-card`, `.demo-gallery-drawer`, shimmer/fadeSlideIn); `apps/app/src/app/chat.css` (focus ring, skip link, `.answer-table`); `apps/app/src/components/demo-gallery/{index,category-filter,demo-card}.tsx` (drawer behaviour, chip filter, tinted pills); `apps/app/src/components/generative-ui/charts/{config,bar-chart}.tsx` (chart tooltip/axis config) |
| Not adopted | CopilotKit runtime/chat, generative-UI renderer and sandbox, `esm.sh` CDN import map, OpenAI/Jev model routing, PostHog analytics, launch telemetry (`docs/telemetry.md` upstream). No model key, assistant, or external request exists at runtime; upstream telemetry code is not present in this project, so `DO_NOT_TRACK` / `COPILOTKIT_TELEMETRY_DISABLED` have nothing to disable here |

## Native and container GPU/video stack

| Component | Locally verified status on 2026-10-26 | Provenance/notice boundary |
| --- | --- | --- |
| NVIDIA Windows/WSL driver | Observed driver fields and the preserved Windows NVML error are in `docs/environment.md` | Raw `nvidia-smi` logs remain the evidence; WSL/container success does not resolve the host failure or establish license terms |
| NVIDIA DeepStream | 9.2.0 exercised successfully through the pinned linux/amd64 container for the single-camera smoke scope | Retain image digest, container notices, plugin inventory and applicable terms; no native Ubuntu userland installation is claimed |
| CUDA driver/runtime | Container version log reports CUDA Driver 13.3 and CUDA Runtime 13.2 | Keep driver capability, container runtime and any native toolkit inventory separate |
| TensorRT | Container version log reports 10.16; `trtexec` identifies `v101600` and built the local FP16 engine | Exact package/build and redistribution notices still require review; an engine build is not accuracy or capacity evidence |
| GStreamer and NVIDIA plugins | Required decode/inference/tracker paths were exercised in the container | Exact core/plugin package versions and licenses were not inventoried by these reports; unrelated plugin-load and AAC audio warnings remain in run logs |
| NVIDIA Service Maker / `pyservicemaker` | Version 0.0.1 recorded by the Service Maker run summary | Container/API provenance and notices remain subject to review; the project extractor is hashed below |
| FFmpeg | Older host/native probes did not find a standalone executable; the host media probe used OpenCV's FFmpeg backend | Do not infer a standalone FFmpeg installation or redistribution terms from that backend observation |
| Docker / NVIDIA Container Toolkit | WSL installation log records Docker Engine 29.9.2 and toolkit 1.20.1 | Retain installer/package provenance and exact runtime context; the project wrapper uses only the locally inspected pinned image |

Version and execution observations do not complete a native package/container license review.

## Pinned image, model, engine, and configuration register

The following values were checked against local files and current evidence on 2026-10-26. File entries use SHA-256; the container entry is an OCI image-manifest digest, not a file hash.

| Artifact | Local path or reference | Verified digest / SHA-256 | Evidence |
| --- | --- | --- | --- |
| DeepStream linux/amd64 image | `ghcr.io/nvidia/deepstream@sha256:092818f8142d82c4611c9e96eb589a975cd928baa3e15a92f8f67b0ff6fcdb85` | `sha256:092818f8142d82c4611c9e96eb589a975cd928baa3e15a92f8f67b0ff6fcdb85` | `reports/environment/deepstream92/image-reference.txt`, `image-inspect.json`, `tag-manifest.json`; `configs/models.yaml` |
| TrafficCamNet ONNX checkpoint | `models/trafficcamnet-smoke/resnet18_trafficcamnet_pruned.onnx` | `d92f97bd840b68850a33c83e480fc6bd83b3097ff4cb517e0ea12671046bab7d` | `configs/models.yaml`; local file hash matches the pin |
| TrafficCamNet FP16 TensorRT engine | `models/trafficcamnet-smoke/resnet18_trafficcamnet_pruned.onnx_b1_gpu0_fp16.engine` | `e96668b546dc77b9e8002419b77d424e991c9d22fe2b39818b1d31d4f63b225f` | `configs/models.yaml`; `reports/validation/deepstream-cam01/result.json`; Service Maker result provenance |
| DeepStream application config | `perception/deepstream/cam01_trafficcamnet_smoke.txt` | `16a9592666dd88e27299c5ea61dbbc29de7b6c3ce8d02c5b4879f09ba0404a9a` | `reports/validation/deepstream-cam01/result.json` |
| TrafficCamNet inference config | `perception/deepstream/cam01_trafficcamnet_smoke_infer.txt` | `8165a340f72b5717b1a7fd41ae1213d6b4c66bdbdbcfe352bc62f0ec05f40405` | DeepStream and Service Maker validation results |
| NvDCF tracker config | `perception/deepstream/reference/config_tracker_NvDCF_perf.yml` | `13662e73754e8e6566c5495df40452aed7edba7ad8fd8a43335604b8643bae27` | DeepStream and Service Maker validation results; tracker revision in `configs/models.yaml` |
| TrafficCamNet labels | `perception/deepstream/reference/labels.txt` | `b8fc01e784d4b3e2f0c9daf5c96da4c41afe0895b3143cb4243c631aea8851a2` | `reports/validation/deepstream-cam01/result.json` |
| Service Maker extractor v1 (frozen baseline, not selected) | `perception/deepstream/service_maker/extract_metadata.py` | `6093b5d5d06089225b3bf60b0342fedccc50efde477f9663c5ef61ff91abad25` | Unchanged; checked by the artifact-manifest validator and `tests/test_service_maker.py` |
| Service Maker extractor v2 (selected) | `perception/deepstream/service_maker/extract_metadata_v2.py` | recorded in `configs/models.yaml` `pipeline.extractor_sha256` (identity history in the comment there) | Source-side pacing, schema 1.1 `emitted_unix_ns`, `--stage` for benchmarks, live RTSP sources; see `docs/decisions.md` |
| RT-DETR candidate inference config r2 | `perception/candidates/rt_detr/deepstream/cam01_rt_detr_infer_r2.txt` | recorded in `configs/models.yaml` (r1 `99fc10d6…` kept) | GPU-Super resize; `reports/model-evaluation/numerical-status-20261028.json` |
| Repeated demo footage (load/duration only) | `demo/derived/cam01-repeat33.mp4` | `03950b88b05d486a350199824b6898597e4f4ff020a2f89bf4a93649b59deafc` | `demo/derived/cam01-repeat33.manifest.json` (33 x `demo/cam01.mp4`, stream copy) |
| Model-selection config | `configs/models.yaml` | `d9cd27fa0969b4a44c7004db6866f98a27dd7bd593a44c1f2aa88ce654143873` | `reports/validation/deepstream-cam01/result.json`; local file hash matches |
| TrafficCamNet FP16 engine, RTX A6000 (sm86) | `models/trafficcamnet-smoke/resnet18_trafficcamnet_pruned_fp16_sm86.engine` | `6d127a999f809db769010235fa3157cfd520d423bffba895c7865eeb2b3a7336` | `configs/a6000/models.yaml`; `reports/a6000/build-logs/`; built 2026-11-04 with `scripts/a6000/build-engines.sh` |
| RT-DETR FP16 engine, RTX A6000 (sm86) | `models/rt-detr-person-candidate/rt_detr_r18vd_raw_fp16_sm86.engine` | `249941e733a1daecd7ed0d8e8463e4ecc6f1e1c9680dddd6440d28cfe645f2bf` | `configs/a6000/models.yaml`; FP16 vs FP32 comparison in `reports/a6000/optimization-receipt.json` |
| RF-DETR FP32 engine, RTX A6000 (sm86) | `models/rf-detr-person-candidate/rf_detr_small_1080p_rgb01_fp32_sm86.engine` | `2fc61ea742c6b90575e449cdf99bba34c0181cf873403c592c239134d4c77428` | `configs/a6000/models.yaml`; `reports/a6000/build-logs/` |
| RTX A6000 inference configs (generated) | `perception/deepstream/a6000/*.txt` | recorded in `configs/a6000/models.yaml` | Generated by `scripts/a6000/configure.py` from the upstream infer configs (engine path; RT-DETR FP16) |
| Current source clip | `demo/cam01.mp4` | `4201f7162eb0b9bcd3910f87b1a67a5bedac89c2b0a0bb4222e67d12f816b503` | `reports/media/cam01/media_probe.json`; DeepStream and Service Maker validation results |

**Extractor lineage:** the current local extractor emits each real frame as an `AEGIS_FRAME_V1:`-prefixed stdout record while retaining JSONL and run-summary outputs; the backend retains process logs. The older standalone `reports/validation/service-maker-cam01/result.json` pins extractor SHA-256 `70f4e617281bc22a3531502394f40d79aeb6dd615b9333a825e529be2f3af42e`, so its 710-frame `PASS` belongs to that earlier revision and must not be attributed to the current streaming file. The current live/backend/EOS reports do not embed an extractor SHA-256: they are behavioral evidence, not a cryptographic binding to the recomputed local-file hash above.

A matching hash establishes local artifact identity only. It does not independently establish upstream provenance, license rights, or redistribution permission. `configs/models.yaml` declares `NVIDIA DeepStream SDK License Agreement for bundled sample artifacts` as the smoke model's `license_ref`; container logs point to `/opt/nvidia/deepstream/deepstream/LicenseAgreement.pdf` and NVIDIA agreement notices. Those terms were not reviewed for this register, so no legal or production-use conclusion is made.

## Detector, tracker, and appearance candidates

No evaluated PRD detector baseline is selected. `trafficcamnet-smoke` is explicitly selected only as `selection_mode: provisional_smoke`; candidate names and smoke output do not establish accuracy, compatibility beyond the recorded scope, or license clearance.

| Candidate/component | Current status | Provenance/use constraint |
| --- | --- | --- |
| `trafficcamnet-smoke` / bundled TrafficCamNet | Enabled and selected for provisional single-camera plumbing smoke; local ONNX/engine hashes verified; not an evaluated PRD baseline | Preserve the pinned container/model/config identities above and review the declared SDK/bundled-artifact terms before distribution |
| `PekingU/rtdetr_v2_r18vd` | Isolated candidate (`models/rt-detr-person-candidate/`), revision `5650961749fa93567c0d46fc7f43ea4f9e914107`, checkpoint SHA-256 `d18309d0…7ab62c7115`; `accepted=false` (`reports/model-evaluation/rt-detr/result.json`, diagnostics in `reports/model-evaluation/rt-detr/diagnostics/result.json`) | HF metadata says Apache-2.0 but the pinned HF tree has no LICENSE file; upstream license evidence is recorded in `reports/model-evaluation/rt-detr/provenance/`. Review before redistribution |
| `Roboflow/rf-detr-small` | Isolated candidate (`models/rf-detr-person-candidate/`), revision `3bdc465063270f99769da5a1b4c00c68bd2d439d`, checkpoint SHA-256 `2bfab2fa…3eb5a1d`; DeepStream integration PASS, `accepted=false` (`reports/model-evaluation/rf-detr/result.json`) | HF metadata says Apache-2.0 but the pinned HF tree has no LICENSE file; upstream `roboflow/rf-detr` declares Apache-2.0 (`reports/model-evaluation/rf-detr/provenance/provenance.json`). The HF checkpoint is a transformers repackaging; equivalence to the upstream release is not verified. A published score cannot select the site baseline |
| `rt-detr-person-candidate` and `rf-detr-person-candidate` | Enabled `candidate_unaccepted` runtime profiles in `configs/models.yaml`, pinned to the artifacts above. Recorded sha256 values: RT-DETR ONNX `8048e153…`, engine `8de38386…`, parser `32497255…`; RF-DETR 1080p ONNX `13023fe6…`, engine `23084d72…`, parser `9f357433…`. Selectable only with `selection_mode: candidate_evaluation`; the default selection stays `trafficcamnet-smoke` | Labelled evaluation (`docs/annotation-guide.md`) is required before either can be `accepted` |
| NVIDIA multi-object tracker / NvDCF | Enabled for the provisional smoke path; boxes/IDs and lifecycle plumbing validated, not tracking accuracy | Revision `deepstream-9.2-nvdcf-perf-13662e73754e`; retain the config hash and review tracker/library notices |
| `kaiyangzhou/osnet` | Optional selective appearance candidate only; not part of the validated path | Pin implementation/weights and licenses. Some OSNet weights have MSMT17 training provenance; experimental use does not imply production clearance |
| Full VSS/CLIP-ReID gallery | Deferred | Do not introduce Kafka/Milvus/etcd/MinIO or extra models without a measured need and separate license/resource review |

Every active model entry must include source repository, immutable revision, checkpoint and engine hashes, class mapping, preprocessing/input shape, parser/export revisions, precision/type policy, and license reference. `scripts/setup/download-models.sh` requires these fields in its installation manifest and writes a receipt; the receipt is not an inference result.

## Footage, annotations, maps, fonts, and evidence

- CCTV clips, person crops, annotations, reference labels, floor plans, calibration images, and evidence exports require separate authorization/provenance records. No footage or dataset license is asserted here.
- The only enabled source is `demo/cam01.mp4`; its SHA-256 is registered above. `reports/media/cam01/provenance.json` records a user-supplied authorized-source statement with `independently_verified: false` and `license_or_redistribution_rights_claimed: false`. It supplies no ground truth or redistribution conclusion.
- Record source/owner or permission reference, capture/use authorization, retention/deletion constraints, checksums, synchronization provenance, and permitted evaluation/distribution scope near the local fixture manifest.
- Do not commit or distribute sensitive footage merely because processing is local. Local inference does not remove privacy, employment, consent, or retention obligations.
- Preserve notices for locally bundled fonts/icons/charts/maps. No remote fonts, map tiles, analytics, or hosted inference may become runtime dependencies.
- Synthetic/unit fixtures may support logic tests but cannot substitute for authorized physical-camera evidence or confer a physical-scene license.

## Completion checklist for a frozen/demo release

1. Generate a complete direct + transitive dependency inventory with exact versions and license texts/notices.
2. Record native SDK/plugin/container versions and image digests; retain local offline installers/images where permitted.
3. Complete model/engine manifests and receipts, including training-data/provenance restrictions and experimental-use constraints.
4. Complete footage/reference/font/map authorization and notice records.
5. Review redistribution obligations separately from local execution permission.
6. Link the reviewed inventory from the frozen run. Mark unresolved items explicitly instead of writing “open source” or “free for production” without evidence.

## Tools used at runtime (not redistributed)

| Tool | Use | Version observed | License note |
| --- | --- | --- | --- |
| FFmpeg / FFprobe (Windows build `Gyan.FFmpeg` via winget) | Evidence snapshot decode and stream-copy clip extraction; annotation-package frame extraction | 9.0 full build | LGPL/GPL depending on build; invoked as an external program, not linked or redistributed |
| NumPy | `scripts/config/fit_calibration.py` homography fitting only (dev requirement) | 2.5.4 | BSD-3-Clause |
| opencv-python-headless | `scripts/validate_deepstream_smoke.py` and its test (dev requirement) | 5.0.0.93 | Apache-2.0 (OpenCV); wheel bundles FFmpeg (LGPL) |

### WSL application-route toolchain (installed by `scripts/setup/install-wsl-toolchain.sh`, project-local, checksum-verified)

| Tool | Version / pinned archive SHA-256 | Use | License note |
| --- | --- | --- | --- |
| uv | 0.12.24, `b4dfaef4…3da6` | Installs a managed CPython for `.venv-wsl` | MIT / Apache-2.0 |
| CPython (python-build-standalone via uv) | 3.12.15 | `.venv-wsl` interpreter (WSL Ubuntu 22.04 ships 3.10, below the pinned packages' floor) | PSF License |
| Node.js | 24.14.1, `84d38715…22be` | Frontend build in WSL (isolated copy; never touches the Windows `node_modules`) | MIT (bundled third-party notices in the archive) |
| FFmpeg static build (BtbN, `autobuild-2026-09-30-13-08`) | n9.0.2, `68ee6468…6269` | Evidence extraction when the backend runs in WSL | GPL build; invoked as an external program, not linked or redistributed |
| mediamtx (TEST-ONLY, `scripts/setup/install-rtsp-test-server.sh`) | v1.21.2, `121be6e0…6a27` | Local RTSP server for the controlled live-source validation | MIT; not part of the runtime |
