"""Tests for :mod:`nyc_cab.constants`.

These tests guard the invariants that other modules rely on: package identity,
the environment-variable prefix, the medallion-layer tuple, and the platform
defaults used by runtime configuration modules.
"""

from __future__ import annotations

from nyc_cab import constants


def test_package_name_is_nyc_cab() -> None:
    """The canonical package identifier is ``nyc_cab``."""
    assert constants.PACKAGE_NAME == "nyc_cab"


def test_env_var_prefix_matches_package() -> None:
    """The environment-variable prefix corresponds to the package name."""
    assert constants.ENV_VAR_PREFIX == "NYC_CAB_"


def test_medallion_layer_names_are_distinct() -> None:
    """Bronze, Silver, and Gold layer names do not collide."""
    layers = {constants.BRONZE_LAYER, constants.SILVER_LAYER, constants.GOLD_LAYER}
    assert len(layers) == 3


def test_data_layers_tuple_is_ordered_bronze_silver_gold() -> None:
    """Iterating ``DATA_LAYERS`` yields Bronze, then Silver, then Gold."""
    assert constants.DATA_LAYERS == (
        constants.BRONZE_LAYER,
        constants.SILVER_LAYER,
        constants.GOLD_LAYER,
    )


def test_env_var_names_share_the_platform_prefix() -> None:
    """Every environment-variable name starts with ``ENV_VAR_PREFIX``."""
    for name in constants.ENV_VAR_NAMES:
        assert name.startswith(constants.ENV_VAR_PREFIX)


def test_env_var_names_are_distinct() -> None:
    """Each environment-variable name is unique."""
    assert len(set(constants.ENV_VAR_NAMES)) == len(constants.ENV_VAR_NAMES)


def test_default_file_format_is_parquet() -> None:
    """Parquet is the platform default storage format."""
    assert constants.DEFAULT_FILE_FORMAT == "parquet"


def test_nyc_timezone_is_eastern() -> None:
    """The NYC timezone constant resolves to ``America/New_York``."""
    assert constants.NYC_TIMEZONE == "America/New_York"


def test_default_log_level_is_info() -> None:
    """``INFO`` serves as the default log level when unset."""
    assert constants.DEFAULT_LOG_LEVEL == "INFO"


def test_default_spark_master_targets_local_cores() -> None:
    """The local Spark master uses every available core by default."""
    assert constants.DEFAULT_SPARK_MASTER_LOCAL == "local[*]"


def test_default_data_root_local_is_relative_data_directory() -> None:
    """Local environments default to ``./data`` for the data root."""
    assert constants.DEFAULT_DATA_ROOT_LOCAL == "./data"


def test_valid_log_levels_cover_the_standard_set() -> None:
    """``VALID_LOG_LEVELS`` contains the standard Python logging levels."""
    assert constants.VALID_LOG_LEVELS == frozenset(
        {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    )


def test_default_log_level_is_valid() -> None:
    """The default log level is part of the accepted set."""
    assert constants.DEFAULT_LOG_LEVEL in constants.VALID_LOG_LEVELS
