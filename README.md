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
python server.py
```

### Docker

```bash
docker-compose build
docker-compose up
```

## Configuration

Environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `CACHE_MAX_ITEMS` | 1000 | Maximum number of cache entries |
| `CACHE_MAX_MEMORY_MB` | 100 | Memory limit in megabytes |
| `CACHE_CLEANUP_INTERVAL` | 10 | TTL cleanup interval in seconds |
| `CACHE_PORT` | 50051 | gRPC server port |
| `CACHE_HOST` | `[::]` | Server bind address |

## API

The service implements the following gRPC methods:

- `Get(CacheKey) → CacheValue`: Retrieve cached value
- `Set(CacheItem) → CacheResponse`: Store value with optional TTL
- `Delete(CacheKey) → CacheResponse`: Remove cached entry
- `Stats(Empty) → CacheStats`: Get cache statistics

## Testing

```bash
# Activate environment
. ./venv/bin/activate

# Run tests with coverage
python run_tests.py --coverage
```

## License

MIT License - see [`LICENSE`](LICENSE) file for details.
