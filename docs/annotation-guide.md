# Annotation and labelled evaluation (cam01-v1)

This package lets a person produce the ground truth that detector selection (AC-05) and tracking quality need. Nothing in it is ground truth until a human has reviewed it.

## What is in `data/annotations/cam01-v1/`

| Path | Content |
| --- | --- |
| `frames/frame_NNNNNN.jpg` | 59 full-resolution frames (1920×1080) decoded from `demo/cam01.mp4`. `NNNNNN` is the decoded frame index, which equals the DeepStream `frame_number`. |
| `annotations.template.json` | COCO-style file listing every image, with `annotations: []`, `info.review.status: UNREVIEWED`, and `annotation_status: UNREVIEWED` on every image. |
| `proposals/<model>.coco.json` | Recorded detector boxes for the same frames (TrafficCamNet smoke, RT-DETR candidate, RF-DETR candidate), marked `PROPOSAL_UNREVIEWED`. They may be imported to speed up labelling; they are **not** labels. |
| `evaluation-settings.json` | Frozen IoU, size, ignore-region, and metric settings, plus the decision rule. |
| `manifest.json` | sha256 of every file at creation. |

### Frame sets and tiers

| Set | Frames | Purpose | Tier |
| --- | --- | --- | --- |
| `detection` | every 35th frame from 15 (20 frames), spread across the camera pan | precision, recall, F1, count error | Tier 1 = the first 12 (minimum useful request) |
| `tracking-A` | 200–219 (consecutive) | MOTA, IDF1, ID switches | Tier 2 |
| `tracking-B` | 500–519 (consecutive) | the same, second scene position | Tier 3 |

The clip is a dense, mostly standing/walking outdoor crowd. Dense, occluded, and distant cases occur within these frames and are captured per box (attributes below), not as separate clips. A labelled fixed-camera clip would be needed for crossings.

## How to annotate

Use any tool that reads and writes COCO 1.0 (for example CVAT: create a task from `frames/`, import a proposal file as a starting point if helpful, correct it, export as COCO 1.0).

1. Draw one box around **every visible person**: standing, walking, seated, partially visible, at frame edges. Exclude reflections, posters, and statues.
2. Box the **visible extent**. For a person cut off by the frame edge, box the visible part and set `"truncated": true`.
3. Set `"occlusion"` to `none`, `partial` (less than about half hidden), or `heavy`.
4. Very small or unresolvable people: mark the box `"ignore": true` or, for an area where people cannot be separated (a far crowd), add a polygon to that image's `ignore_regions` (pixel coordinates). Boxes shorter than 16 px are ignored automatically.
5. Tracking sets only: give each person a `track_id` that stays the same in every frame of that sequence.
6. Set each image's `annotation_status` explicitly when you finish it:
   - `COMPLETE`: every person in the image is boxed and the image was reviewed. A `COMPLETE` image with **no boxes** is a reviewed empty frame: any detection on it counts as a false positive.
   - `UNREVIEWED`: not done yet. It is excluded from scoring and listed as missing coverage; it is never treated as an empty frame.
   - `EXCLUDED`: deliberately left out; `exclusion_reason` is required (for example `"motion blur"`).
7. When the work is done, a **second person** reviews the boxes and then sets:

```json
"info": {"review": {"status": "REVIEWED", "annotators": ["<name>"], "reviewer": "<name>",
                    "reviewed_at": "2026-11-01", "tool": "CVAT 2.x", "notes": ""}}
```

Save the result as `data/annotations/cam01-v1/annotations.json`. Partial work is fine: only `COMPLETE` images are scored, so Tier 1 alone (12 `COMPLETE` detection frames) already produces detection metrics, and the report lists the unreviewed and excluded frames with their reasons.

Example annotation entry:

```json
{"id": 1, "image_id": 15, "category_id": 1, "bbox": [812.0, 401.5, 38.0, 96.0],
 "iscrowd": 0, "occlusion": "partial", "truncated": false, "track_id": null}
```

## How evaluation runs

```powershell
.venv\Scripts\python.exe scripts\evaluation\evaluate_detections.py `
  --annotations data\annotations\cam01-v1\annotations.json `
  --model trafficcamnet-smoke=reports/validation/service-maker-cam01/frames.jsonl:2 `
  --model rt-detr-person-candidate=reports/model-evaluation/rt-detr/deepstream/run-20261026T143055Z/frames.jsonl:0 `
  --model rf-detr-person-candidate=reports/model-evaluation/rf-detr/deepstream/integration-20261026T162321Z/frames.jsonl:0
```

The evaluator refuses to run (exit 2) if the file is not `REVIEWED` by a named reviewer, if any image lacks a valid `annotation_status`, if an `EXCLUDED` image has no reason, if no image is `COMPLETE`, if an annotation points at an unknown image, or if its boxes are identical to a proposal file. Results go to a new directory `reports/model-evaluation/labeled/cam01-v1-<timestamp>/`.

Scoring rules (frozen in `evaluation-settings.json`):

- Precision is undefined (null, with the reason in `undefined`) only when nothing was predicted; recall only when the scored frames contain no countable person. F1 = 2·TP / (2·TP + FP + FN): it is **0** when there are errors and no true positive, and undefined only when TP = FP = FN = 0.
- A reviewed frame with no record in the pipeline JSONL is not an empty prediction: the detection result becomes `NOT_EVALUATED` with the missing frames listed.
- A tracking sequence is scored only when every frame is `COMPLETE`, every reviewed person box has a `track_id` (unique per frame), and the predictions carry tracker ids. Otherwise it is `NOT_EVALUATED` with the reason, never a number.

The JSONL paths above are earlier recorded runs. For the configuration actually selected now (v2 extractor, RT-DETR candidate config r2), produce fresh outputs first with `scripts/benchmark/run_detector_profiles.py` and point `--model` at the `backend/var/perception/cam-01/<epoch>.jsonl` files that run lists.

The `:2` / `:0` suffix is each model's own person class id (TrafficCamNet person is class 2; the candidate parsers emit person as class 0). It is never inferred.

## What the results can and cannot decide

Three things stay separate:

1. **Same-input engine/parser numerical consistency.** Existing reports; already PASS for both candidates.
2. **Cross-pipeline agreement under different preprocessing.** Existing reports; FAIL for both candidates, and the same strict check also fails between two CPU-only references that differ only in colour matrix (`reports/model-evaluation/numerical-status-20261028.json`). It measures sensitivity, not which detector is better.
3. **Labelled task accuracy.** This package.

The decision rule is in `evaluation-settings.json`: a candidate replaces the provisional TrafficCamNet profile only if it has better recall at comparable precision on both the detection set and the tracking sequences. More detections, or a numerical check on two frames, is not evidence that a detector is better.

After a decision, select the model in `configs/models.yaml` by setting `acceptance: accepted` on it and `selection_mode: evaluated_baseline`, and cite the labelled report in `evaluation_ref`. The loader refuses an `evaluated_baseline` selection whose model is not `accepted`.
