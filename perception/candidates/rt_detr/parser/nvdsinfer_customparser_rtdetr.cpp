// RT-DETR candidate person parser for DeepStream 9.2. Not used by selected runtime.
// Raw contract: logits [1,300,80], pred_boxes [1,300,4], COCO person slot 0.
#include "nvdsinfer_custom_impl.h"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>

namespace {
constexpr unsigned int kQueries = 300;
constexpr unsigned int kCocoClasses = 80;
constexpr unsigned int kPersonSlot = 0;
constexpr unsigned int kAppPersonClass = 0;
constexpr std::size_t kBoundedTopK = 300;

struct Candidate {
  unsigned int query;
  float score;
  NvDsInferObjectDetectionInfo object;
};

float sigmoid(float value) {
  if (value >= 0.0f) {
    return 1.0f / (1.0f + std::exp(-value));
  }
  const float exponential = std::exp(value);
  return exponential / (1.0f + exponential);
}

float clip(float value, float low, float high) {
  return std::max(low, std::min(value, high));
}

bool valid_dimensions(const NvDsInferLayerInfo& layer, unsigned int trailing) {
  const NvDsInferDims& dims = layer.inferDims;
  if (dims.numElements != kQueries * trailing) return false;
  if (dims.numDims == 3) {
    return dims.d[0] == 1 && dims.d[1] == kQueries && dims.d[2] == trailing;
  }
  // Gst-nvinfer may expose full dimensions without the unit batch dimension.
  if (dims.numDims == 2) {
    return dims.d[0] == kQueries && dims.d[1] == trailing;
  }
  return false;
}

const NvDsInferLayerInfo* find_layer(
    const std::vector<NvDsInferLayerInfo>& layers, const char* name,
    unsigned int trailing) {
  const NvDsInferLayerInfo* found = nullptr;
  for (const auto& layer : layers) {
    if (layer.layerName && std::strcmp(layer.layerName, name) == 0) {
      if (found) {
        std::cerr << "RT-DETR person parser: duplicate layer " << name << std::endl;
        return nullptr;
      }
      found = &layer;
    }
  }
  if (!found) {
    std::cerr << "RT-DETR person parser: missing layer " << name << std::endl;
    return nullptr;
  }
  if (found->dataType != FLOAT || !found->buffer || !valid_dimensions(*found, trailing)) {
    std::cerr << "RT-DETR person parser: invalid type/buffer/dimensions for " << name
              << " numDims=" << found->inferDims.numDims
              << " numElements=" << found->inferDims.numElements << std::endl;
    return nullptr;
  }
  return found;
}
}  // namespace

extern "C" bool NvDsInferParseCustomRtDetrPerson(
    const std::vector<NvDsInferLayerInfo>& outputLayersInfo,
    const NvDsInferNetworkInfo& networkInfo,
    const NvDsInferParseDetectionParams& detectionParams,
    std::vector<NvDsInferObjectDetectionInfo>& objectList) {
  if (networkInfo.width != 640 || networkInfo.height != 640 || networkInfo.channels != 3) {
    std::cerr << "RT-DETR person parser: expected network 3x640x640" << std::endl;
    return false;
  }
  if (detectionParams.numClassesConfigured != 1 ||
      detectionParams.perClassPreclusterThreshold.size() != 1) {
    std::cerr << "RT-DETR person parser: expected one configured app class/threshold" << std::endl;
    return false;
  }
  const float threshold = detectionParams.perClassPreclusterThreshold[kAppPersonClass];
  if (!std::isfinite(threshold) || threshold < 0.0f || threshold > 1.0f) {
    std::cerr << "RT-DETR person parser: invalid threshold" << std::endl;
    return false;
  }
  const NvDsInferLayerInfo* logits_layer = find_layer(outputLayersInfo, "logits", kCocoClasses);
  const NvDsInferLayerInfo* boxes_layer = find_layer(outputLayersInfo, "pred_boxes", 4);
  if (!logits_layer || !boxes_layer) return false;
  const float* logits = static_cast<const float*>(logits_layer->buffer);
  const float* boxes = static_cast<const float*>(boxes_layer->buffer);

  std::vector<Candidate> candidates;
  candidates.reserve(kQueries);
  for (unsigned int query = 0; query < kQueries; ++query) {
    const float logit = logits[query * kCocoClasses + kPersonSlot];
    if (!std::isfinite(logit)) {
      std::cerr << "RT-DETR person parser: non-finite person logit" << std::endl;
      return false;
    }
    const float score = sigmoid(logit);
    if (score < threshold) continue;
    NvDsInferObjectDetectionInfo object{};
    object.classId = kAppPersonClass;
    object.detectionConfidence = score;
    const unsigned int offset = query * 4;
    const float cx = boxes[offset];
    const float cy = boxes[offset + 1];
    const float width = boxes[offset + 2];
    const float height = boxes[offset + 3];
    if (!std::isfinite(cx) || !std::isfinite(cy) || !std::isfinite(width) || !std::isfinite(height)) {
      std::cerr << "RT-DETR person parser: non-finite box" << std::endl;
      return false;
    }
    const float x1 = clip((cx - width / 2.0f) * networkInfo.width, 0.0f, networkInfo.width);
    const float y1 = clip((cy - height / 2.0f) * networkInfo.height, 0.0f, networkInfo.height);
    const float x2 = clip((cx + width / 2.0f) * networkInfo.width, 0.0f, networkInfo.width);
    const float y2 = clip((cy + height / 2.0f) * networkInfo.height, 0.0f, networkInfo.height);
    if (x2 <= x1 || y2 <= y1) continue;
    object.left = x1;
    object.top = y1;
    object.width = x2 - x1;
    object.height = y2 - y1;
    candidates.push_back(Candidate{query, score, object});
  }
  std::sort(candidates.begin(), candidates.end(), [](const Candidate& left, const Candidate& right) {
    if (left.score != right.score) return left.score > right.score;
    return left.query < right.query;
  });
  if (candidates.size() > kBoundedTopK) candidates.resize(kBoundedTopK);
  objectList.reserve(objectList.size() + candidates.size());
  for (const auto& candidate : candidates) objectList.push_back(candidate.object);
  return true;  // No NMS: configure DeepStream cluster-mode=4.
}
