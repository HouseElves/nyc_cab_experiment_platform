"""Tests for :mod:`nyc_cab.spark_config`."""

from __future__ import annotations

import dataclasses

import pytest

from nyc_cab import constants
from nyc_cab.spark_config import SparkConfig, load_spark_config


def _env(**overrides: str) -> dict[str, str]:
    """Return an environment mapping containing only the supplied overrides."""
    return dict(overrides)


def test_empty_environ_uses_spark_defaults() -> None:
    """An empty mapping loads the default Spark-adjacent configuration."""
    spark = load_spark_config(_env())
    assert spark.master == constants.DEFAULT_SPARK_MASTER_LOCAL
    assert spark.app_name == constants.PACKAGE_NAME


def test_spark_master_override_is_preserved() -> None:
    """An explicit Spark master overrides the local default."""
    spark = load_spark_config(
        _env(NYC_CAB_SPARK_MASTER="spark://example.invalid:7077")
    )
    assert spark.master == "spark://example.invalid:7077"


def test_spark_master_whitespace_falls_back_to_default() -> None:
    """A whitespace-only Spark master falls back to the default value."""
    spark = load_spark_config(_env(NYC_CAB_SPARK_MASTER="   "))
    assert spark.master == constants.DEFAULT_SPARK_MASTER_LOCAL


def test_spark_app_name_override_is_preserved() -> None:
    """An explicit Spark app name overrides the package default."""
    spark = load_spark_config(_env(NYC_CAB_SPARK_APP_NAME="custom-app"))
    assert spark.app_name == "custom-app"


def test_spark_app_name_whitespace_falls_back_to_default() -> None:
    """A whitespace-only Spark app name falls back to the default value."""
    spark = load_spark_config(_env(NYC_CAB_SPARK_APP_NAME="   "))
    assert spark.app_name == constants.PACKAGE_NAME


def test_load_spark_config_falls_back_to_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When called with no argument, ``load_spark_config`` reads ``os.environ``."""
    for name in (
        constants.ENV_VAR_SPARK_MASTER,
        constants.ENV_VAR_SPARK_APP_NAME,
    ):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setenv(constants.ENV_VAR_SPARK_MASTER, "local[2]")
    monkeypatch.setenv(constants.ENV_VAR_SPARK_APP_NAME, "batch-job")

    spark = load_spark_config()

    assert spark.master == "local[2]"
    assert spark.app_name == "batch-job"


def test_spark_config_is_frozen() -> None:
    """:class:`SparkConfig` rejects attribute mutation after construction."""
    spark = load_spark_config(_env())
    with pytest.raises(dataclasses.FrozenInstanceError):
        spark.master = "other"  # type: ignore[misc]


def test_spark_config_can_be_constructed_directly() -> None:
    """Direct construction yields an equivalent immutable Spark config."""
    spark = SparkConfig(master="local[*]", app_name="nyc_cab")
    assert spark.master == "local[*]"
    assert spark.app_name == "nyc_cab"
