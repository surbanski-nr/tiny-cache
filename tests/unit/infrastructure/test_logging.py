import json
import logging

import pytest

from tiny_cache.infrastructure.logging import (
    JsonFormatter,
    RequestIdFilter,
    configure_logging,
)
from tiny_cache.request_context import request_id_var

pytestmark = pytest.mark.unit


def test_request_id_filter_and_json_formatter_include_request_id() -> None:
    token = request_id_var.set("rid-123")
    try:
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="hello",
            args=(),
            exc_info=None,
        )

        request_id_filter = RequestIdFilter()
        assert request_id_filter.filter(record) is True
        assert getattr(record, "request_id") == "rid-123"

        formatter = JsonFormatter()
        payload = json.loads(formatter.format(record))
        assert payload["request_id"] == "rid-123"
        assert payload["message"] == "hello"
    finally:
        request_id_var.reset(token)


def test_configure_logging_switches_formatters() -> None:
    configure_logging("INFO", "text")
    root = logging.getLogger()
    assert root.handlers
    assert not isinstance(root.handlers[0].formatter, JsonFormatter)

    configure_logging("INFO", "json")
    root = logging.getLogger()
    assert root.handlers
    assert isinstance(root.handlers[0].formatter, JsonFormatter)
