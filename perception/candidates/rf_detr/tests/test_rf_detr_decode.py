from __future__ import annotations

from pathlib import Path
import sys
import unittest
import numpy as np

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from perception.candidates.rf_detr.decode import DecodeGeometry, decode_person, sigmoid


class SigmoidTests(unittest.TestCase):
    def test_extremes_are_stable(self):
        actual = sigmoid(np.array([-1000.0, 0.0, 1000.0], dtype=np.float32))
        np.testing.assert_allclose(actual, [0.0, 0.5, 1.0], rtol=0, atol=0)
        self.assertTrue(np.isfinite(actual).all())


class DecodeTests(unittest.TestCase):
    def setUp(self):
        self.logits = np.full((1, 300, 91), -20.0, dtype=np.float32)
        self.boxes = np.zeros((1, 300, 4), dtype=np.float32)
        self.boxes[:] = [0.5, 0.5, 0.1, 0.1]
        self.geometry = DecodeGeometry(1920, 1080)

    def test_person_is_sparse_coco_slot_one(self):
        self.logits[0, 7, 1] = 1.0
        self.logits[0, 8, 0] = 20.0  # "N/A" slot must be ignored
        self.logits[0, 9, 2:] = 20.0  # other classes must be ignored
        self.boxes[0, 7] = [0.5, 0.5, 0.2, 0.2]
        result = decode_person(self.logits, self.boxes, self.geometry, threshold=0.5)
        self.assertEqual(len(result), 1)
        detection = result[0]
        self.assertEqual(detection["query_index"], 7)
        self.assertEqual(detection["class_id"], 0)
        self.assertEqual(detection["coco_class_id"], 1)
        self.assertEqual(detection["label"], "person")
        np.testing.assert_allclose(detection["bbox_xyxy_pixels"], [768, 432, 1152, 648], rtol=0, atol=1e-5)

    def test_non_square_direct_resize_uses_independent_scales(self):
        self.logits[0, 0, 1] = 10.0
        self.boxes[0, 0] = [0.25, 0.25, 0.5, 0.5]
        result = decode_person(self.logits, self.boxes, DecodeGeometry(1000, 500), threshold=0.5)
        np.testing.assert_allclose(result[0]["bbox_xyxy_pixels"], [0, 0, 500, 250], rtol=0, atol=1e-6)

    def test_score_order_query_tie_and_top_k(self):
        self.logits[0, 9, 1] = 2.0
        self.logits[0, 4, 1] = 2.0
        self.logits[0, 2, 1] = 3.0
        result = decode_person(self.logits, self.boxes, self.geometry, threshold=0.5, top_k=2)
        self.assertEqual([item["query_index"] for item in result], [2, 4])

    def test_threshold_is_strict_like_hf_postprocessor(self):
        self.logits[0, 3, 1] = 0.0  # sigmoid == 0.5 exactly
        self.assertEqual(decode_person(self.logits, self.boxes, self.geometry, threshold=0.5), [])
        self.assertEqual(len(decode_person(self.logits, self.boxes, self.geometry, threshold=0.0)), 300)

    def test_no_nms_duplicate_boxes_remain(self):
        self.logits[0, 0, 1] = 4.0
        self.logits[0, 1, 1] = 3.0
        result = decode_person(self.logits, self.boxes, self.geometry, threshold=0.5)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["bbox_xyxy_pixels"], result[1]["bbox_xyxy_pixels"])

    def test_invalid_shapes_topk_and_nonfinite_fail(self):
        with self.assertRaises(ValueError):
            decode_person(np.zeros((1, 300, 80), np.float32), self.boxes, self.geometry)
        with self.assertRaises(ValueError):
            decode_person(self.logits, self.boxes, self.geometry, top_k=301)
        self.logits[0, 0, 1] = np.nan
        with self.assertRaises(ValueError):
            decode_person(self.logits, self.boxes, self.geometry)

    def test_box_is_clipped_to_source(self):
        self.logits[0, 0, 1] = 2.0
        self.boxes[0, 0] = [0.0, 0.0, 0.4, 0.4]
        result = decode_person(self.logits, self.boxes, DecodeGeometry(512, 512), threshold=0.5)
        np.testing.assert_allclose(result[0]["bbox_xyxy_pixels"], [0, 0, 102.4, 102.4], rtol=0, atol=1e-4)


if __name__ == "__main__":
    unittest.main()
