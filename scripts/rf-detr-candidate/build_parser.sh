#!/usr/bin/env bash
# Inside the pinned DeepStream container: build + unit-test the RF-DETR person parser.
set -Eeuo pipefail
cd /workspace/perception/candidates/rf_detr/parser
make clean >/dev/null 2>&1 || true
make all
./parser_test
nm -D --defined-only libnvds_infercustomparser_rfdetr.so | grep NvDsInferParseCustomRfDetrPerson
sha256sum nvdsinfer_customparser_rfdetr.cpp libnvds_infercustomparser_rfdetr.so parser_test.cpp
