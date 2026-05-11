# pylint: disable=redefined-outer-name
"""
	Shared fixtures for root-level integration tests.

This module provides a session-scoped Spark session built via the platform's
real ``build_spark_session`` and a helper for generating synthetic parquet
files that match the Bronze v1 Yellow cab schema.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pyspark.sql import SparkSession

from nyc_cab.config import load_config
from nyc_cab.contracts.bronze import BRONZE_RAW_YELLOW_SCHEMA_FIELDS, BronzeSchemaField
from nyc_cab.orchestration.spark import build_spark_session
from nyc_cab.spark_config import load_spark_config


# --- Spark type to Arrow type mapping --------------------------------------

_SPARK_TO_ARROW = {
    "bigint": pa.int64(),
    "double": pa.float64(),
    "string": pa.string(),
    "timestamp_ntz": pa.timestamp("us"),
}


# --- Synthetic parquet generation -------------------------------------------


def _generate_column_data(spark_type: str, row_count: int) -> list:
    """
	Return a list of plausible fake values for a given Spark type."""
    if spark_type == "bigint":
        return [i % 5 + 1 for i in range(row_count)]
    if spark_type == "double":
        return [round(1.5 + i * 0.3, 2) for i in range(row_count)]
    if spark_type == "string":
        return ["N"] * row_count
    if spark_type == "timestamp_ntz":
        base = datetime.datetime(2023, 1, 15, 10, 0, 0)
        return [base + datetime.timedelta(minutes=i * 30) for i in range(row_count)]
    raise ValueError(f"No fake-data generator for spark_type '{spark_type}'")


def write_synthetic_yellow_parquet(
    destination: Path,
    fields: tuple[BronzeSchemaField, ...] = BRONZE_RAW_YELLOW_SCHEMA_FIELDS,
    row_count: int = 10,
) -> Path:
    """
	Write a synthetic parquet file matching the Bronze v1 Yellow cab schema.

    The file is built from the contract's ``BronzeSchemaField`` definitions so
    it automatically adjusts if the contract schema changes. Returns the path
    to the written file.
    """
    arrow_schema = pa.schema([
        pa.field(f.name, _SPARK_TO_ARROW[f.spark_type], nullable=f.nullable)
        for f in fields
    ])
    columns = {
        f.name: _generate_column_data(f.spark_type, row_count)
        for f in fields
    }
    table = pa.table(columns, schema=arrow_schema)
    destination.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, str(destination))
    return destination


# --- Session-scoped fixtures ------------------------------------------------


@pytest.fixture(scope="session")
def spark(tmp_path_factory):
    """Create a Spark session via the platform's real session factory."""
    warehouse = tmp_path_factory.mktemp("spark_warehouse")
    # Pre-set warehouse and Derby paths to avoid metadata pollution in the
    # project working directory. These configs accumulate on the JVM-level
    # builder state and are picked up by build_spark_session's getOrCreate.
    SparkSession.builder.config("spark.sql.warehouse.dir", str(warehouse))
    SparkSession.builder.config(
        "spark.driver.extraJavaOptions",
        "-Dderby.system.home=" + str(warehouse),
    )
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path_factory.mktemp("data"))})
    spark_cfg = load_spark_config({})
    session = build_spark_session(runtime, spark_cfg)
    yield session
    session.stop()
