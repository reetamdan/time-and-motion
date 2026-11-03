NVDS_VERSION := 9.2
CXX := g++
CPPFLAGS := -I/opt/nvidia/deepstream/deepstream-$(NVDS_VERSION)/includes -I/usr/local/cuda/include
CXXFLAGS := -std=c++17 -O2 -Wall -Wextra -Werror -Wno-error=missing-field-initializers -fPIC
LIB := libnvds_infercustomparser_rtdetr.so
TEST := parser_test

.PHONY: all test
all: $(LIB) $(TEST)

$(LIB): nvdsinfer_customparser_rtdetr.cpp
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -shared -o $@ $<

$(TEST): parser_test.cpp $(LIB)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -o $@ parser_test.cpp -L. -lnvds_infercustomparser_rtdetr -Wl,-rpath,'$$ORIGIN'

test: all
	./$(TEST)
