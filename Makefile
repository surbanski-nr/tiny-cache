PROTOC=python-grpc-tools-protoc

gen:
	$(PROTOC) -I. --python_out=. --grpc_python_out=. cache.proto

clean:
	rm -f cache_pb2.py cache_pb2_grpc.py

.PHONY: gen clean
