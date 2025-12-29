from __future__ import annotations

import logging

import grpc

logger = logging.getLogger(__name__)

from tiny_cache.transport.active_requests import ActiveRequests


class ActiveRequestsInterceptor(grpc.aio.ServerInterceptor):
    def __init__(self, active_requests: ActiveRequests):
        self._active_requests = active_requests

    async def intercept_service(self, continuation, handler_call_details):
        rpc_method = getattr(handler_call_details, "method", "unknown")
        self._active_requests.increment()
        logger.debug("RPC started %s (active=%s)", rpc_method, self._active_requests.value)
        try:
            return await continuation(handler_call_details)
        finally:
            self._active_requests.decrement()
            logger.debug("RPC finished %s (active=%s)", rpc_method, self._active_requests.value)
