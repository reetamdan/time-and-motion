---
license: apache-2.0
tags:
- object-detection
- vision
datasets:
- coco
pipeline_tag: object-detection
library_name: transformers
---

# RF-DETR (Small)

RF-DETR is a real-time detection transformer family introduced in [RF-DETR: Neural Architecture Search for Real-Time Detection Transformers](https://huggingface.co/papers/2511.09554) by Robinson et al. and integrated in 🤗 Transformers via [PR #36895](https://github.com/huggingface/transformers/pull/36895).

## Model description

RF-DETR is an end-to-end object detection model that combines ideas from LW-DETR and Deformable DETR: a DINOv2-with-registers style ViT backbone (with an RF-DETR windowing pattern for efficient attention), a multi-scale projector between encoder and decoder, and a multi-scale deformable DETR decoder for fast convergence and strong accuracy–latency tradeoffs.

Key Architectural Details:
- **Backbone:** DINOv2-with-registers style ViT with RF-DETR **windowed / full** attention alternation (instead of a purely convolutional encoder).
- **Multi-scale fusion:** **RF-DETR multi-scale projector** (C2f-style blocks in the LW-DETR lineage) to aggregate multi-level backbone features before the decoder.
- **Decoder:** **Deformable DETR**-style decoder with multi-scale deformable cross-attention; depth and input resolution vary by checkpoint (NAS frontier).
- **Queries:** DETR-style object queries with bipartite matching and auxiliary decoder losses for training stability.

Training Details:
- **Detection losses:** classification plus bounding-box L1 and GIoU, with auxiliary losses on intermediate decoder layers.
- **Group DETR:** parallel decoder copies during training for faster convergence (same high-level idea as LW-DETR's Group DETR).
- **NAS (family-level):** the RF-DETR paper uses weight-sharing neural architecture search over practical accuracy–latency knobs after adapting a shared backbone on the target dataset, so many checkpoints correspond to different subnets without full independent retrains for every point on the frontier.

### How to use

You can use the raw model for object detection. See the [model hub](https://huggingface.co/models?search=stevenbucaille/rf-detr) to look for all available RF-DETR models.

Here is how to use this model:

```python
from transformers import AutoImageProcessor, RfDetrForObjectDetection
import torch
from PIL import Image
import requests

url = "http://images.cocodataset.org/val2017/000000039769.jpg"
image = Image.open(requests.get(url, stream=True).raw)

processor = AutoImageProcessor.from_pretrained("stevenbucaille/rf-detr-small")
model = RfDetrForObjectDetection.from_pretrained("stevenbucaille/rf-detr-small")

inputs = processor(images=image, return_tensors="pt")
outputs = model(**inputs)

# convert outputs (bounding boxes and class logits) to COCO API
# let's only keep detections with score > 0.35
target_sizes = torch.tensor([image.size[::-1]])
results = processor.post_process_object_detection(outputs, target_sizes=target_sizes, threshold=0.35)[0]

for score, label, box in list(zip(results["scores"], results["labels"], results["boxes"]))[:8]:
    box = [round(i, 2) for i in box.tolist()]
    print(
            f"Detected {model.config.id2label[label.item()]} with confidence "
            f"{round(score.item(), 3)} at location {box}"
    )
```
This should output:
```
Detected cat with confidence 0.993 at location [11.83, 55.12, 318.32, 473.23]
Detected cat with confidence 0.987 at location [347.05, 24.26, 639.54, 373.83]
Detected remote with confidence 0.99 at location [40.49, 72.61, 175.87, 117.71]
Detected remote with confidence 0.983 at location [333.95, 77.09, 371.07, 187.33]
Detected bed with confidence 0.498 at location [1.53, 1.25, 640.42, 475.72]
Detected remote with confidence 0.136 at location [338.68, 76.71, 371.2, 138.69]
Detected remote with confidence 0.241 at location [334.29, 77.9, 370.72, 187.68]
Detected remote with confidence 0.177 at location [340.43, 77.33, 371.02, 119.32]
```

## Training data

These checkpoints are trained on the standard [COCO 2017 object detection dataset](https://cocodataset.org/#home) label space (80 categories) as reflected in `config.id2label`.

### BibTeX entry and citation info

```bibtex
@misc{robinson2026rfdetrneuralarchitecturesearch,
      title={RF-DETR: Neural Architecture Search for Real-Time Detection Transformers},
      author={Isaac Robinson and Peter Robicheaux and Matvei Popov and Deva Ramanan and Neehar Peri},
      year={2026},
      eprint={2511.09554},
      archivePrefix={arXiv},
      primaryClass={cs.CV},
      url={https://huggingface.co/papers/2511.09554},
}
```

This model was originally contributed by stevenbucaille in 🤗 transformers.
