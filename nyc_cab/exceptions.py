"""Exception hierarchy for the NYC Cab Experiment Platform.

The hierarchy is deliberately shallow at this stage. :class:`NYCCabError` is
the platform-wide base; :class:`ConfigurationError` covers anything that goes
wrong while loading or validating runtime configuration, with
:class:`MissingConfigError` and :class:`InvalidConfigError` as the two concrete
failure modes currently raised.

Further branches (ingestion, Spark session, schema, quality) will land with
the modules that actually raise them. No speculative placeholders live here.
"""

from __future__ import annotations
from collections.abc import Sequence
from typing import Any


# pylint: disable=too-few-public-methods
class NYCCabError(Exception):
    """Base class for every exception raised by the NYC Cab platform."""


# pylint: disable=too-few-public-methods
class ConfigurationError(NYCCabError):
    """Raised when runtime configuration fails to load or validate."""


class MissingConfigError(ConfigurationError):
    """Raised when a required environment variable is absent or empty.

    The ``variable`` attribute names the missing environment variable so
    callers can produce precise error messages without re-parsing the text.
    """

    def __init__(self, message: str, *, variable: str | None = None) -> None:
        """Initialize the error with a message and the missing variable name.

        Args:
            message: Human-readable description of the failure.
            variable: Name of the environment variable that was absent.
        """
        super().__init__(message)
        self.variable: str | None = variable


class InvalidConfigError(ConfigurationError):
    """
    Raised when a configuration value is present but malformed.

    The ``variable`` attribute names the offending environment variable; the
    ``value`` attribute captures the raw string that failed validation.
    """

    def __init__(self, message: str, *, variable: str | None = None, value: str | None = None) -> None:
        """Initialize the error with a message and offending variable.

        Args:
            message: Human-readable description of the failure.
            variable: Name of the environment variable whose value failed.
            value: The raw string value that failed validation.
        """
        super().__init__(message)
        self.variable: str | None = variable
        self.value: str | None = value


class ValidationError(NYCCabError):
    """Raised when typed application data fails validation."""


class InvalidRequestError(ValidationError):
    """
    Raised when a typed request object fails validation.

    The ``violations`` attribute records offending member names and values.
    """

    def __init__(self, message: str, *, violations: Sequence[tuple[str, Any]] | None = None) -> None:
        """
        Initialize the error with a message and optional violation details.

        Args:
            message: Human-readable description of the failure.
            violations: Pairs of member names and rejected values.
        """
        super().__init__(message)
        self.violations = tuple(violations or ())
