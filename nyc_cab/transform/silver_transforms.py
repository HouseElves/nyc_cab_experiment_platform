"""
Apply pure column normalizations for the Silver layer.

This module owns type casts and derived-column logic. It does not make
accept/reject decisions; see ``silver_validators`` for domain constraints.

The normalizations applied are defined in the Silver contract's
``SILVER_YELLOW_TYPE_NORMALIZATIONS`` tuple. This module reads that
specification and applies the casts.

Module Constraints
------------------

    - All transforms are pure column operations (no I/O, no side effects).
    - The module reads normalization specs from the Silver contract.
    - No accept/reject logic belongs here.
"""

from pyspark.sql import DataFrame


def apply_type_normalizations(df: DataFrame) -> DataFrame:
    """Cast Bronze-typed columns to their Silver-normalized types.

    Applies the type normalizations defined in the Silver contract
    (e.g. ``passenger_count`` double → int, ``RatecodeID`` double → int).
    Returns a new DataFrame with the casts applied.
    """
    raise NotImplementedError
