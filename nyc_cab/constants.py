"""Platform-wide constants for the NYC Cab Experiment Platform.

This module holds values that are fixed at authoring time and never vary by
deployment: the package name, the environment-variable naming convention,
medallion layer identifiers, and a small set of platform defaults that other
modules reference by symbol rather than by literal.

No logic lives here. No I/O happens here. Nothing in this module imports from
elsewhere inside ``nyc_cab``.
"""

from __future__ import annotations

from typing import Final

# --- Package identity -------------------------------------------------------

PACKAGE_NAME: Final[str] = "nyc_cab"
"""Canonical package identifier used for Spark app names and log prefixes."""

ENV_VAR_PREFIX: Final[str] = "NYC_CAB_"
"""Shared prefix for every environment variable the platform consumes."""

# --- Medallion layers -------------------------------------------------------

BRONZE_LAYER: Final[str] = "bronze"
SILVER_LAYER: Final[str] = "silver"
GOLD_LAYER: Final[str] = "gold"

DATA_LAYERS: Final[tuple[str, ...]] = (BRONZE_LAYER, SILVER_LAYER, GOLD_LAYER)
"""Ordered medallion layers, Bronze through Gold."""

# --- Environment variable names ---------------------------------------------
#
# The *names* are platform-stable identifiers. The *values* these names map to
# at runtime are the concern of :mod:`nyc_cab.config`, not this module.

ENV_VAR_ENVIRONMENT: Final[str] = ENV_VAR_PREFIX + "ENVIRONMENT"
ENV_VAR_DATA_ROOT: Final[str] = ENV_VAR_PREFIX + "DATA_ROOT"
ENV_VAR_SPARK_MASTER: Final[str] = ENV_VAR_PREFIX + "SPARK_MASTER"
ENV_VAR_SPARK_APP_NAME: Final[str] = ENV_VAR_PREFIX + "SPARK_APP_NAME"
ENV_VAR_LOG_LEVEL: Final[str] = ENV_VAR_PREFIX + "LOG_LEVEL"

ENV_VAR_NAMES: Final[tuple[str, ...]] = (
    ENV_VAR_ENVIRONMENT,
    ENV_VAR_DATA_ROOT,
    ENV_VAR_SPARK_MASTER,
    ENV_VAR_SPARK_APP_NAME,
    ENV_VAR_LOG_LEVEL,
)

# --- Platform defaults ------------------------------------------------------

DEFAULT_FILE_FORMAT: Final[str] = "parquet"
"""Default on-disk format for partitioned platform data."""

NYC_TIMEZONE: Final[str] = "America/New_York"
"""Canonical timezone for NYC TLC trip records.

This is a property of the dataset rather than a deployment choice, so it lives
here rather than in runtime configuration.
"""

DEFAULT_LOG_LEVEL: Final[str] = "INFO"
"""Default logging level when no value is supplied in the environment."""

DEFAULT_SPARK_MASTER_LOCAL: Final[str] = "local[*]"
"""Spark master URL used for local development environments."""

DEFAULT_DATA_ROOT_LOCAL: Final[str] = "./data"
"""Default data root path used for local development environments."""

# --- Accepted values --------------------------------------------------------

VALID_LOG_LEVELS: Final[frozenset[str]] = frozenset(
    {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
)
"""Log-level strings the platform accepts, normalized to upper case."""
