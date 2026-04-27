"""Spark-adjacent runtime configuration for the NYC Cab Experiment Platform.

This module defines configuration values that the future Spark session factory
will consume. It remains independent from the Spark library itself.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from nyc_cab import _env
from nyc_cab.constants import (
    DEFAULT_SPARK_MASTER_LOCAL,
    ENV_VAR_SPARK_APP_NAME,
    ENV_VAR_SPARK_MASTER,
    PACKAGE_NAME,
)


@dataclass(frozen=True, slots=True)
class SparkConfig:
    """Immutable Spark-adjacent runtime configuration."""

    master: str
    app_name: str


def load_spark_config(environ: Mapping[str, str] | None = None) -> SparkConfig:
    """Load and freeze Spark-adjacent runtime configuration.

    Args:
        environ: Mapping of environment variable names to values. When omitted,
            ``os.environ`` supplies the values. Tests should prefer an explicit
            mapping over mutating process state.

    Returns:
        A populated :class:`SparkConfig` instance.
    """
    source: Mapping[str, str] = os.environ if environ is None else environ
    master = _env.optional(source, ENV_VAR_SPARK_MASTER, DEFAULT_SPARK_MASTER_LOCAL)
    app_name = _env.optional(source, ENV_VAR_SPARK_APP_NAME, PACKAGE_NAME)
    return SparkConfig(master=master, app_name=app_name)
