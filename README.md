# tiny-cache

A lightweight gRPC cache service designed to work as a sidecar. Provides in-memory caching with LRU eviction and TTL support.

## Features

- **gRPC API**: Get, Set, Delete, and Stats operations
- **LRU Eviction**: Automatic cleanup when cache reaches maximum size
- **TTL Support**: Time-based expiration for cache entries
- **Memory Management**: Configurable memory limits and cleanup intervals
- **Statistics**: Cache hit/miss metrics and size monitoring

## Quick Start

### Local Development

Local development is tested with Python 3.11 (matching the Docker image). See `.python-version`.

```bash
# Setup environment
python3 -m venv venv
. ./venv/bin/activate
pip install -r requirements.txt

# Generate protobuf files
make gen

# Start server
python -m tiny_cache
```

### Docker

```bash
docker-compose up -d --build

# Default ports:
# - gRPC: 127.0.0.1:50051
# - HTTP health/metrics: 127.0.0.1:58080 (container listens on 8080)
curl -fsS "http://127.0.0.1:${CACHE_HEALTH_PORT_HOST:-58080}/health"
```

## Configuration

Environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `CACHE_MAX_ITEMS` | 1000 | Maximum number of cache entries |
| `CACHE_MAX_MEMORY_MB` | 100 | Memory limit in megabytes |
| `CACHE_MAX_VALUE_BYTES` | `CACHE_MAX_MEMORY_MB * 1024 * 1024` | Maximum per-entry value size |
| `CACHE_CLEANUP_INTERVAL` | 10 | TTL cleanup interval in seconds |
| `CACHE_PORT` | 50051 | gRPC server port |
| `CACHE_HOST` | `[::]` | Server bind address |
| `CACHE_HEALTH_HOST` | `0.0.0.0` | Health/metrics bind address |
| `CACHE_HEALTH_PORT` | 8080 | Health/metrics HTTP port |
| `CACHE_LOG_LEVEL` | `INFO` | Log level |
| `CACHE_LOG_FORMAT` | `text` | Log format (`text` or `json`) |
| `CACHE_TLS_ENABLED` | `false` | Enable gRPC TLS |
| `CACHE_TLS_CERT_PATH` | (unset) | TLS certificate path |
| `CACHE_TLS_KEY_PATH` | (unset) | TLS private key path |
| `CACHE_TLS_REQUIRE_CLIENT_AUTH` | `false` | Require client certificates (mTLS) |
| `CACHE_TLS_CLIENT_CA_PATH` | (unset) | Client CA bundle for mTLS |

## API

The service implements the following gRPC methods:

- `Get(CacheKey) → CacheValue`: Retrieve cached value
- `Set(CacheItem) → CacheResponse`: Store value with optional TTL
- `Delete(CacheKey) → CacheResponse`: Remove cached entry
- `Stats(Empty) → CacheStats`: Get cache statistics

## CLI (Optional)

The repo includes a small CLI client for quick manual testing:

```bash
make gen
python -m tiny_cache.cli --target 127.0.0.1:50051 set greeting hello --ttl 60
python -m tiny_cache.cli --target 127.0.0.1:50051 get greeting --format utf8
python -m tiny_cache.cli --target 127.0.0.1:50051 stats
```

## Testing

```bash
# Activate environment
. ./venv/bin/activate

# Install dev dependencies (includes pytest)
pip install -r requirements-dev.txt

# Run tests with coverage
pytest --cov=tiny_cache --cov-report=term-missing
```

## License

MIT License - see [`LICENSE`](LICENSE) file for details.
