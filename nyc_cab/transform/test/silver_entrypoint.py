# pylint: disable=redefined-outer-name
"""Tests for :mod:`nyc_cab.transform.silver_entrypoint`.

These tests cover:

* :class:`SilverTransformResult` -- the post-transformation result, including
  the reconciliation invariant (``bronze_count == accepted_count +
  rejected_count``).
* :func:`transform_silver_month` -- stub verification.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from nyc_cab.config import load_config
from nyc_cab.exceptions import InvalidRequestError
from nyc_cab.transform.silver_entrypoint import SilverTransformResult, transform_silver_month
from nyc_cab.transform.silver_request import SilverTransformRequest


def _request() -> SilverTransformRequest:
    """Build a valid Silver transform request for tests."""
    return SilverTransformRequest.create_validated("yellow", 2023, 1)


# --- SilverTransformResult: happy paths -------------------------------------


def test_result_create_validated_happy_path(tmp_path: Path) -> None:
    """A well-formed result with consistent counts constructs cleanly."""
    silver = tmp_path / "silver"
    silver.mkdir()
    rejected = tmp_path / "silver_rejected"
    rejected.mkdir()
    result = SilverTransformResult.create_validated(
        _request(), silver, rejected, 1000, 950, 50,
    )
    assert result.bronze_count == 1000
    assert result.accepted_count == 950
    assert result.rejected_count == 50
    assert result.is_valid()


def test_result_accepts_zero_rejected(tmp_path: Path) -> None:
    """Zero rejected rows is valid (all rows accepted)."""
    silver = tmp_path / "silver"
    silver.mkdir()
    rejected = tmp_path / "rejected"
    rejected.mkdir()
    result = SilverTransformResult.create_validated(
        _request(), silver, rejected, 500, 500, 0,
    )
    assert result.rejected_count == 0


def test_result_accepts_all_rejected(tmp_path: Path) -> None:
    """All rows rejected is valid (zero accepted)."""
    silver = tmp_path / "silver"
    silver.mkdir()
    rejected = tmp_path / "rejected"
    rejected.mkdir()
    result = SilverTransformResult.create_validated(
        _request(), silver, rejected, 500, 0, 500,
    )
    assert result.accepted_count == 0


def test_result_accepts_nonexistent_partition_paths(tmp_path: Path) -> None:
    """Partition directories that don't exist yet are structurally valid."""
    silver = tmp_path / "silver" / "not_yet"
    rejected = tmp_path / "rejected" / "not_yet"
    result = SilverTransformResult.create_validated(
        _request(), silver, rejected, 100, 90, 10,
    )
    assert not result.silver_partition_path.exists()
    assert not result.silver_rejected_partition_path.exists()


# --- SilverTransformResult: type-check rejections ---------------------------


def test_result_rejects_non_request(tmp_path: Path) -> None:
    """``request`` must be a SilverTransformRequest."""
    with pytest.raises(InvalidRequestError) as info:
        SilverTransformResult.create_validated(
            "not-a-request", tmp_path, tmp_path, 100, 90, 10,
        )
    names = [v[0] for v in info.value.violations]
    assert "request" in names


def test_result_rejects_string_silver_path(tmp_path: Path) -> None:
    """``silver_partition_path`` must be a Path."""
    with pytest.raises(InvalidRequestError) as info:
        SilverTransformResult.create_validated(
            _request(), "/tmp/silver", tmp_path, 100, 90, 10,
        )
    names = [v[0] for v in info.value.violations]
    assert "silver_partition_path" in names


def test_result_rejects_bool_bronze_count(tmp_path: Path) -> None:
    """``bronze_count`` rejects bool despite int compatibility."""
    with pytest.raises(InvalidRequestError) as info:
        SilverTransformResult.create_validated(
            _request(), tmp_path, tmp_path, True, 0, 0,
        )
    assert ("bronze_count", True) in info.value.violations


# --- SilverTransformResult: structural rejections ---------------------------


def test_result_rejects_negative_bronze_count(tmp_path: Path) -> None:
    """Negative bronze_count violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        SilverTransformResult.create_validated(
            _request(), tmp_path, tmp_path, -1, 0, 0,
        )
    names = [v[0] for v in info.value.violations]
    assert "bronze_count" in names


def test_result_rejects_negative_accepted_count(tmp_path: Path) -> None:
    """Negative accepted_count violates the structural rule."""
    with pytest.raises(InvalidRequestError) as info:
        SilverTransformResult.create_validated(
            _request(), tmp_path, tmp_path, 100, -1, 101,
        )
    names = [v[0] for v in info.value.violations]
    assert "accepted_count" in names


def test_result_rejects_file_as_silver_path(tmp_path: Path) -> None:
    """A regular file at the silver path violates the directory rule."""
    file_path = tmp_path / "not-a-dir"
    file_path.write_text("data")
    with pytest.raises(InvalidRequestError) as info:
        SilverTransformResult.create_validated(
            _request(), file_path, tmp_path, 100, 90, 10,
        )
    assert ("silver_partition_path", file_path) in info.value.violations


# --- Reconciliation invariant -----------------------------------------------


def test_result_rejects_inconsistent_counts(tmp_path: Path) -> None:
    """bronze_count != accepted + rejected violates the reconciliation invariant."""
    with pytest.raises(InvalidRequestError) as info:
        SilverTransformResult.create_validated(
            _request(), tmp_path, tmp_path, 100, 90, 20,
        )
    names = [v[0] for v in info.value.violations]
    assert "reconciliation" in names


def test_result_rejects_overcounted_accepted(tmp_path: Path) -> None:
    """accepted_count exceeding bronze_count violates reconciliation."""
    with pytest.raises(InvalidRequestError) as info:
        SilverTransformResult.create_validated(
            _request(), tmp_path, tmp_path, 100, 101, 0,
        )
    names = [v[0] for v in info.value.violations]
    assert "reconciliation" in names


def test_reconciliation_passes_at_zero(tmp_path: Path) -> None:
    """Zero bronze, zero accepted, zero rejected satisfies the invariant."""
    result = SilverTransformResult.create_validated(
        _request(), tmp_path, tmp_path, 0, 0, 0,
    )
    assert result.bronze_count == 0


# --- validity_check chaining ------------------------------------------------


def test_result_chaining_catches_invalid_request(tmp_path: Path) -> None:
    """A structurally-bad request bubbles up as a request violation."""
    bad_request = SilverTransformRequest("yellow", 2023, 13)
    with pytest.raises(InvalidRequestError) as info:
        SilverTransformResult.create_validated(
            bad_request, tmp_path, tmp_path, 100, 90, 10,
        )
    names = [v[0] for v in info.value.violations]
    assert "request" in names


# --- Frozenness -------------------------------------------------------------


def test_result_is_frozen(tmp_path: Path) -> None:
    """``SilverTransformResult`` rejects attribute mutation."""
    result = SilverTransformResult.create_validated(
        _request(), tmp_path, tmp_path, 100, 90, 10,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.bronze_count = 200  # type: ignore[misc]


# --- transform_silver_month stub -------------------------------------------


def test_transform_silver_month_raises_not_implemented(tmp_path: Path) -> None:
    """The stub raises NotImplementedError until implemented."""
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    request = _request()
    with pytest.raises(NotImplementedError):
        transform_silver_month(None, runtime, request)  # type: ignore[arg-type]
