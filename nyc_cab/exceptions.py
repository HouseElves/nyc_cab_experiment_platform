"""Exception hierarchy for the NYC Cab Experiment Platform.

The hierarchy is deliberately shallow at this stage. :class:`NYCCabError` is
the platform-wide base; :class:`ConfigurationError` covers anything that goes
wrong while loading or validating runtime configuration, with
:class:`MissingConfigError` and :class:`InvalidConfigError` as the two concrete
failure modes currently raised.

Further branches (ingestion, Spark session, schema, quality) will land with
the modules that actually raise them. No speculative placeholders live here.

Note on the ``too-few-public-methods`` suppressions below: :class:`NYCCabError`
and :class:`ConfigurationError` are intentionally bare. Their only role is to
serve as ``except`` targets for callers that want to catch a category of
failure without caring about the specific subclass. Adding synthetic methods
to satisfy the linter would obscure that intent.
"""

from __future__ import annotations


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
        """Initialise the error with a message and the missing variable name.

        Args:
            message: Human-readable description of the failure.
            variable: Name of the environment variable that was absent.
        """
        super().__init__(message)
        self.variable: str | None = variable


class InvalidConfigError(ConfigurationError):
    """Raised when a configuration value is present but malformed.

    The ``variable`` attribute names the offending environment variable; the
    ``value`` attribute captures the raw string that failed validation.
    """

    def __init__(
        self,
        message: str,
        *,
        variable: str | None = None,
        value: str | None = None,
    ) -> None:
        """Initialise the error with a message and offending variable.

        Args:
            message: Human-readable description of the failure.
            variable: Name of the environment variable whose value failed.
            value: The raw string value that failed validation.
        """
        super().__init__(message)
        self.variable: str | None = variable
        self.value: str | None = value
