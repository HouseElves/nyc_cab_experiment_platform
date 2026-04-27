"""Internal environment-mapping helpers for the NYC Cab Experiment Platform.

This module provides the small typed primitives that runtime configuration
loaders share: required and optional value lookup against a string mapping,
with consistent treatment of missing and whitespace-only values.

The leading underscore on the module name marks it as private to the
``nyc_cab`` package. Public-facing configuration loaders live in
:mod:`nyc_cab.config` and :mod:`nyc_cab.spark_config`.
"""

from __future__ import annotations

from collections.abc import Mapping

from nyc_cab.exceptions import MissingConfigError


def require(source: Mapping[str, str], name: str) -> str:
    """Return a required value from the environment mapping.

    A value consisting solely of whitespace counts as absent.

    Args:
        source: Environment variable mapping.
        name: Environment variable name.

    Returns:
        The stripped environment variable value.

    Raises:
        MissingConfigError: The variable is absent or blank.
    """
    raw = source.get(name)
    if raw is None or raw.strip() == "":
        raise MissingConfigError(
            f"Required environment variable {name} is not set.",
            variable=name,
        )
    return raw.strip()


def optional(source: Mapping[str, str], name: str, default: str) -> str:
    """Return an optional value from the environment mapping.

    Missing or blank values fall back to ``default``.

    Args:
        source: Environment variable mapping.
        name: Environment variable name.
        default: Default value to use when the variable is absent or blank.

    Returns:
        The stripped configured value or the default.
    """
    raw = source.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip()
