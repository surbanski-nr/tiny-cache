from unittest.mock import patch

import pytest

from tiny_cache.infrastructure.config import get_env_bool, get_env_int, load_settings

pytestmark = pytest.mark.unit


def test_get_env_int_validation() -> None:
    with patch.dict("os.environ", {"CACHE_MAX_ITEMS": "not-an-int"}):
        with pytest.raises(ValueError, match="CACHE_MAX_ITEMS must be an integer"):
            get_env_int("CACHE_MAX_ITEMS", 1000)

    with patch.dict("os.environ", {"CACHE_MAX_ITEMS": "0"}):
        with pytest.raises(ValueError, match="CACHE_MAX_ITEMS must be >= 1"):
            get_env_int("CACHE_MAX_ITEMS", 1000, min_value=1)


def test_get_env_bool_validation() -> None:
    with patch.dict("os.environ", {"CACHE_TLS_ENABLED": "not-a-bool"}):
        with pytest.raises(ValueError, match="CACHE_TLS_ENABLED must be a boolean"):
            get_env_bool("CACHE_TLS_ENABLED", False)

    with patch.dict("os.environ", {"CACHE_TLS_ENABLED": "true"}):
        assert get_env_bool("CACHE_TLS_ENABLED", False) is True

    with patch.dict("os.environ", {"CACHE_TLS_ENABLED": "0"}):
        assert get_env_bool("CACHE_TLS_ENABLED", True) is False


def test_load_settings_rejects_invalid_choices() -> None:
    with patch.dict("os.environ", {"CACHE_LOG_FORMAT": "xml"}):
        with pytest.raises(ValueError, match="CACHE_LOG_FORMAT must be one of"):
            load_settings()

    with patch.dict("os.environ", {"CACHE_LOG_LEVEL": "TRACE"}):
        with pytest.raises(ValueError, match="CACHE_LOG_LEVEL must be one of"):
            load_settings()

    with patch.dict("os.environ", {"CACHE_BACKEND": "redis"}):
        with pytest.raises(ValueError, match="CACHE_BACKEND must be one of"):
            load_settings()


def test_load_settings_parses_max_value_bytes() -> None:
    with patch.dict("os.environ", {"CACHE_MAX_MEMORY_MB": "1"}, clear=True):
        settings = load_settings()
        assert settings.max_value_bytes == 1024 * 1024

    with patch.dict(
        "os.environ",
        {"CACHE_MAX_MEMORY_MB": "1", "CACHE_MAX_VALUE_BYTES": "100"},
        clear=True,
    ):
        settings = load_settings()
        assert settings.max_value_bytes == 100

    with patch.dict(
        "os.environ",
        {"CACHE_MAX_MEMORY_MB": "1", "CACHE_MAX_VALUE_BYTES": str(1024 * 1024 + 1)},
        clear=True,
    ):
        with pytest.raises(ValueError, match="CACHE_MAX_VALUE_BYTES must be <= "):
            load_settings()
