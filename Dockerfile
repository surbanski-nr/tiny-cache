FROM python:3.11-slim AS proto-builder

WORKDIR /build

ENV UV_NO_MODIFY_PATH=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

COPY cache.proto ./
RUN uv pip install --system "grpcio-tools==1.76.0"
RUN python -m grpc_tools.protoc -I. --python_out=. --pyi_out=. --grpc_python_out=. cache.proto

FROM python:3.11-slim AS deps

WORKDIR /app

ENV UV_NO_MODIFY_PATH=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.11-slim AS production

WORKDIR /app

RUN groupadd -r cache && useradd -r -g cache cache

COPY --from=deps /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

COPY tiny_cache ./tiny_cache
COPY --from=proto-builder /build/cache_pb2.py /build/cache_pb2_grpc.py /build/cache_pb2.pyi ./

RUN chown -R cache:cache /app
USER cache

EXPOSE 50051 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import os, urllib.request; port=os.getenv('CACHE_HEALTH_PORT','8080'); urllib.request.urlopen('http://localhost:%s/health' % port)"

CMD ["python", "-m", "tiny_cache"]
