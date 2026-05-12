# pylint: disable=redefined-outer-name
"""
Bronze ingestion integration tests (fast, synthetic, CI-safe).

These tests exercise the full ``ingest_bronze_month`` flow with a real Spark
session, the real 19-field Bronze v1 contract schema, and real cache-first
acquisition logic. The only thing that does not fire is the HTTP download:
a synthetic parquet file is pre-staged in the cache directory so
``acquire_bronze_source_file`` gets a cache hit on every call.

No mocks, no monkeypatching. Every module boundary is exercised for real.
"""

from __future__ import annotations

from pathlib import Path

from conftest import write_synthetic_yellow_parquet

from nyc_cab.config import load_config
from nyc_cab.contracts.bronze import derive_bronze_source_filename
from nyc_cab.ingestion.bronze_entrypoint import BronzeIngestionResult, ingest_bronze_month
from nyc_cab.ingestion.bronze_request import BronzeIngestionConfig, BronzeIngestionRequest


_ROW_COUNT = 10


# --- Helpers ----------------------------------------------------------------


def _stage_synthetic_source(tmp_path: Path) -> tuple[BronzeIngestionConfig, BronzeIngestionRequest]:
    """Pre-stage a synthetic parquet in the cache directory and return config + request."""
    request = BronzeIngestionRequest.create_validated("yellow", 2023, 1)
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    source_filename = derive_bronze_source_filename("yellow", 2023, 1)
    write_synthetic_yellow_parquet(cache_dir / source_filename, row_count=_ROW_COUNT)
    config = BronzeIngestionConfig.create_validated(cache_dir, 10, 60)
    return config, request


# --- Happy path -------------------------------------------------------------


def test_ingest_produces_valid_result(spark, tmp_path) -> None:
    """Full ingestion run produces a valid BronzeIngestionResult."""
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    config, request = _stage_synthetic_source(tmp_path)

    result = ingest_bronze_month(spark, runtime, config, request)

    assert isinstance(result, BronzeIngestionResult)
    assert result.is_valid()


def test_ingest_row_count_matches_source(spark, tmp_path) -> None:
    """The result row count matches the synthetic source file."""
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    config, request = _stage_synthetic_source(tmp_path)

    result = ingest_bronze_month(spark, runtime, config, request)

    assert result.row_count == _ROW_COUNT


def test_ingest_returns_cache_hit(spark, tmp_path) -> None:
    """Pre-staged source file produces a cache hit."""
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    config, request = _stage_synthetic_source(tmp_path)

    result = ingest_bronze_month(spark, runtime, config, request)

    assert result.source.cache_hit is True


# --- Schema validation fires on real contract -------------------------------


def test_ingest_validates_against_real_contract_schema(spark, tmp_path) -> None:
    """The real 19-field contract schema validates the 19-column synthetic parquet."""
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    config, request = _stage_synthetic_source(tmp_path)

    # If the synthetic fixture drifts from the contract, this raises
    # InvalidRequestError from validate_against_bronze_schema at step 4.
    result = ingest_bronze_month(spark, runtime, config, request)

    assert result.row_count == _ROW_COUNT


# --- Partition layout -------------------------------------------------------


def test_ingest_writes_partition_directory(spark, tmp_path) -> None:
    """The ingestion creates a Hive-style partition directory containing parquet files."""
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    config, request = _stage_synthetic_source(tmp_path)

    result = ingest_bronze_month(spark, runtime, config, request)

    assert result.bronze_partition_path.exists()
    assert result.bronze_partition_path.is_dir()
    parquet_files = list(result.bronze_partition_path.glob("*.parquet"))
    assert len(parquet_files) > 0


def test_ingest_partition_path_uses_hive_layout(spark, tmp_path) -> None:
    """The partition path follows the cab_type/year/month Hive directory layout."""
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    config, request = _stage_synthetic_source(tmp_path)

    result = ingest_bronze_month(spark, runtime, config, request)

    parts = result.bronze_partition_path.parts
    assert "cab_type=yellow" in parts
    assert "year=2023" in parts
    assert "month=1" in parts


def test_ingest_partition_lives_under_bronze_root(spark, tmp_path) -> None:
    """The partition directory nests under the runtime config's Bronze root."""
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    config, request = _stage_synthetic_source(tmp_path)

    result = ingest_bronze_month(spark, runtime, config, request)

    assert str(result.bronze_partition_path).startswith(str(runtime.paths.bronze))


# --- Result traceability ----------------------------------------------------


def test_ingest_result_traces_to_request(spark, tmp_path) -> None:
    """The result's request field is the original request object."""
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    config, request = _stage_synthetic_source(tmp_path)

    result = ingest_bronze_month(spark, runtime, config, request)

    assert result.request is request


def test_ingest_result_source_url_matches_contract(spark, tmp_path) -> None:
    """The result's source URL matches the contract's canonical URL."""
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    config, request = _stage_synthetic_source(tmp_path)

    result = ingest_bronze_month(spark, runtime, config, request)

    assert result.source.source_url.endswith("yellow_tripdata_2023-01.parquet")
    assert "cloudfront.net" in result.source.source_url


# --- Idempotent re-execution ------------------------------------------------


def test_ingest_idempotent_reexecution(spark, tmp_path) -> None:
    """Running the same ingestion twice produces the same result without error."""
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    config, request = _stage_synthetic_source(tmp_path)

    first = ingest_bronze_month(spark, runtime, config, request)
    second = ingest_bronze_month(spark, runtime, config, request)

    assert first.row_count == second.row_count
    assert second.bronze_partition_path.exists()

    # Prove the partition was overwritten, not appended to.
    written_count = spark.read.parquet(str(second.bronze_partition_path)).count()
    assert written_count == _ROW_COUNT
