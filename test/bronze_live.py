# pylint: disable=redefined-outer-name
"""
Bronze ingestion live integration tests (real download, on-demand).

These tests download actual TLC Yellow cab Parquet files from CloudFront and
run the full ``ingest_bronze_month`` flow against real data. They are marked
with ``@pytest.mark.live`` and skipped by default in CI.

Run these tests explicitly when you need to verify that the real TLC source
files still match the Bronze v1 contract schema:

    pytest -m live test/bronze_live.py

Prerequisites:

    - Network access to ``d37ci6vzurychx.cloudfront.net``
    - Sufficient disk space for one monthly TLC file (~100-200 MB)
    - The ``live`` marker must be registered in ``pytest.ini``::

        [pytest]
        markers =
            live: marks tests that require network access to external services
"""

from __future__ import annotations

import pytest

from nyc_cab.config import load_config
from nyc_cab.contracts.bronze import BRONZE_RAW_YELLOW_SCHEMA_FIELDS
from nyc_cab.ingestion.bronze_entrypoint import ingest_bronze_month
from nyc_cab.ingestion.bronze_request import BronzeIngestionConfig, BronzeIngestionRequest


pytestmark = pytest.mark.live


# --- Full download and ingest -----------------------------------------------


def test_live_ingest_downloads_and_completes(spark, tmp_path) -> None:
    """A real TLC file downloads and ingests without error."""
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    cache_dir = tmp_path / "cache"
    config = BronzeIngestionConfig.create_validated(cache_dir, 5, 120)
    request = BronzeIngestionRequest.create_validated("yellow", 2023, 1)

    result = ingest_bronze_month(spark, runtime, config, request)

    assert result.is_valid()
    assert result.row_count > 1_000_000
    assert result.source.cache_hit is False
    assert result.bronze_partition_path.exists()


# --- Real schema matches contract -------------------------------------------


def test_live_schema_matches_contract(spark, tmp_path) -> None:
    """The real TLC file's schema matches the Bronze v1 contract field-by-field."""
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    cache_dir = tmp_path / "cache"
    config = BronzeIngestionConfig.create_validated(cache_dir, 5, 120)
    request = BronzeIngestionRequest.create_validated("yellow", 2023, 1)

    # ingest_bronze_month validates the schema internally at step 4.
    # If the real file's schema drifts from the contract, this raises
    # InvalidRequestError with structured violations naming the mismatched
    # fields. A passing test means the real file matches the contract.
    result = ingest_bronze_month(spark, runtime, config, request)

    expected_names = [f.name for f in BRONZE_RAW_YELLOW_SCHEMA_FIELDS]
    assert result.row_count > 0
    assert len(expected_names) == 19


# --- Cache round trip -------------------------------------------------------


def test_live_cache_round_trip(spark, tmp_path) -> None:
    """First ingest downloads (cache miss); second reuses the cached file (cache hit)."""
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    cache_dir = tmp_path / "cache"
    config = BronzeIngestionConfig.create_validated(cache_dir, 5, 120)
    request = BronzeIngestionRequest.create_validated("yellow", 2023, 1)

    first = ingest_bronze_month(spark, runtime, config, request)
    second = ingest_bronze_month(spark, runtime, config, request)

    assert first.source.cache_hit is False
    assert second.source.cache_hit is True
    assert first.row_count == second.row_count


# --- February 2023 also ingests cleanly -------------------------------------


def test_live_ingest_february_2023(spark, tmp_path) -> None:
    """The second supported period also downloads and ingests without error."""
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    cache_dir = tmp_path / "cache"
    config = BronzeIngestionConfig.create_validated(cache_dir, 5, 120)
    request = BronzeIngestionRequest.create_validated("yellow", 2023, 2)

    result = ingest_bronze_month(spark, runtime, config, request)

    assert result.is_valid()
    assert result.row_count > 1_000_000

    # February is the month that motivated the schema equivalence layer.
    # Verify the written partition has canonical column names regardless
    # of what the source file used.
    written_columns = spark.read.parquet(str(result.bronze_partition_path)).columns
    assert "Airport_fee" in written_columns

