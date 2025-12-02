# tiny-cache

Tiny Cache Service that works as a sidecar, implementing a gRPC server with GET/SET/TTL/LRU functionality.
The cache uses an in-memory dictionary for storage.
LRU eviction policy is used when the cache reaches its maximum size.
TTL (Time To Live) is supported for each cache entry.

## Getting Started

```yaml
python3 -m venv venv
. ./venv/bin/activate
pip install -r requirements.txt
make gen
python server.py
```

```yaml
docker-compose build
docker-compose up
```
