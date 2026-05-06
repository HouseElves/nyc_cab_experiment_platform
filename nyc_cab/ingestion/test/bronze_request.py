"""Tests for :mod:`nyc_cab.ingestion.bronze_request`.

These tests cover the two typed request objects:

* :class:`BronzeIngestionConfig` — process-time settings for cache and download
* :class:`BronzeIngestionRequest` — invocation-time slice identifier

Type-check failures, structural-tier rejections, and the ``period_id`` property
are exercised explicitly. The semantic-tier "is this slice supported?" check
lives on the contract; tests for that live in
:mod:`nyc_cab.contracts.test.bronze`.
"""

from __future__ import annotations

import dataclasses

from pathlib import Path

import pytest

from nyc_cab.exceptions import InvalidRequestError
from nyc_cab.ingestion.bronze_request import (
    BronzeIngestionConfig,
    BronzeIngestionRequest,
)


# --- BronzeIngestionConfig: happy paths -------------------------------------


def test_config_create_validated_happy_path(tmp_path: Path) -> None:
    """A well-formed configuration constructs cleanly."""
    config = BronzeIngestionConfig.create_validated(tmp_path, 10, 60)
    assert config.source_cache_directory == tmp_path
    assert config.source_cache_max_files == 10
    assert config.source_download_timeout_seconds == 60


def test_config_accepts_zero_max_files(tmp_path: Path) -> None:
    """Zero ``source_cache_max_files`` is a valid (cache-disabled) configuration."""
    config = BronzeIngestionConfig.create_validated(tmp_path, 0, 60)
    assert config.source_cache_max_files == 0


def test_config_accepts_zero_timeout(tmp_path: Path) -> None:
    """Zero ``source_download_timeout_seconds`` is structurally valid."""
    config = BronzeIngestionConfig.create_validated(tmp_path, 10, 0)
    assert config.source_download_timeout_seconds == 0


def test_config_accepts_nonexistent_cache_directory(tmp_path: Path) -> None:
    """A non-existent cache directory is structurally valid (created lazily)."""
    nonexistent = tmp_path / "not-yet-here"
    config = BronzeIngestionConfig.create_validated(nonexistent, 10, 60)
    assert config.source_cache_directory == nonexistent


# --- BronzeIngestionConfig: type-check rejections ---------------------------


def test_config_rejects_non_path_cache_directory() -> None:
    """``source_cache_directory`` must be a ``Path``; strings are rejected."""
    with pytest.raises(InvalidRequestError) as info:
        BronzeIngestionConfig.create_validated("/tmp/cache", 10, 60)
    names = [v[0] for v in info.value.violations]
    assert "source_cache_directory" in names


def test_config_rejects_bool_max_files(tmp_path: Path) -> None:
    """``source_cache_max_files`` rejects ``True``/``False`` despite int compatibility."""
    with pytest.raises(InvalidRequestError) as info:
        BronzeIngestionConfig.create_validated(tmp_path, True, 60)
    assert ("source_cache_max_files", True) in info.value.violations


def test_config_rejects_bool_timeout(tmp_path: Path) -> None:
    """``source_download_timeout_seconds`` rejects ``True``/``False``."""
    with pytest.raises(InvalidRequestError) as info:
        BronzeIngestionConfig.create_validated(tmp_path, 10, False)
    assert ("source_download_timeout_seconds", False) in info.value.violations


# --- BronzeIngestionConfig: structural rejections ---------------------------


def test_config_rejects_negative_max_files(tmp_path: Path) -> None:
    """Negative ``source_cache_max_files`` violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        BronzeIngestionConfig.create_validated(tmp_path, -1, 60)
    assert ("source_cache_max_files", -1) in info.value.violations


def test_config_rejects_negative_timeout(tmp_path: Path) -> None:
    """Negative ``source_download_timeout_seconds`` violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        BronzeIngestionConfig.create_validated(tmp_path, 10, -1)
    assert ("source_download_timeout_seconds", -1) in info.value.violations


def test_config_rejects_file_as_cache_directory(tmp_path: Path) -> None:
    """A path that exists as a regular file violates the directory rule."""
    file_path = tmp_path / "not-a-directory"
    file_path.write_text("data")
    with pytest.raises(InvalidRequestError) as info:
        BronzeIngestionConfig.create_validated(file_path, 10, 60)
    assert ("source_cache_directory", file_path) in info.value.violations


# --- BronzeIngestionRequest: happy paths ------------------------------------


def test_request_create_validated_happy_path() -> None:
    """A well-formed request constructs cleanly."""
    request = BronzeIngestionRequest.create_validated("yellow", 2023, 1)
    assert request.cab_type == "yellow"
    assert request.year == 2023
    assert request.month == 1


def test_request_accepts_year_at_lower_bound() -> None:
    """Year 1970 is the structural lower bound and is accepted."""
    request = BronzeIngestionRequest.create_validated("yellow", 1970, 6)
    assert request.year == 1970


def test_request_accepts_year_at_upper_bound() -> None:
    """Year 2050 is the structural upper bound and is accepted."""
    request = BronzeIngestionRequest.create_validated("yellow", 2050, 6)
    assert request.year == 2050


def test_request_accepts_month_at_lower_bound() -> None:
    """Month 1 is accepted."""
    request = BronzeIngestionRequest.create_validated("yellow", 2023, 1)
    assert request.month == 1


def test_request_accepts_month_at_upper_bound() -> None:
    """Month 12 is accepted."""
    request = BronzeIngestionRequest.create_validated("yellow", 2023, 12)
    assert request.month == 12


def test_request_accepts_arbitrary_cab_type_strings() -> None:
    """Any non-blank cab_type string passes structural validation."""
    # Structural validation does not enforce contract support; that's tier 3.
    request = BronzeIngestionRequest.create_validated("green", 2023, 1)
    assert request.cab_type == "green"


# --- BronzeIngestionRequest: type-check rejections --------------------------


def test_request_rejects_non_string_cab_type() -> None:
    """``cab_type`` must be a string."""
    with pytest.raises(InvalidRequestError) as info:
        BronzeIngestionRequest.create_validated(123, 2023, 1)
    assert ("cab_type", 123) in info.value.violations


def test_request_rejects_string_year() -> None:
    """``year`` must be an int."""
    with pytest.raises(InvalidRequestError) as info:
        BronzeIngestionRequest.create_validated("yellow", "2023", 1)
    assert ("year", "2023") in info.value.violations


def test_request_rejects_bool_year() -> None:
    """``year`` rejects ``True``/``False`` despite int compatibility."""
    with pytest.raises(InvalidRequestError) as info:
        BronzeIngestionRequest.create_validated("yellow", True, 1)
    assert ("year", True) in info.value.violations


def test_request_rejects_bool_month() -> None:
    """``month`` rejects ``True``/``False`` despite int compatibility."""
    with pytest.raises(InvalidRequestError) as info:
        BronzeIngestionRequest.create_validated("yellow", 2023, False)
    assert ("month", False) in info.value.violations


# --- BronzeIngestionRequest: structural rejections --------------------------


def test_request_rejects_blank_cab_type() -> None:
    """A whitespace-only ``cab_type`` violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        BronzeIngestionRequest.create_validated("   ", 2023, 1)
    assert ("cab_type", "   ") in info.value.violations


def test_request_rejects_year_below_lower_bound() -> None:
    """Year 1969 is below the structural lower bound."""
    with pytest.raises(InvalidRequestError) as info:
        BronzeIngestionRequest.create_validated("yellow", 1969, 1)
    assert ("year", 1969) in info.value.violations


def test_request_rejects_year_above_upper_bound() -> None:
    """Year 2051 is above the structural upper bound."""
    with pytest.raises(InvalidRequestError) as info:
        BronzeIngestionRequest.create_validated("yellow", 2051, 1)
    assert ("year", 2051) in info.value.violations


def test_request_rejects_month_below_lower_bound() -> None:
    """Month 0 is below the structural lower bound."""
    with pytest.raises(InvalidRequestError) as info:
        BronzeIngestionRequest.create_validated("yellow", 2023, 0)
    assert ("month", 0) in info.value.violations


def test_request_rejects_month_above_upper_bound() -> None:
    """Month 13 is above the structural upper bound."""
    with pytest.raises(InvalidRequestError) as info:
        BronzeIngestionRequest.create_validated("yellow", 2023, 13)
    assert ("month", 13) in info.value.violations


def test_request_aggregates_multiple_structural_failures() -> None:
    """Two structural failures aggregate into a single exception."""
    with pytest.raises(InvalidRequestError) as info:
        BronzeIngestionRequest.create_validated("yellow", 1900, 99)
    names = [v[0] for v in info.value.violations]
    assert "year" in names
    assert "month" in names


# --- BronzeIngestionRequest: period_id --------------------------------------


def test_request_period_id_zero_pads_single_digit_month() -> None:
    """``period_id`` produces the canonical zero-padded YYYY-MM string."""
    request = BronzeIngestionRequest.create_validated("yellow", 2023, 1)
    assert request.period_id == "2023-01"


def test_request_period_id_handles_two_digit_month() -> None:
    """``period_id`` passes two-digit months through unchanged."""
    request = BronzeIngestionRequest.create_validated("yellow", 2023, 12)
    assert request.period_id == "2023-12"


# --- Frozenness -------------------------------------------------------------


def test_config_is_frozen(tmp_path: Path) -> None:
    """:class:`BronzeIngestionConfig` rejects attribute mutation."""

    config = BronzeIngestionConfig.create_validated(tmp_path, 10, 60)
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.source_cache_max_files = 99  # type: ignore[misc]


def test_request_is_frozen() -> None:
    """:class:`BronzeIngestionRequest` rejects attribute mutation."""

    request = BronzeIngestionRequest.create_validated("yellow", 2023, 1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        request.month = 2  # type: ignore[misc]
