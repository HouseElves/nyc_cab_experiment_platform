"""
Tests for :mod:`nyc_cab.exceptions`.

These tests lock the exception hierarchy and the keyword-argument interfaces on
the concrete leaves. Downstream modules depend on being able to catch
:class:`ConfigurationError` or :class:`ValidationError` without caring about
the specific subclass.
"""

from __future__ import annotations

import pytest

from nyc_cab.exceptions import (
    ConfigurationError,
    InvalidConfigError,
    InvalidRequestError,
    MissingConfigError,
    NYCCabError,
    ValidationError,
)

pytestmark = pytest.mark.unit


def test_nyc_cab_error_extends_builtin_exception() -> None:
    """The platform base class is a plain :class:`Exception` subclass."""
    assert issubclass(NYCCabError, Exception)


def test_configuration_error_extends_nyc_cab_error() -> None:
    """:class:`ConfigurationError` sits under the platform base."""
    assert issubclass(ConfigurationError, NYCCabError)


def test_validation_error_extends_nyc_cab_error() -> None:
    """:class:`ValidationError` sits under the platform base."""
    assert issubclass(ValidationError, NYCCabError)


def test_missing_config_error_extends_configuration_error() -> None:
    """:class:`MissingConfigError` is a :class:`ConfigurationError`."""
    assert issubclass(MissingConfigError, ConfigurationError)


def test_invalid_config_error_extends_configuration_error() -> None:
    """:class:`InvalidConfigError` is a :class:`ConfigurationError`."""
    assert issubclass(InvalidConfigError, ConfigurationError)


def test_invalid_request_error_extends_validation_error() -> None:
    """:class:`InvalidRequestError` is a :class:`ValidationError`."""
    assert issubclass(InvalidRequestError, ValidationError)


def test_missing_config_error_stores_variable_name() -> None:
    """The ``variable`` keyword argument is preserved on the instance."""
    error = MissingConfigError("not set", variable="NYC_CAB_DATA_ROOT")
    assert error.variable == "NYC_CAB_DATA_ROOT"
    assert str(error) == "not set"


def test_missing_config_error_defaults_variable_to_none() -> None:
    """Omitting ``variable`` leaves the attribute set to ``None``."""
    error = MissingConfigError("not set")
    assert error.variable is None


def test_invalid_config_error_stores_variable_and_value() -> None:
    """Both ``variable`` and ``value`` keyword arguments are preserved."""
    error = InvalidConfigError(
        "bad",
        variable="NYC_CAB_ENVIRONMENT",
        value="staging",
    )
    assert error.variable == "NYC_CAB_ENVIRONMENT"
    assert error.value == "staging"
    assert str(error) == "bad"


def test_invalid_config_error_defaults_variable_and_value_to_none() -> None:
    """Omitting the keyword arguments leaves both attributes set to ``None``."""
    error = InvalidConfigError("bad")
    assert error.variable is None
    assert error.value is None


def test_invalid_request_error_stores_violations() -> None:
    """The ``violations`` keyword argument is preserved as a tuple."""
    violations = (("year", 1850), ("month", 13))
    error = InvalidRequestError("invalid", violations=violations)
    assert error.violations == violations
    assert str(error) == "invalid"


def test_invalid_request_error_defaults_violations_to_empty_tuple() -> None:
    """Omitting ``violations`` leaves the attribute as an empty tuple."""
    error = InvalidRequestError("invalid")
    assert isinstance(error.violations, tuple)
    assert len(error.violations) == 0


def test_invalid_request_error_normalizes_violations_to_tuple() -> None:
    """A list of violations is converted to a tuple internally."""
    error = InvalidRequestError("invalid", violations=[("x", 1), ("y", 2)])
    assert error.violations == (("x", 1), ("y", 2))
    assert isinstance(error.violations, tuple)


def test_missing_config_error_caught_as_configuration_error() -> None:
    """Callers can catch ``MissingConfigError`` via the base config error."""
    with pytest.raises(ConfigurationError):
        raise MissingConfigError("not set", variable="X")


def test_invalid_config_error_caught_as_nyc_cab_error() -> None:
    """Callers can catch ``InvalidConfigError`` via the platform base."""
    with pytest.raises(NYCCabError):
        raise InvalidConfigError("bad", variable="X", value="y")


def test_invalid_request_error_caught_as_validation_error() -> None:
    """Callers can catch ``InvalidRequestError`` via the validation base."""
    with pytest.raises(ValidationError):
        raise InvalidRequestError("invalid", violations=(("x", 1),))


def test_invalid_request_error_caught_as_nyc_cab_error() -> None:
    """Callers can catch ``InvalidRequestError`` via the platform base."""
    with pytest.raises(NYCCabError):
        raise InvalidRequestError("invalid")
