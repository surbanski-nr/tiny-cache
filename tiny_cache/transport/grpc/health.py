from __future__ import annotations

import grpc
from grpc_health.v1 import health as grpc_health
from grpc_health.v1 import health_pb2, health_pb2_grpc


def add_grpc_health_service(server: grpc.aio.Server) -> grpc_health.HealthServicer:
    servicer = grpc_health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(servicer, server)

    servicer.set("", health_pb2.HealthCheckResponse.SERVING)
    servicer.set("cache.CacheService", health_pb2.HealthCheckResponse.SERVING)
    return servicer
