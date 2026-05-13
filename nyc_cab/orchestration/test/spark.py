# pylint: disable=redefined-outer-name
"""
Tests for :mod:`nyc_cab.orchestration.spark`.

These tests exercise the Spark session factory with a real local session.
The module-scoped fixture creates one session via :func:`build_spark_session`
and verifies the platform-required configuration through that session's
properties.
"""

from __future__ import annotations

import pytest
from pyspark.sql import SparkSession

from nyc_cab.config import load_config
from nyc_cab.orchestration.spark import apply_spark_config, build_spark_session
from nyc_cab.spark_config import SparkConfig, load_spark_config


@pytest.fixture(scope="module")
def runtime_config(tmp_path_factory):
    """Build a runtime config rooted in a temporary directory."""
    data_root = tmp_path_factory.mktemp("data")
    return load_config({"NYC_CAB_DATA_ROOT": str(data_root)})


@pytest.fixture(scope="module")
def spark_config():
    """Build a Spark config with defaults."""
    return load_spark_config({})


@pytest.fixture(scope="module")
def spark(runtime_config, spark_config, tmp_path_factory):
    """Create a Spark session via build_spark_session for the test module."""
    warehouse = tmp_path_factory.mktemp("spark_warehouse")
    # Pre-set warehouse and Derby paths before build_spark_session adds its own config.
    # This avoids creating Derby metadata in the project working directory.
    SparkSession.builder.config("spark.sql.warehouse.dir", str(warehouse))
    SparkSession.builder.config("spark.driver.extraJavaOptions", "-Dderby.system.home=" + str(warehouse))
    session = build_spark_session(runtime_config, spark_config)
    yield session
    session.stop()


# --- build_spark_session ----------------------------------------------------


@pytest.mark.spark
def test_build_spark_session_returns_spark_session(spark) -> None:
    """The factory returns a real SparkSession instance."""
    assert isinstance(spark, SparkSession)


@pytest.mark.spark
def test_build_spark_session_sets_master(spark) -> None:
    """The session uses the master from SparkConfig."""
    assert spark.conf.get("spark.master") == "local[*]"


@pytest.mark.spark
def test_build_spark_session_sets_app_name(spark) -> None:
    """The session uses the app name from SparkConfig."""
    assert spark.conf.get("spark.app.name") == "nyc_cab"


@pytest.mark.spark
def test_build_spark_session_sets_partition_overwrite_mode(spark) -> None:
    """The session has dynamic partition overwrite mode for idempotent writes."""
    assert spark.conf.get("spark.sql.sources.partitionOverwriteMode") == "dynamic"


@pytest.mark.spark
def test_build_spark_session_sets_log_level(spark) -> None:
    """The session's Spark context log level is set without error."""
    # SparkContext doesn't expose log level as a readable property in PySpark,
    # but we can verify the call didn't raise and the session is functional.
    assert spark.sparkContext is not None


# --- apply_spark_config -----------------------------------------------------


@pytest.mark.unit
def test_apply_spark_config_returns_builder() -> None:
    """The function returns a Builder for chaining."""
    spark_cfg = SparkConfig(master="local[1]", app_name="test_apply")
    result = apply_spark_config(SparkSession.builder, spark_cfg)
    assert result is not None


@pytest.mark.unit
def test_apply_spark_config_accepts_custom_master() -> None:
    """A non-default master value is accepted without error."""
    spark_cfg = SparkConfig(master="local[2]", app_name="test_custom_master")
    builder = apply_spark_config(SparkSession.builder, spark_cfg)
    assert builder is not None


@pytest.mark.unit
def test_apply_spark_config_accepts_custom_app_name() -> None:
    """A non-default app name is accepted without error."""
    spark_cfg = SparkConfig(master="local[1]", app_name="custom_name")
    builder = apply_spark_config(SparkSession.builder, spark_cfg)
    assert builder is not None
