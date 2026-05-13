"""
Silver transformation orchestration entry point.

This module owns the top-level transformation function and the result type.
Contract facts, normalization transforms, and domain validators live in
their respective sibling modules; see the transformation package
``__init__`` for the full module layout.

The transformation flow reads from an existing Bronze partition, normalizes
types, applies domain constraints in two phases (pre- and post-normalization),
and writes accepted and rejected records to separate Silver partitions. The
reconciliation invariant is enforced structurally:
``bronze_count == accepted_count + rejected_count``.

Class Relationships
-------------------

.. mermaid::

    classDiagram

        dataclass <|-- SilverTransformResult
        _Validated <|-- SilverTransformResult

        class SilverTransformResult {
            <<immutable>>
            integer bronze_count
            integer accepted_count
            integer rejected_count
            }
        SilverTransformResult *-- SilverTransformRequest : request
        SilverTransformResult *-- Path : silver_partition_path
        SilverTransformResult *-- Path : silver_rejected_partition_path
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

from nyc_cab._validation import _Validated, CheckSpec, CheckTuple
from nyc_cab.config import RuntimeConfig
from nyc_cab.contracts.silver import (
    SILVER_PARTITION_COLUMNS,
    SILVER_REJECTED_LAYER_NAME,
    validate_supported_silver_slice,
)
from nyc_cab.transform.silver_request import SilverTransformRequest
from nyc_cab.transform.silver_transforms import apply_type_normalizations
from nyc_cab.transform.silver_validators import (
    apply_post_normalization_constraints,
    apply_pre_normalization_constraints,
    split_accepted_rejected,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SilverTransformResult(_Validated):
    """
    Describe the result of a Silver transformation run.

    The result carries the original request, both output partition paths,
    and the three counts that satisfy the reconciliation invariant:
    ``bronze_count == accepted_count + rejected_count``.
    """

    request: SilverTransformRequest
    silver_partition_path: Path
    silver_rejected_partition_path: Path
    bronze_count: int
    accepted_count: int
    rejected_count: int

    _type_check_specs: ClassVar[tuple[CheckSpec, ...]] = (
        ("request", SilverTransformRequest),
        ("silver_partition_path", Path),
        ("silver_rejected_partition_path", Path),
        ("bronze_count", int, bool),
        ("accepted_count", int, bool),
        ("rejected_count", int, bool),
    )

    def _structural_checks(self) -> tuple[CheckTuple, ...]:
        """
        Provide structural validation rules for SilverTransformResult.

        The reconciliation invariant (bronze_count == accepted_count +
        rejected_count) is enforced here as a structural check. A result
        that violates this invariant cannot be constructed via
        ``create_validated``.
        """
        return (
            self.request.validity_check("request"),
            (
                not self.silver_partition_path.exists() or self.silver_partition_path.is_dir(),
                "silver_partition_path",
                self.silver_partition_path,
            ),
            (
                not self.silver_rejected_partition_path.exists() or self.silver_rejected_partition_path.is_dir(),
                "silver_rejected_partition_path",
                self.silver_rejected_partition_path,
            ),
            (self.bronze_count >= 0, "bronze_count", self.bronze_count),
            (self.accepted_count >= 0, "accepted_count", self.accepted_count),
            (self.rejected_count >= 0, "rejected_count", self.rejected_count),
            (
                self.bronze_count == self.accepted_count + self.rejected_count,
                "reconciliation",
                f"bronze={self.bronze_count} != accepted={self.accepted_count} + rejected={self.rejected_count}",
            ),
        )


def _derive_silver_partition_path(runtime_config: RuntimeConfig, request: SilverTransformRequest) -> Path:
    """Return the Silver accepted partition directory for a transform request."""
    return (
        runtime_config.paths.silver
        / f"cab_type={request.cab_type}"
        / f"year={request.year}"
        / f"month={request.month}"
    )


def _derive_silver_rejected_partition_path(runtime_config: RuntimeConfig, request: SilverTransformRequest) -> Path:
    """Return the Silver rejected partition directory for a transform request."""
    return (
        runtime_config.paths.data_root
        / SILVER_REJECTED_LAYER_NAME
        / f"cab_type={request.cab_type}"
        / f"year={request.year}"
        / f"month={request.month}"
    )


def transform_silver_month(
    spark: SparkSession,
    runtime_config: RuntimeConfig,
    request: SilverTransformRequest,
) -> SilverTransformResult:
    """
    Transform one monthly Bronze partition into Silver accepted and rejected outputs.

    The flow is:

        1. Validate the request against the Silver contract.
        2. Read the Bronze partition for ``(cab_type, year, month)``.
        3. Count Bronze rows (anchors the reconciliation invariant).
        4. Apply pre-normalization constraints: tag non-integral doubles
           in normalization targets and nulls in constraint-checked fields.
        5. Normalize types (double to int for ``passenger_count``,
           ``RatecodeID``). Safe because non-integral values are already
           tagged for rejection.
        6. Apply post-normalization domain constraints (negative fare,
           negative distance, pickup after dropoff, invalid passenger
           count).
        7. Split into accepted (empty rejection array) and rejected
           (non-empty rejection array).
        8. Write accepted to ``silver/`` partition (rejection column
           dropped).
        9. Write rejected to ``silver_rejected/`` partition (rejection
           column retained).
        10. Count accepted and rejected rows.
        11. Return result with reconciliation invariant enforced.

    Re-execution is safe under dynamic partition overwrite semantics.
    """
    # Step 1: validate the request against the Silver v1 contract.
    # Structural checks already fired at request construction time; this is
    # the Tier 3 semantic check (supported cab type and period).
    logger.info("Validating Silver slice: cab_type=%s period=%s", request.cab_type, request.period_id)
    validate_supported_silver_slice(request.cab_type, request.year, request.month)

    # Step 2: read Bronze partition.
    # The partition path is the leaf directory; partition column values
    # (cab_type, year, month) are not stored in the parquet files themselves
    # and will be added as literals before writing Silver output.
    bronze_partition_path = (
        runtime_config.paths.bronze
        / f"cab_type={request.cab_type}"
        / f"year={request.year}"
        / f"month={request.month}"
    )
    logger.info("Reading Bronze partition: %s", bronze_partition_path)
    df = spark.read.parquet(str(bronze_partition_path))

    # Step 3: count Bronze rows to anchor the reconciliation invariant.
    bronze_count = df.count()
    logger.info("Bronze row count: %d", bronze_count)

    # Step 4: pre-normalization constraints.
    # Tags non-integral normalization targets and nulls in constraint-checked
    # fields before type casts alter the values.
    logger.info("Applying pre-normalization constraints")
    df = apply_pre_normalization_constraints(df)

    # Step 5: type normalizations.
    # Safe to cast now: non-integral values in normalization targets are
    # already tagged for rejection.
    logger.info("Applying type normalizations")
    df = apply_type_normalizations(df)

    # Step 6: post-normalization domain constraints.
    # Fires on Silver-typed columns; passenger_count is now int.
    logger.info("Applying post-normalization constraints")
    df = apply_post_normalization_constraints(df)

    # Step 7: split accepted and rejected rows.
    logger.info("Splitting accepted and rejected rows")
    accepted_df, rejected_df = split_accepted_rejected(df)

    # Step 8: write accepted partition to silver/.
    # Add partition columns as literals; partitionBy encodes them in the
    # directory structure and excludes them from the parquet file data.
    silver_root = runtime_config.paths.silver
    logger.info("Writing accepted Silver partition: root=%s cab_type=%s year=%d month=%d",
                silver_root, request.cab_type, request.year, request.month)
    (
        accepted_df
        .withColumn("cab_type", F.lit(request.cab_type))
        .withColumn("year", F.lit(request.year))
        .withColumn("month", F.lit(request.month))
        .write
        .mode("overwrite")
        .partitionBy(*SILVER_PARTITION_COLUMNS)
        .parquet(str(silver_root))
    )

    # Step 9: write rejected partition to silver_rejected/.
    silver_rejected_root = runtime_config.paths.data_root / SILVER_REJECTED_LAYER_NAME
    logger.info("Writing rejected Silver partition: root=%s cab_type=%s year=%d month=%d",
                silver_rejected_root, request.cab_type, request.year, request.month)
    (
        rejected_df
        .withColumn("cab_type", F.lit(request.cab_type))
        .withColumn("year", F.lit(request.year))
        .withColumn("month", F.lit(request.month))
        .write
        .mode("overwrite")
        .partitionBy(*SILVER_PARTITION_COLUMNS)
        .parquet(str(silver_rejected_root))
    )

    # Step 10: count accepted and rejected rows.
    accepted_count = accepted_df.count()
    rejected_count = rejected_df.count()
    logger.info("Silver transform complete: accepted=%d rejected=%d bronze=%d",
                accepted_count, rejected_count, bronze_count)

    # Step 11: return result with reconciliation invariant enforced.
    silver_partition_path = _derive_silver_partition_path(runtime_config, request)
    silver_rejected_partition_path = _derive_silver_rejected_partition_path(runtime_config, request)
    return SilverTransformResult.create_validated(
        request,
        silver_partition_path,
        silver_rejected_partition_path,
        bronze_count,
        accepted_count,
        rejected_count,
    )
