import pytest

from tiny_cache.domain.validation import (
    validate_key,
    validate_namespace,
    validate_value,
)

pytestmark = pytest.mark.unit


def test_validate_key_rejects_non_string() -> None:
    with pytest.raises(TypeError, match="Key must be a string"):
        validate_key(123)  # type: ignore[arg-type]


def test_validate_value_rejects_non_bytes() -> None:
    with pytest.raises(TypeError, match="Cache value must be bytes"):
        validate_value("value")  # type: ignore[arg-type]


def test_validate_namespace_normalizes_and_rejects_invalid_values() -> None:
    assert validate_namespace(None) is None
    assert validate_namespace("  team-a  ") == "team-a"
    assert validate_namespace("   ") is None

    with pytest.raises(ValueError, match="Namespace must contain only"):
        validate_namespace("team/a")
