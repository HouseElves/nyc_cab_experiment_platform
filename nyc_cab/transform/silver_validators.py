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
from pyspark.sql import functions as F
from pyspark.sql.types import ArrayType, StringType

from nyc_cab.contracts.silver import SILVER_REJECTION_COLUMN, RejectionReason


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
    df = df.withColumn(SILVER_REJECTION_COLUMN, F.array().cast(ArrayType(StringType())))

    # Integrality checks: tag values that would be silently truncated by cast("int").
    # Spark's three-valued logic means NULL != floor(NULL) evaluates to NULL (falsy),
    # so null values are not tagged here — the null check below handles them separately.
    for col_name, reason in (
        ("passenger_count", RejectionReason.NON_INTEGRAL_PASSENGER_COUNT),
        ("RatecodeID", RejectionReason.NON_INTEGRAL_RATECODE),
    ):
        df = df.withColumn(
            SILVER_REJECTION_COLUMN,
            F.when(
                F.col(col_name) != F.floor(F.col(col_name)),
                F.array_append(F.col(SILVER_REJECTION_COLUMN), F.lit(reason.value)),
            ).otherwise(F.col(SILVER_REJECTION_COLUMN)),
        )

    # Null checks: explicit rejection prevents downstream constraints from silently
    # passing on NULL inputs under Spark's three-valued logic (NULL >= 0 → NULL).
    for col_name, reason in (
        ("fare_amount", RejectionReason.NULL_FARE_AMOUNT),
        ("trip_distance", RejectionReason.NULL_TRIP_DISTANCE),
        ("passenger_count", RejectionReason.NULL_PASSENGER_COUNT),
        ("tpep_pickup_datetime", RejectionReason.NULL_PICKUP_DATETIME),
        ("tpep_dropoff_datetime", RejectionReason.NULL_DROPOFF_DATETIME),
    ):
        df = df.withColumn(
            SILVER_REJECTION_COLUMN,
            F.when(
                F.col(col_name).isNull(),
                F.array_append(F.col(SILVER_REJECTION_COLUMN), F.lit(reason.value)),
            ).otherwise(F.col(SILVER_REJECTION_COLUMN)),
        )

    return df


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
    # Null inputs evaluated by these comparisons produce NULL (falsy in when),
    # so null rows are not additionally tagged here. Pre-normalization null
    # checks already tag them before this phase runs.
    checks = (
        (F.col("fare_amount") < 0, RejectionReason.NEGATIVE_FARE),
        (F.col("trip_distance") < 0, RejectionReason.NEGATIVE_DISTANCE),
        (
            (F.col("passenger_count") < 0) | (F.col("passenger_count") > 9),
            RejectionReason.INVALID_PASSENGER_COUNT,
        ),
        (
            F.col("tpep_pickup_datetime") >= F.col("tpep_dropoff_datetime"),
            RejectionReason.PICKUP_AFTER_DROPOFF,
        ),
    )

    for condition, reason in checks:
        df = df.withColumn(
            SILVER_REJECTION_COLUMN,
            F.when(
                condition,
                F.array_append(F.col(SILVER_REJECTION_COLUMN), F.lit(reason.value)),
            ).otherwise(F.col(SILVER_REJECTION_COLUMN)),
        )

    return df


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
    rejection_col = F.col(SILVER_REJECTION_COLUMN)
    accepted_df = (
        df.filter(F.size(rejection_col) == 0)
          .drop(SILVER_REJECTION_COLUMN)
    )
    rejected_df = df.filter(F.size(rejection_col) > 0)
    return accepted_df, rejected_df
