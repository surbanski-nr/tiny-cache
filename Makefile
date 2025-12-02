PROTOC=python -m grpc_tools.protoc

gen:
	$(PROTOC) -I. --python_out=. --grpc_python_out=. cache.proto
