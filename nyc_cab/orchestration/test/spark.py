"""Tests for :mod:`nyc_cab.orchestration.spark`.

Both functions in this module are stubs awaiting implementation. The tests
confirm that each raises :class:`NotImplementedError` when called with
well-formed configuration objects, locking the signature against drift.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nyc_cab.config import load_config
from nyc_cab.orchestration.spark import apply_spark_config, build_spark_session
from nyc_cab.spark_config import load_spark_config


def test_build_spark_session_raises_not_implemented(tmp_path: Path) -> None:
    """``build_spark_session`` is a stub awaiting implementation."""
    runtime = load_config({"NYC_CAB_DATA_ROOT": str(tmp_path)})
    spark = load_spark_config({})
    with pytest.raises(NotImplementedError):
        build_spark_session(runtime, spark)


def test_apply_spark_config_raises_not_implemented() -> None:
    """``apply_spark_config`` is a stub awaiting implementation."""
    spark = load_spark_config({})
    # The stub raises before consulting the builder, so ``None`` is acceptable.
    with pytest.raises(NotImplementedError):
        apply_spark_config(None, spark)  # type: ignore[arg-type]
