# Local/offline preflight checklist

- Started (UTC): `2026-10-23T20:41:21.959751+00:00`
- Mode: `development_static_preflight`
- Static preflight result: **FAIL**
- Failures: **5**; warnings: **9**
- Network isolation: **NOT VERIFIED**
- Offline acceptance: **NOT DETERMINED BY THIS STATIC CHECK**

Static file checks do not prove cold-start behavior, GPU inference, four-feed operation, network isolation, or acceptance.

## Checks

- **PASS** — `project_root`: C:\Users\Reetam\Desktop\projects\aditya_freelance\dsa\cctv
- **PASS** — `config_cameras_exists`: Found C:\Users\Reetam\Desktop\projects\aditya_freelance\dsa\cctv\configs\cameras.yaml
- **PASS** — `config_cameras_parse`: Document parsed as a mapping/object
- **PASS** — `config_floors_exists`: Found C:\Users\Reetam\Desktop\projects\aditya_freelance\dsa\cctv\configs\floors.yaml
- **PASS** — `config_floors_parse`: Document parsed as a mapping/object
- **PASS** — `config_zones_exists`: Found C:\Users\Reetam\Desktop\projects\aditya_freelance\dsa\cctv\configs\zones.yaml
- **PASS** — `config_zones_parse`: Document parsed as a mapping/object
- **PASS** — `config_portals_exists`: Found C:\Users\Reetam\Desktop\projects\aditya_freelance\dsa\cctv\configs\portals.yaml
- **PASS** — `config_portals_parse`: Document parsed as a mapping/object
- **PASS** — `config_analytics_exists`: Found C:\Users\Reetam\Desktop\projects\aditya_freelance\dsa\cctv\configs\analytics.yaml
- **PASS** — `config_analytics_parse`: Document parsed as a mapping/object
- **PASS** — `config_models_exists`: Found C:\Users\Reetam\Desktop\projects\aditya_freelance\dsa\cctv\configs\models.yaml
- **PASS** — `config_models_parse`: Document parsed as a mapping/object
- **PASS** — `config_runtime_exists`: Found C:\Users\Reetam\Desktop\projects\aditya_freelance\dsa\cctv\configs\runtime.yaml
- **PASS** — `config_runtime_parse`: Document parsed as a mapping/object
- **PASS** — `backend_config_schema`: Project configuration loader accepted all schemas and cross-references
- **PASS** — `floors_unique_ids`: 2 unique id(s)
- **PASS** — `cameras_unique_ids`: 4 unique id(s)
- **PASS** — `zones_unique_ids`: 6 unique id(s)
- **FAIL** — `camera_cam-01_source`: Local file is missing: C:\Users\Reetam\Desktop\projects\aditya_freelance\dsa\cctv\data\demo\cam01.mp4
- **WARN** — `camera_cam-01_source_hash`: Source checksum is not pinned: missing sidecar C:\Users\Reetam\Desktop\projects\aditya_freelance\dsa\cctv\data\demo\SHA256SUMS
- **FAIL** — `camera_cam-02_source`: Local file is missing: C:\Users\Reetam\Desktop\projects\aditya_freelance\dsa\cctv\data\demo\cam02.mp4
- **WARN** — `camera_cam-02_source_hash`: Source checksum is not pinned: missing sidecar C:\Users\Reetam\Desktop\projects\aditya_freelance\dsa\cctv\data\demo\SHA256SUMS
- **FAIL** — `camera_cam-03_source`: Local file is missing: C:\Users\Reetam\Desktop\projects\aditya_freelance\dsa\cctv\data\demo\cam03.mp4
- **WARN** — `camera_cam-03_source_hash`: Source checksum is not pinned: missing sidecar C:\Users\Reetam\Desktop\projects\aditya_freelance\dsa\cctv\data\demo\SHA256SUMS
- **FAIL** — `camera_cam-04_source`: Local file is missing: C:\Users\Reetam\Desktop\projects\aditya_freelance\dsa\cctv\data\demo\cam04.mp4
- **WARN** — `camera_cam-04_source_hash`: Source checksum is not pinned: missing sidecar C:\Users\Reetam\Desktop\projects\aditya_freelance\dsa\cctv\data\demo\SHA256SUMS
- **PASS** — `enabled_camera_count`: 4 enabled camera(s); no stream-capacity claim is made
- **PASS** — `config_cross_references`: Available camera/zone floor references and unique ids were checked
- **FAIL** — `selected_model`: selected_model must reference an enabled model for the demo
- **WARN** — `model_rt-detr-person-candidate_disabled`: Disabled/unselected candidate is not required by this demo; its accuracy and runtime remain unevaluated
- **WARN** — `model_yolo-person-candidate_disabled`: Disabled/unselected candidate is not required by this demo; its accuracy and runtime remain unevaluated
- **WARN** — `tracker_enabled`: Authoritative tracker is disabled; tracking is not ready
- **PASS** — `frontend_local_build`: Found C:\Users\Reetam\Desktop\projects\aditya_freelance\dsa\cctv\frontend\dist\index.html
- **PASS** — `frontend_html_external_assets`: No external script/style/media assets found in index.html
- **WARN** — `frontend_dynamic_network_review`: Static HTML inspection cannot prove built JavaScript, maps, fonts, workers, or APIs make no external requests
- **PASS** — `runtime_database_parent`: Database parent: C:\Users\Reetam\Desktop\projects\aditya_freelance\dsa\cctv\backend\var
- **WARN** — `network_isolation`: No operator attestation supplied; this script performs no external-network probe and proves no isolation

Machine-readable evidence, including observed file hashes where collected: `checklist.json`.
