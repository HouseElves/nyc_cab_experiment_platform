"""Tests for :mod:`nyc_cab.transform.silver_validators`."""

from __future__ import annotations

import pytest

from nyc_cab.transform.silver_validators import (
    apply_post_normalization_constraints,
    apply_pre_normalization_constraints,
    split_accepted_rejected,
)


def test_apply_pre_normalization_constraints_raises_not_implemented() -> None:
    """The stub raises NotImplementedError until implemented."""
    with pytest.raises(NotImplementedError):
        apply_pre_normalization_constraints(None)  # type: ignore[arg-type]


def test_apply_post_normalization_constraints_raises_not_implemented() -> None:
    """The stub raises NotImplementedError until implemented."""
    with pytest.raises(NotImplementedError):
        apply_post_normalization_constraints(None)  # type: ignore[arg-type]


def test_split_accepted_rejected_raises_not_implemented() -> None:
    """The stub raises NotImplementedError until implemented."""
    with pytest.raises(NotImplementedError):
        split_accepted_rejected(None)  # type: ignore[arg-type]
 