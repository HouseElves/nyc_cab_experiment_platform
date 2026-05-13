# pylint: disable=redefined-outer-name
"""
Tests for :mod:`nyc_cab.ingestion.bronze_entrypoint`.

These tests cover:

* :class:`BronzeIngestionResult` -- the post-ingestion result, which composes a
  request, an acquired source file, and the new facts produced by the run
  (partition path, row count). The structural checks delegate to the composed
  members via :meth:`_Validated.validity_check`.
* :func:`ingest_bronze_month` -- the five-step orchestration flow. Steps 4-5
  require a local Spark session; step 3 (file acquisition) is mocked with a
  pre-staged file for these tests.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pandas as pd
import pytest
from pyspark.sql import SparkSession

from nyc_cab.config import load_config
from nyc_cab.contracts.bronze import BronzeSchemaField
from nyc_cab.exceptions import InvalidRequestError
from nyc_cab.ingestion.bronze_entrypoint import BronzeIngestionResult, ingest_bronze_month
from nyc_cab.ingestion.bronze_io import AcquiredSourceFile
from nyc_cab.ingestion.bronze_request import (
    BronzeIngestionConfig,
    BronzeIngestionRequest,
)


# --- Test fixtures and helpers ----------------------------------------------


# Stub schema matching the pandas-generated test parquet.
# pandas int64 -> Spark bigint; pandas float64 -> Spark double; all nullable.
_STUB_YELLOW_FIELDS: tuple[BronzeSchemaField, ...] = (
    BronzeSchemaField("VendorID", "bigint", True),
    BronzeSchemaField("trip_distance", "double", True),
    BronzeSchemaField("payment_type", "bigint", True),
)

_TEST_ROW_COUNT = 3


def _request() -> BronzeIngestionRequest:
    """Build a valid Bronze ingestion request for tests."""
    return BronzeIngestionRequest.create_validated("yellow", 2023, 1)


def _source(tmp_path: Path) -> AcquiredSourceFile:
    """Build a valid acquired-source artifact rooted in ``tmp_path``."""
    file_path = tmp_path / "yellow_tripdata_2023-01.parquet"
    file_path.write_bytes(b"data")
    return AcquiredSourceFile.create_validated(file_path, "https://x/y", True)


def _write_test_source_parquet(base_path: Path) -> Path:
    """Write a small test parquet file and return its path."""
    source_dir = base_path / "source"
    source_dir.mkdir(exist_ok=True)
    source_path = source_dir / "test_source.parquet"
    pdf = pd.DataFrame({
        "VendorID": pd.array([1, 2, 3], dtype="int64"),
        "trip_distance": [1.5, 2.5, 0.8],
        "payment_type": pd.array([1, 2, 1], dtype="int64"),
    })
    pdf.to_parquet(source_path)
    return source_path


def _write_test_source_parquet_lowercase(base_path: Path) -> Path:
    """Write a test parquet with lowercase column names for canonicalization testing."""
    source_dir = base_path / "source"
    source_dir.mkdir(exist_ok=True)
    source_path = source_dir / "test_source.parquet"
    pdf = pd.DataFrame({
        "vendorid": pd.array([1, 2, 3], dtype="int64"),
        "trip_distance": [1.5, 2.5, 0.8],
        "payment_type": pd.array([1, 2, 1], dtype="int64"),
    })
    pdf.to_parquet(source_path)
    return source_path


def _mock_acquire(source_file: Path):
    """Return a mock acquire function that hands back ``source_file``."""
    def _acquire(source_url, _source_filename, _config):
        return AcquiredSourceFile.create_validated(source_file, source_url, False)
    return _acquire


@pytest.fixture(scope="module")
def spark(tmp_path_factory):
    """Create a local Spark session for Bronze ingestion tests."""
    warehouse = tmp_path_factory.mktemp("spark_warehouse")
    session = (
        SparkSession.builder
        .master("local[1]")
        .appName("test_bronze_entrypoint")
        .config("spark.sql.sources.partitionOverwriteMode", "dynamic")
        .config("spark.sql.warehouse.dir", str(warehouse))
        .config("spark.driver.extraJavaOptions", "-Dderby.system.home=" + str(warehouse))
        .getOrCreate()
    )
    yield session
    session.stop()


# --- BronzeIngestionResult: happy paths -------------------------------------


@pytest.mark.unit
def test_result_create_validated_happy_path(tmp_path: Path) -> None:
    """A well-formed result constructs cleanly."""
    partition = tmp_path / "year=2023" / "month=1"
    partition.mkdir(parents=True)
    result = BronzeIngestionResult.create_validated(
        _request(), _source(tmp_path), partition, 12345,
    )
    assert result.row_count == 12345
    assert result.is_valid()


@pytest.mark.unit
def test_result_accepts_zero_row_count(tmp_path: Path) -> None:
    """Zero rows is structurally valid (empty partition write)."""
    partition = tmp_path / "year=2023" / "month=1"
    partition.mkdir(parents=True)
    result = BronzeIngestionResult.create_validated(
        _request(), _source(tmp_path), partition, 0,
    )
    assert result.row_count == 0


@pytest.mark.unit
def test_result_accepts_nonexistent_partition_path(tmp_path: Path) -> None:
    """A partition directory that doesn't exist yet is structurally valid."""
    not_yet = tmp_path / "year=2023" / "month=1"
    result = BronzeIngestionResult.create_validated(
        _request(), _source(tmp_path), not_yet, 100,
    )
    assert result.bronze_partition_path == not_yet


# --- BronzeIngestionResult: type-check rejections ---------------------------


@pytest.mark.unit
def test_result_rejects_non_request(tmp_path: Path) -> None:
    """``request`` must be a :class:`BronzeIngestionRequest`."""
    with pytest.raises(InvalidRequestError) as info:
        BronzeIngestionResult.create_validated(
            "not-a-request", _source(tmp_path), tmp_path, 100,
        )
    names = [v[0] for v in info.value.violations]
    assert "request" in names


@pytest.mark.unit
def test_result_rejects_non_source(tmp_path: Path) -> None:
    """``source`` must be an :class:`AcquiredSourceFile`."""
    with pytest.raises(InvalidRequestError) as info:
        BronzeIngestionResult.create_validated(
            _request(), "not-a-source", tmp_path, 100,
        )
    names = [v[0] for v in info.value.violations]
    assert "source" in names


@pytest.mark.unit
def test_result_rejects_string_partition_path(tmp_path: Path) -> None:
    """``bronze_partition_path`` must be a ``Path``."""
    with pytest.raises(InvalidRequestError) as info:
        BronzeIngestionResult.create_validated(
            _request(), _source(tmp_path), str(tmp_path), 100,
        )
    names = [v[0] for v in info.value.violations]
    assert "bronze_partition_path" in names


@pytest.mark.unit
def test_result_rejects_bool_row_count(tmp_path: Path) -> None:
    """``row_count`` rejects ``True``/``False`` despite int compatibility."""
    with pytest.raises(InvalidRequestError) as info:
        BronzeIngestionResult.create_validated(
            _request(), _source(tmp_path), tmp_path, True,
        )
    assert ("row_count", True) in info.value.violations


@pytest.mark.unit
def test_result_rejects_string_row_count(tmp_path: Path) -> None:
    """``row_count`` must be an int."""
    with pytest.raises(InvalidRequestError) as info:
        BronzeIngestionResult.create_validated(
            _request(), _source(tmp_path), tmp_path, "100",
        )
    assert ("row_count", "100") in info.value.violations


# --- BronzeIngestionResult: structural rejections ---------------------------


@pytest.mark.unit
def test_result_rejects_negative_row_count(tmp_path: Path) -> None:
    """Negative row counts violate the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        BronzeIngestionResult.create_validated(
            _request(), _source(tmp_path), tmp_path, -1,
        )
    assert ("row_count", -1) in info.value.violations


@pytest.mark.unit
def test_result_rejects_file_as_partition_path(tmp_path: Path) -> None:
    """A regular file at the partition path violates the directory rule."""
    file_path = tmp_path / "not-a-dir"
    file_path.write_text("data")
    with pytest.raises(InvalidRequestError) as info:
        BronzeIngestionResult.create_validated(
            _request(), _source(tmp_path), file_path, 100,
        )
    assert ("bronze_partition_path", file_path) in info.value.violations


# --- validity_check chaining ------------------------------------------------


@pytest.mark.unit
def test_result_chaining_catches_invalid_request(tmp_path: Path) -> None:
    """A structurally-bad request bubbles up as a ``request`` violation."""
    bad_request = BronzeIngestionRequest("yellow", 2023, 13)
    with pytest.raises(InvalidRequestError) as info:
        BronzeIngestionResult.create_validated(
            bad_request, _source(tmp_path), tmp_path, 100,
        )
    names = [v[0] for v in info.value.violations]
    assert "request" in names


@pytest.mark.unit
def test_result_chaining_catches_invalid_source(tmp_path: Path) -> None:
    """A structurally-bad source bubbles up as a ``source`` violation."""
    file_path = tmp_path / "data.parquet"
    file_path.write_bytes(b"data")
    bad_source = AcquiredSourceFile(file_path, "   ", True)
    with pytest.raises(InvalidRequestError) as info:
        BronzeIngestionResult.create_validated(
            _request(), bad_source, tmp_path, 100,
        )
    names = [v[0] for v in info.value.violations]
    assert "source" in names


@pytest.mark.unit
def test_result_chaining_aggregates_multiple_member_failures(tmp_path: Path) -> None:
    """Multiple structurally-bad composed members all surface in one exception."""
    bad_request = BronzeIngestionRequest("yellow", 2023, 13)
    file_path = tmp_path / "data.parquet"
    file_path.write_bytes(b"data")
    bad_source = AcquiredSourceFile(file_path, "   ", True)
    with pytest.raises(InvalidRequestError) as info:
        BronzeIngestionResult.create_validated(
            bad_request, bad_source, tmp_path, 100,
        )
    names = [v[0] for v in info.value.violations]
    assert "request" in names
    assert "source" in names


# --- Frozenness -------------------------------------------------------------


@pytest.mark.unit
def test_result_is_frozen(tmp_path: Path) -> None:
    """:class:`BronzeIngestionResult` rejects attribute mutation."""
    partition = tmp_path / "year=2023" / "month=1"
    partition.mkdir(parents=True)
    result = BronzeIngestionResult.create_validated(
        _request(), _source(tmp_path), partition, 100,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.row_count = 200  # type: ignore[misc]


# --- ingest_bronze_month: step 1 rejection ----------------------------------


@pytest.mark.unit
def test_ingest_rejects_unsupported_slice(tmp_path: Path) -> None:
    """An unsupported slice is rejected before any I/O or Spark interaction."""
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    config = BronzeIngestionConfig.create_validated(tmp_path / "cache", 10, 60)
    request = BronzeIngestionRequest.create_validated("green", 2023, 1)
    with pytest.raises(InvalidRequestError) as info:
        ingest_bronze_month(None, runtime, config, request)  # type: ignore[arg-type]
    names = [v[0] for v in info.value.violations]
    assert "cab_type" in names


@pytest.mark.unit
def test_ingest_rejects_unsupported_period(tmp_path: Path) -> None:
    """An unsupported period is rejected before any I/O or Spark interaction."""
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    config = BronzeIngestionConfig.create_validated(tmp_path / "cache", 10, 60)
    request = BronzeIngestionRequest.create_validated("yellow", 2023, 6)
    with pytest.raises(InvalidRequestError) as info:
        ingest_bronze_month(None, runtime, config, request)  # type: ignore[arg-type]
    names = [v[0] for v in info.value.violations]
    assert "period" in names


# --- ingest_bronze_month: happy path ----------------------------------------


@pytest.mark.spark
def test_ingest_happy_path(spark, tmp_path, monkeypatch) -> None:
    """Full ingestion run produces a valid result with correct row count."""
    source_file = _write_test_source_parquet(tmp_path)
    monkeypatch.setattr(
        "nyc_cab.ingestion.bronze_entrypoint.acquire_bronze_source_file",
        _mock_acquire(source_file),
    )
    monkeypatch.setattr(
        "nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS",
        _STUB_YELLOW_FIELDS,
    )

    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    config = BronzeIngestionConfig.create_validated(tmp_path / "cache", 10, 60)
    request = _request()

    result = ingest_bronze_month(spark, runtime, config, request)

    assert result.row_count == _TEST_ROW_COUNT
    assert result.request is request
    assert result.source.cache_hit is False
    assert result.is_valid()


@pytest.mark.spark
def test_ingest_writes_partition_directory(spark, tmp_path, monkeypatch) -> None:
    """The ingestion creates the Hive-style partition directory on disk."""
    source_file = _write_test_source_parquet(tmp_path)
    monkeypatch.setattr(
        "nyc_cab.ingestion.bronze_entrypoint.acquire_bronze_source_file",
        _mock_acquire(source_file),
    )
    monkeypatch.setattr(
        "nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS",
        _STUB_YELLOW_FIELDS,
    )

    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    config = BronzeIngestionConfig.create_validated(tmp_path / "cache", 10, 60)

    result = ingest_bronze_month(spark, runtime, config, _request())

    assert result.bronze_partition_path.exists()
    assert result.bronze_partition_path.is_dir()
    parquet_files = list(result.bronze_partition_path.glob("*.parquet"))
    assert len(parquet_files) > 0


@pytest.mark.spark
def test_ingest_partition_path_uses_hive_layout(spark, tmp_path, monkeypatch) -> None:
    """The written partition path follows the cab_type/year/month Hive layout."""
    source_file = _write_test_source_parquet(tmp_path)
    monkeypatch.setattr(
        "nyc_cab.ingestion.bronze_entrypoint.acquire_bronze_source_file",
        _mock_acquire(source_file),
    )
    monkeypatch.setattr(
        "nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS",
        _STUB_YELLOW_FIELDS,
    )

    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    config = BronzeIngestionConfig.create_validated(tmp_path / "cache", 10, 60)

    result = ingest_bronze_month(spark, runtime, config, _request())

    parts = result.bronze_partition_path.parts
    assert "cab_type=yellow" in parts
    assert "year=2023" in parts
    assert "month=1" in parts


@pytest.mark.spark
def test_ingest_source_url_flows_through_to_result(spark, tmp_path, monkeypatch) -> None:
    """The result's source carries the URL that was passed to acquisition."""
    source_file = _write_test_source_parquet(tmp_path)
    monkeypatch.setattr(
        "nyc_cab.ingestion.bronze_entrypoint.acquire_bronze_source_file",
        _mock_acquire(source_file),
    )
    monkeypatch.setattr(
        "nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS",
        _STUB_YELLOW_FIELDS,
    )

    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    config = BronzeIngestionConfig.create_validated(tmp_path / "cache", 10, 60)

    result = ingest_bronze_month(spark, runtime, config, _request())

    assert result.source.source_url.endswith("yellow_tripdata_2023-01.parquet")


# --- ingest_bronze_month: canonicalization ----------------------------------


@pytest.mark.spark
def test_ingest_canonicalizes_column_names(spark, tmp_path, monkeypatch) -> None:
    """Source columns with non-canonical casing are renamed before writing."""
    source_file = _write_test_source_parquet_lowercase(tmp_path)
    monkeypatch.setattr(
        "nyc_cab.ingestion.bronze_entrypoint.acquire_bronze_source_file",
        _mock_acquire(source_file),
    )
    monkeypatch.setattr(
        "nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS",
        _STUB_YELLOW_FIELDS,
    )

    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    config = BronzeIngestionConfig.create_validated(tmp_path / "cache", 10, 60)

    result = ingest_bronze_month(spark, runtime, config, _request())

    # Read back the written parquet to verify the column was renamed.
    written_df = spark.read.parquet(str(result.bronze_partition_path))
    assert "VendorID" in written_df.columns
    assert "vendorid" not in written_df.columns
    assert result.row_count == _TEST_ROW_COUNT


# --- ingest_bronze_month: idempotent re-execution ---------------------------


@pytest.mark.spark
def test_ingest_idempotent_reexecution(spark, tmp_path, monkeypatch) -> None:
    """Running the same ingestion twice produces the same result without error."""
    source_file = _write_test_source_parquet(tmp_path)
    monkeypatch.setattr(
        "nyc_cab.ingestion.bronze_entrypoint.acquire_bronze_source_file",
        _mock_acquire(source_file),
    )
    monkeypatch.setattr(
        "nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS",
        _STUB_YELLOW_FIELDS,
    )

    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    config = BronzeIngestionConfig.create_validated(tmp_path / "cache", 10, 60)
    request = _request()

    first = ingest_bronze_month(spark, runtime, config, request)
    second = ingest_bronze_month(spark, runtime, config, request)

    assert first.row_count == second.row_count
    assert second.bronze_partition_path.exists()


# --- ingest_bronze_month: step 4 schema rejection --------------------------


@pytest.mark.spark
def test_ingest_rejects_schema_mismatch(spark, tmp_path, monkeypatch) -> None:
    """A source file whose schema does not match the contract is rejected."""
    source_file = _write_test_source_parquet(tmp_path)
    monkeypatch.setattr(
        "nyc_cab.ingestion.bronze_entrypoint.acquire_bronze_source_file",
        _mock_acquire(source_file),
    )
    # Expect "string" for VendorID, but the test file has bigint.
    # "string" and "bigint" are in different type families -> incompatible.
    wrong_schema = (
        BronzeSchemaField("VendorID", "string", True),
        BronzeSchemaField("trip_distance", "double", True),
        BronzeSchemaField("payment_type", "bigint", True),
    )
    monkeypatch.setattr(
        "nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS",
        wrong_schema,
    )

    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    config = BronzeIngestionConfig.create_validated(tmp_path / "cache", 10, 60)

    with pytest.raises(InvalidRequestError) as info:
        ingest_bronze_month(spark, runtime, config, _request())
    names = [v[0] for v in info.value.violations]
    assert "VendorID.spark_type" in names


@pytest.mark.spark
def test_ingest_schema_rejection_does_not_write(spark, tmp_path, monkeypatch) -> None:
    """A schema rejection at step 4 prevents the partition write at step 5."""
    source_file = _write_test_source_parquet(tmp_path)
    monkeypatch.setattr(
        "nyc_cab.ingestion.bronze_entrypoint.acquire_bronze_source_file",
        _mock_acquire(source_file),
    )
    wrong_schema = (
        BronzeSchemaField("VendorID", "string", True),
        BronzeSchemaField("trip_distance", "double", True),
        BronzeSchemaField("payment_type", "bigint", True),
    )
    monkeypatch.setattr(
        "nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS",
        wrong_schema,
    )

    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    config = BronzeIngestionConfig.create_validated(tmp_path / "cache", 10, 60)
    bronze_dir = runtime.paths.bronze

    with pytest.raises(InvalidRequestError):
        ingest_bronze_month(spark, runtime, config, _request())

    assert not bronze_dir.exists()
