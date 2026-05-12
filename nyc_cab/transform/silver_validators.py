"""
Apply domain constraints and tag rejection reasons for the Silver layer.

This module owns the logic that evaluates each row against the Silver v1
domain constraints and tags it with an array of rejection reasons. A row
with an empty array passes all constraints and is accepted; a row with
any entries is rejected.

Constraints are applied in two phases:

    Pre-normalization: fires on Bronze-typed columns before type casts.
    Catches non-integral doubles in normalization targets and nulls in
    fields that downstream constraints depend on.

    Post-normalization: fires on Silver-typed columns after type casts.
    Enforces domain-level business rules on clean, normalized data.

Both phases write to the same ``_rejection_reasons`` array column.
Normalization (type casting) happens between the two phases.

Module Constraints
------------------

    - Domain constraints are defined in the contract; this module
      implements the Spark expressions that evaluate them.
    - The rejection column name is ``_rejection_reasons``
      (from ``SILVER_REJECTION_COLUMN``).
    - Each constraint appends its ``RejectionReason.value`` to the array
      when the constraint is violated.
    - The split function separates accepted from rejected based on
      array emptiness.
"""

from pyspark.sql import DataFrame


def apply_pre_normalization_constraints(df: DataFrame) -> DataFrame:
    """Tag rows violating pre-normalization constraints.

    Initializes the ``_rejection_reasons`` array column and checks for:

    - Non-integral values in normalization target columns
      (``passenger_count``, ``RatecodeID``). A value that is not equal
      to its floor is tagged; this catches values like ``1.5`` that
      would be silently truncated by ``cast("int")``.
    - Null values in constraint-checked fields (``fare_amount``,
      ``trip_distance``, ``passenger_count``, ``tpep_pickup_datetime``,
      ``tpep_dropoff_datetime``). Explicit null rejection prevents
      Spark's three-valued logic from silently passing null rows
      through downstream ``>=`` comparisons.

    This function must be called BEFORE ``apply_type_normalizations``
    because the integrality check requires the original double values.
    """
    raise NotImplementedError


def apply_post_normalization_constraints(df: DataFrame) -> DataFrame:
    """Tag rows violating post-normalization domain constraints.

    Appends to the existing ``_rejection_reasons`` array column
    (initialized by :func:`apply_pre_normalization_constraints`).
    Checks on Silver-typed (normalized) columns:

    - ``fare_amount >= 0``
    - ``trip_distance >= 0``
    - ``passenger_count`` between 0 and 9 inclusive
    - ``tpep_pickup_datetime < tpep_dropoff_datetime``

    This function must be called AFTER ``apply_type_normalizations``
    so that ``passenger_count`` is already cast to int.
    """
    raise NotImplementedError


def split_accepted_rejected(df: DataFrame) -> tuple[DataFrame, DataFrame]:
    """Split a tagged DataFrame into accepted and rejected partitions.

    Returns ``(accepted_df, rejected_df)`` where:

    - ``accepted_df`` contains rows with an empty ``_rejection_reasons``
      array. The rejection column is dropped from the output.
    - ``rejected_df`` contains rows with a non-empty ``_rejection_reasons``
      array. The rejection column is retained.

    The caller is responsible for ensuring the input DataFrame has been
    tagged by both :func:`apply_pre_normalization_constraints` and
    :func:`apply_post_normalization_constraints`.
    """
    raise NotImplementedError
