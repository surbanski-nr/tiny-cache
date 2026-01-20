PYTHON ?= python
PROTOC = $(PYTHON) -m grpc_tools.protoc

gen:
	$(PROTOC) -I. --python_out=. --grpc_python_out=. cache.proto

clean:
	rm -f cache_pb2.py cache_pb2_grpc.py

test: gen
	$(PYTHON) -m pytest

.PHONY: gen clean test
