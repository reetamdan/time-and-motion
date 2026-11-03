#include "nvdsinfer_custom_impl.h"

#include <cmath>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>

extern "C" bool NvDsInferParseCustomRtDetrPerson(
    const std::vector<NvDsInferLayerInfo>&, const NvDsInferNetworkInfo&,
    const NvDsInferParseDetectionParams&, std::vector<NvDsInferObjectDetectionInfo>&);

namespace {
NvDsInferLayerInfo layer(const char* name, NvDsInferDims dims, void* buffer) {
  NvDsInferLayerInfo info{};
  info.dataType = FLOAT;
  info.inferDims = dims;
  info.layerName = name;
  info.buffer = buffer;
  info.isInput = 0;
  return info;
}

NvDsInferDims dims(unsigned int num_dims, unsigned int d0, unsigned int d1,
                   unsigned int d2, unsigned int elements) {
  NvDsInferDims value{};
  value.numDims = num_dims;
  value.d[0] = d0;
  value.d[1] = d1;
  value.d[2] = d2;
  value.numElements = elements;
  return value;
}

bool close(float left, float right, float tolerance = 1e-4f) {
  return std::fabs(left - right) <= tolerance;
}

int require(bool condition, const std::string& message) {
  if (!condition) {
    std::cerr << "FAIL: " << message << std::endl;
    return 1;
  }
  return 0;
}
}  // namespace

int main() {
  std::vector<float> logits(300 * 80, -20.0f);
  std::vector<float> boxes(300 * 4, 0.0f);
  for (unsigned int query = 0; query < 300; ++query) {
    boxes[query * 4] = 0.5f;
    boxes[query * 4 + 1] = 0.5f;
    boxes[query * 4 + 2] = 0.1f;
    boxes[query * 4 + 3] = 0.1f;
  }
  NvDsInferNetworkInfo network{640, 640, 3};
  NvDsInferParseDetectionParams params{};
  params.numClassesConfigured = 1;
  params.perClassPreclusterThreshold.push_back(0.5f);

  logits[9 * 80] = 2.0f;
  logits[4 * 80] = 2.0f;
  logits[2 * 80] = 3.0f;
  logits[8 * 80 + 1] = 20.0f;  // Non-person classes must be ignored.
  // Deliberately reverse output order: parser must locate tensors by name.
  std::vector<NvDsInferLayerInfo> layers = {
      layer("pred_boxes", dims(3, 1, 300, 4, 1200), boxes.data()),
      layer("logits", dims(3, 1, 300, 80, 24000), logits.data())};
  std::vector<NvDsInferObjectDetectionInfo> objects;
  if (require(NvDsInferParseCustomRtDetrPerson(layers, network, params, objects), "valid parse")) return 1;
  if (require(objects.size() == 3, "person-only threshold count")) return 1;
  if (require(objects[0].detectionConfidence > objects[1].detectionConfidence &&
              close(objects[1].detectionConfidence, objects[2].detectionConfidence),
              "descending score order")) return 1;
  // Equal-score queries 4 and 9 retain ascending query order; boxes identify queries.
  boxes[4 * 4] = 0.25f;
  boxes[9 * 4] = 0.75f;
  objects.clear();
  if (require(NvDsInferParseCustomRtDetrPerson(layers, network, params, objects), "tie parse")) return 1;
  if (require(close(objects[1].left, 128.0f) && close(objects[2].left, 448.0f),
              "ascending query tie order and cxcywh conversion")) return 1;
  for (const auto& object : objects) {
    if (require(object.classId == 0 && object.width > 0 && object.height > 0,
                "app class and valid boxes")) return 1;
  }

  // Duplicate geometry is not suppressed: DETR must not receive NMS here.
  logits[2 * 80] = -20.0f;
  logits[4 * 80] = -20.0f;
  objects.clear();
  if (require(NvDsInferParseCustomRtDetrPerson(layers, network, params, objects), "duplicate parse")) return 1;
  if (require(objects.size() == 1, "remaining single query")) return 1;
  logits[0] = 4.0f;
  boxes[0] = boxes[9 * 4];
  boxes[1] = boxes[9 * 4 + 1];
  boxes[2] = boxes[9 * 4 + 2];
  boxes[3] = boxes[9 * 4 + 3];
  objects.clear();
  if (require(NvDsInferParseCustomRtDetrPerson(layers, network, params, objects), "NMS check parse")) return 1;
  if (require(objects.size() == 2, "no NMS duplicate suppression")) return 1;

  // Unit batch may be removed by nvinfer; accept [300,80]/[300,4] as well.
  layers[0].inferDims = dims(2, 300, 4, 0, 1200);
  layers[1].inferDims = dims(2, 300, 80, 0, 24000);
  objects.clear();
  if (require(NvDsInferParseCustomRtDetrPerson(layers, network, params, objects), "batchess dims")) return 1;

  // The parser's built-in bound is exactly 300, never more than the query count.
  for (unsigned int query = 0; query < 300; ++query) logits[query * 80] = 0.0f;
  params.perClassPreclusterThreshold[0] = 0.0f;
  objects.clear();
  if (require(NvDsInferParseCustomRtDetrPerson(layers, network, params, objects), "top-k parse")) return 1;
  if (require(objects.size() == 300, "bounded top-k <=300")) return 1;

  layers[1].inferDims.numElements = 79;
  objects.clear();
  if (require(!NvDsInferParseCustomRtDetrPerson(layers, network, params, objects), "invalid shape rejection")) return 1;
  std::cout << "PASS: parser person slot, geometry, order, top-k, no-NMS, dimensions and class mapping" << std::endl;
  return 0;
}
