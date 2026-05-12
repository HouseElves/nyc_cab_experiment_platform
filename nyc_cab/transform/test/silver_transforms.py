"""Tests for :mod:`nyc_cab.transform.silver_transforms`."""

from __future__ import annotations

import pytest

from nyc_cab.transform.silver_transforms import apply_type_normalizations


def test_apply_type_normalizations_raises_not_implemented() -> None:
    """The stub raises NotImplementedError until implemented."""
    with pytest.raises(NotImplementedError):
        apply_type_normalizations(None)  # type: ignore[arg-type]
