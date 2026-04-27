"""Runtime configuration for the NYC Cab Experiment Platform.

This module defines stable, application-wide runtime configuration derived from
an explicit environment mapping. It owns deployment environment selection,
filesystem layout, and application log level.

Validation that depends on external state belongs at the point of use rather
than here. For example, this module does not verify that configured paths exist
on disk.
"""

from __future__ import annotations

import enum
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from nyc_cab import _env
from nyc_cab.constants import (
    BRONZE_LAYER,
    DEFAULT_DATA_ROOT_LOCAL,
    DEFAULT_LOG_LEVEL,
    ENV_VAR_DATA_ROOT,
    ENV_VAR_ENVIRONMENT,
    ENV_VAR_LOG_LEVEL,
    GOLD_LAYER,
    SILVER_LAYER,
    VALID_LOG_LEVELS,
)
from nyc_cab.exceptions import InvalidConfigError


class Environment(enum.Enum):
    """Deployment environment discriminator."""

    LOCAL = "local"
    DEV = "dev"
    PROD = "prod"


_VALID_ENVIRONMENT_VALUES: Final[tuple[str, ...]] = tuple(
    item.value for item in Environment
)


@dataclass(frozen=True, slots=True)
class PathsConfig:
    """Filesystem layout rooted at a single data directory."""

    data_root: Path

    @property
    def bronze(self) -> Path:
        """Return the Bronze layer directory path."""
        return self.data_root / BRONZE_LAYER

    @property
    def silver(self) -> Path:
        """Return the Silver layer directory path."""
        return self.data_root / SILVER_LAYER

    @property
    def gold(self) -> Path:
        """Return the Gold layer directory path."""
        return self.data_root / GOLD_LAYER


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Top-level immutable runtime configuration."""

    environment: Environment
    paths: PathsConfig
    log_level: str


def load_config(environ: Mapping[str, str] | None = None) -> RuntimeConfig:
    """Load, validate, and freeze runtime configuration.

    Args:
        environ: Mapping of environment variable names to values. When omitted,
            ``os.environ`` supplies the values. Tests should prefer an explicit
            mapping over mutating process state.

    Returns:
        A fully populated :class:`RuntimeConfig` instance.

    Raises:
        MissingConfigError: A required variable is absent or empty.
        InvalidConfigError: A variable is present but malformed.
    """
    source: Mapping[str, str] = os.environ if environ is None else environ
    environment = _parse_environment(
        _env.optional(source, ENV_VAR_ENVIRONMENT, Environment.LOCAL.value)
    )
    paths = _build_paths_config(source, environment)
    log_level = _parse_log_level(
        _env.optional(source, ENV_VAR_LOG_LEVEL, DEFAULT_LOG_LEVEL)
    )
    return RuntimeConfig(environment=environment, paths=paths, log_level=log_level)


def _build_paths_config(
    source: Mapping[str, str],
    environment: Environment,
) -> PathsConfig:
    """Build a :class:`PathsConfig` from the supplied environment mapping.

    ``NYC_CAB_DATA_ROOT`` is required in every environment other than
    :attr:`Environment.LOCAL`, where ``./data`` serves as the default.
    """
    if environment is Environment.LOCAL:
        raw = _env.optional(source, ENV_VAR_DATA_ROOT, DEFAULT_DATA_ROOT_LOCAL)
    else:
        raw = _env.require(source, ENV_VAR_DATA_ROOT)
    return PathsConfig(data_root=_parse_path(raw))


def _parse_environment(value: str) -> Environment:
    """Convert a raw string into an :class:`Environment` member."""
    try:
        return Environment(value.lower())
    except ValueError as exc:
        valid = ", ".join(_VALID_ENVIRONMENT_VALUES)
        raise InvalidConfigError(
            f"Invalid environment '{value}'. Expected one of: {valid}.",
            variable=ENV_VAR_ENVIRONMENT,
            value=value,
        ) from exc


def _parse_path(value: str) -> Path:
    """Convert a raw string into an absolute, user-expanded path."""
    return Path(value).expanduser().resolve()


def _parse_log_level(value: str) -> str:
    """Normalize and validate an application log-level string."""
    normalized = value.upper()
    if normalized not in VALID_LOG_LEVELS:
        valid = ", ".join(sorted(VALID_LOG_LEVELS))
        raise InvalidConfigError(
            f"Invalid log level '{value}'. Expected one of: {valid}.",
            variable=ENV_VAR_LOG_LEVEL,
            value=value,
        )
    return normalized
