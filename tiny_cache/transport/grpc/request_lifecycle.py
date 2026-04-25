from __future__ import annotations

import logging
import time
import uuid
from contextvars import Token
from dataclasses import dataclass

from grpc import StatusCode

from tiny_cache.domain.validation import validate_namespace
from tiny_cache.request_context import request_id_var
from tiny_cache.transport.grpc.contracts import RpcContextLike

NAMESPACE_HEADER = "x-cache-namespace"


@dataclass(frozen=True, slots=True)
class RpcRequestState:
    request_id: str
    token: Token[str] | None
    client_addr: str
    start_time: float

    def duration_ms(self) -> float:
        return (time.monotonic() - self.start_time) * 1000


class GrpcRequestLifecycle:
    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def begin_request(self, context: RpcContextLike) -> RpcRequestState:
        request_id, token = self._ensure_request_id()
        return RpcRequestState(
            request_id=request_id,
            token=token,
            client_addr=self._get_client_address(context),
            start_time=time.monotonic(),
        )

    def finish_request(self, state: RpcRequestState) -> None:
        if state.token is not None:
            request_id_var.reset(state.token)

    def namespace_from_context(self, context: RpcContextLike) -> str | None:
        try:
            for key, value in context.invocation_metadata() or ():
                if key.lower() == NAMESPACE_HEADER and value:
                    return validate_namespace(value)
        except ValueError:
            raise
        except Exception:
            self._logger.debug("Unable to read namespace from metadata", exc_info=True)
        return None

    def log_request(
        self,
        operation: str,
        key: str,
        client_addr: str,
        duration_ms: float,
        result: str,
    ) -> None:
        if not self._logger.isEnabledFor(logging.DEBUG):
            return
        self._logger.debug(
            "%s key=%r client=%s duration_ms=%.2f result=%s",
            operation,
            key,
            client_addr,
            duration_ms,
            result,
        )

    def handle_invalid_argument(
        self,
        context: RpcContextLike,
        state: RpcRequestState,
        operation: str,
        key: str,
        exc: ValueError,
    ) -> None:
        self.log_request(
            operation,
            key,
            state.client_addr,
            state.duration_ms(),
            "INVALID_ARGUMENT",
        )
        context.set_code(StatusCode.INVALID_ARGUMENT)
        context.set_details(str(exc))

    def handle_internal_error(
        self,
        context: RpcContextLike,
        state: RpcRequestState,
        operation: str,
        key: str,
        log_message: str,
    ) -> None:
        self.log_request(
            operation,
            key,
            state.client_addr,
            state.duration_ms(),
            "ERROR",
        )
        self._logger.exception(log_message, key)
        context.set_code(StatusCode.INTERNAL)
        context.set_details(self.internal_error_details(state))

    def internal_error_details(self, state: RpcRequestState) -> str:
        return f"Internal server error (request_id={state.request_id})"

    def _ensure_request_id(self) -> tuple[str, Token[str] | None]:
        request_id = request_id_var.get()
        if request_id != "-":
            return request_id, None

        request_id = uuid.uuid4().hex
        return request_id, request_id_var.set(request_id)

    def _get_client_address(self, context: RpcContextLike) -> str:
        try:
            peer = context.peer()
            return peer if peer else "unknown"
        except Exception:
            self._logger.debug("Unable to read client peer from context", exc_info=True)
            return "unknown"
