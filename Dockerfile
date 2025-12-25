FROM python:3.11-slim as builder

WORKDIR /build

RUN apt-get update && apt-get install -y \
    make \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY cache.proto Makefile ./
COPY cache_store.py server.py config.py ./

RUN make gen

FROM python:3.11-slim as production

WORKDIR /app

RUN groupadd -r cache && useradd -r -g cache cache

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --from=builder /build/*.py ./
COPY --from=builder /build/cache_pb2.py ./
COPY --from=builder /build/cache_pb2_grpc.py ./

RUN chown -R cache:cache /app
USER cache

EXPOSE 50051 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

CMD ["python", "server.py"]
