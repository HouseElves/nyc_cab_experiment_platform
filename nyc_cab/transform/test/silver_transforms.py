"""
Tests for :mod:`nyc_cab.transform.silver_transforms`.

These tests exercise ``apply_type_normalizations`` against a real Spark
session using small inline DataFrames. No I/O, no fixtures beyond the
module-scoped Spark session.
"""

from __future__ import annotations

import pytest
from pyspark.sql import SparkSession
from pyspark.sql.types import DoubleType, StructField, StructType

from nyc_cab.transform.silver_transforms import apply_type_normalizations

pytestmark = pytest.mark.spark


# pylint: disable=redefined-outer-name
# pylint: disable=duplicate-code
# See design_log.md decision 28: Spark session fixture duplication across Silver test modules
# is intentional. The shared abstraction will be extracted when the test infrastructure stabilises.


# Schema: two normalization targets (double) plus one bystander column.
_SCHEMA = StructType([
    StructField("passenger_count", DoubleType(), True),
    StructField("RatecodeID",      DoubleType(), True),
    StructField("fare_amount",     DoubleType(), True),
])


# ---------------------------------------------------------------------------
# Spark fixture (module-scoped)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def spark(tmp_path_factory):
    """Create a local Spark session for silver_transforms tests."""
    warehouse = tmp_path_factory.mktemp("spark_warehouse")
    session = (
        SparkSession.builder
        .master("local[1]")
        .appName("test_silver_transforms")
        .config("spark.sql.warehouse.dir", str(warehouse))
        .config("spark.driver.extraJavaOptions", "-Dderby.system.home=" + str(warehouse))
        .getOrCreate()
    )
    yield session
    session.stop()


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------


def test_normalizations_cast_passenger_count_to_int(spark) -> None:
    """passenger_count is cast from double to int in the output schema."""
    df = spark.createDataFrame([(2.0, 1.0, 10.0)], schema=_SCHEMA)
    result = apply_type_normalizations(df)
    schema_map = {f.name: f.dataType.simpleString() for f in result.schema.fields}
    assert schema_map["passenger_count"] == "int"


def test_normalizations_cast_ratecode_to_int(spark) -> None:
    """RatecodeID is cast from double to int in the output schema."""
    df = spark.createDataFrame([(1.0, 3.0, 10.0)], schema=_SCHEMA)
    result = apply_type_normalizations(df)
    schema_map = {f.name: f.dataType.simpleString() for f in result.schema.fields}
    assert schema_map["RatecodeID"] == "int"


def test_normalizations_preserves_non_target_column_type(spark) -> None:
    """Columns not listed in SILVER_YELLOW_TYPE_NORMALIZATIONS are not retyped."""
    df = spark.createDataFrame([(1.0, 1.0, 12.50)], schema=_SCHEMA)
    result = apply_type_normalizations(df)
    schema_map = {f.name: f.dataType.simpleString() for f in result.schema.fields}
    assert schema_map["fare_amount"] == "double"


# ---------------------------------------------------------------------------
# Output values
# ---------------------------------------------------------------------------


def test_normalizations_preserves_integral_values(spark) -> None:
    """Integral double values survive the cast with the correct integer result."""
    df = spark.createDataFrame([(3.0, 2.0, 5.0)], schema=_SCHEMA)
    result = apply_type_normalizations(df)
    row = result.collect()[0]
    assert row["passenger_count"] == 3
    assert row["RatecodeID"] == 2


def test_normalizations_truncates_non_integral_values(spark) -> None:
    """Non-integral doubles are truncated, not rounded, by the int cast."""
    df = spark.createDataFrame([(1.9, 2.7, 5.0)], schema=_SCHEMA)
    result = apply_type_normalizations(df)
    row = result.collect()[0]
    assert row["passenger_count"] == 1
    assert row["RatecodeID"] == 2


def test_normalizations_preserves_non_target_column_value(spark) -> None:
    """The bystander column value is not altered by the normalization pass."""
    df = spark.createDataFrame([(1.0, 1.0, 99.99)], schema=_SCHEMA)
    result = apply_type_normalizations(df)
    row = result.collect()[0]
    assert row["fare_amount"] == pytest.approx(99.99)


# ---------------------------------------------------------------------------
# Null handling
# ---------------------------------------------------------------------------


def test_normalizations_propagates_null_in_passenger_count(spark) -> None:
    """A null passenger_count survives the int cast as null rather than erroring."""
    df = spark.createDataFrame([(None, 1.0, 10.0)], schema=_SCHEMA)
    result = apply_type_normalizations(df)
    row = result.collect()[0]
    assert row["passenger_count"] is None


def test_normalizations_propagates_null_in_ratecode(spark) -> None:
    """A null RatecodeID survives the int cast as null rather than erroring."""
    df = spark.createDataFrame([(1.0, None, 10.0)], schema=_SCHEMA)
    result = apply_type_normalizations(df)
    row = result.collect()[0]
    assert row["RatecodeID"] is None
