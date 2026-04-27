"""Tests for :mod:`nyc_cab._env`.

These tests provide direct module-level coverage for the small environment
mapping helpers shared by the runtime configuration loaders. The helpers are
also exercised indirectly by :mod:`nyc_cab.test.config` and
:mod:`nyc_cab.test.spark_config`, but those tests are authoritative for the
loaders rather than for these primitives.
"""

from __future__ import annotations

import pytest

from nyc_cab import _env
from nyc_cab.exceptions import MissingConfigError


def test_optional_returns_value_when_present() -> None:
    """A populated value passes through the optional helper."""
    assert _env.optional({"X": "value"}, "X", "default") == "value"


def test_optional_returns_default_when_absent() -> None:
    """An absent variable resolves to the supplied default."""
    assert _env.optional({}, "X", "default") == "default"


def test_optional_returns_default_when_value_is_blank() -> None:
    """An empty-string value resolves to the supplied default."""
    assert _env.optional({"X": ""}, "X", "default") == "default"


def test_optional_returns_default_when_value_is_whitespace() -> None:
    """A whitespace-only value resolves to the supplied default."""
    assert _env.optional({"X": "   "}, "X", "default") == "default"


def test_optional_strips_surrounding_whitespace() -> None:
    """Surrounding whitespace is removed from a populated optional value."""
    assert _env.optional({"X": "  value  "}, "X", "default") == "value"


def test_require_returns_value_when_present() -> None:
    """A populated value passes through the require helper."""
    assert _env.require({"X": "value"}, "X") == "value"


def test_require_strips_surrounding_whitespace() -> None:
    """Surrounding whitespace is removed from a populated required value."""
    assert _env.require({"X": "  value  "}, "X") == "value"


def test_require_raises_when_absent() -> None:
    """An absent variable raises :class:`MissingConfigError`."""
    with pytest.raises(MissingConfigError) as error_info:
        _env.require({}, "X")
    assert error_info.value.variable == "X"


def test_require_raises_when_value_is_blank() -> None:
    """An empty-string value raises :class:`MissingConfigError`."""
    with pytest.raises(MissingConfigError) as error_info:
        _env.require({"X": ""}, "X")
    assert error_info.value.variable == "X"


def test_require_raises_when_value_is_whitespace() -> None:
    """A whitespace-only value raises :class:`MissingConfigError`."""
    with pytest.raises(MissingConfigError) as error_info:
        _env.require({"X": "   "}, "X")
    assert error_info.value.variable == "X"
