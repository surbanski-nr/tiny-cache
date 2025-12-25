FROM python:3.11-slim as builder

WORKDIR /build

COPY cache.proto ./
RUN pip install --no-cache-dir grpcio-tools>=1.76.0
RUN python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. cache.proto

FROM python:3.11-slim as production

WORKDIR /app

RUN groupadd -r cache && useradd -r -g cache cache

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY cache_store.py server.py config.py ./
COPY --from=builder /build/cache_pb2.py /build/cache_pb2_grpc.py ./

RUN chown -R cache:cache /app
USER cache

EXPOSE 50051 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import os, urllib.request; port=os.getenv('CACHE_HEALTH_PORT','8080'); urllib.request.urlopen('http://localhost:%s/health' % port)"

CMD ["python", "server.py"]
