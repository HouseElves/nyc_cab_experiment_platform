"""
Silver-layer contract constraints for NYC Cab transformation.

This module contains stable Silver v1 contract facts: the normalized output
schema, rejection reason definitions, domain constraint metadata, and path
derivation helpers.

The Silver contract differs from Bronze in one critical way: Silver makes
decisions about the data. Bronze faithfully copies what TLC publishes;
Silver normalizes types, enforces domain constraints, and splits records
into accepted and rejected partitions. The contract defines WHAT those
decisions are; the transformation package implements HOW.

The module intentionally performs no I/O and imports no Spark symbols.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Final, TypeAlias

from nyc_cab._validation import CheckTuple, raise_on_violations
from nyc_cab.exceptions import InvalidRequestError


Period: TypeAlias = tuple[int, int]


# Supported scope (mirrors Bronze for v1)

CAB_TYPE_YELLOW: Final[str] = "yellow"

SILVER_SUPPORTED_CAB_TYPES: Final[tuple[str, ...]] = (CAB_TYPE_YELLOW,)

SILVER_SUPPORTED_PERIODS: Final[tuple[Period, ...]] = ((2023, 1), (2023, 2))

SILVER_PARTITION_COLUMNS: Final[tuple[str, ...]] = ("cab_type", "year", "month")


# Schema definition
#
# The Silver schema is the Bronze schema with two normalizations applied:
#
#   - ``passenger_count``: double → int (logically integer, stored as
#     double by TLC in recent vintages)
#   - ``RatecodeID``: double → int (same rationale)
#
# All other columns carry forward from Bronze unchanged.

@dataclass(frozen=True, slots=True)
class SilverSchemaField:
    """Describe one expected Silver output field.

    Uses the same ``simpleString()`` convention as
    :class:`~nyc_cab.contracts.bronze.BronzeSchemaField`.
    """

    name: str
    spark_type: str
    nullable: bool


SILVER_YELLOW_SCHEMA_FIELDS: Final[tuple[SilverSchemaField, ...]] = (
    SilverSchemaField("VendorID", "bigint", True),
    SilverSchemaField("tpep_pickup_datetime", "timestamp_ntz", True),
    SilverSchemaField("tpep_dropoff_datetime", "timestamp_ntz", True),
    SilverSchemaField("passenger_count", "int", True),
    SilverSchemaField("trip_distance", "double", True),
    SilverSchemaField("RatecodeID", "int", True),
    SilverSchemaField("store_and_fwd_flag", "string", True),
    SilverSchemaField("PULocationID", "bigint", True),
    SilverSchemaField("DOLocationID", "bigint", True),
    SilverSchemaField("payment_type", "bigint", True),
    SilverSchemaField("fare_amount", "double", True),
    SilverSchemaField("extra", "double", True),
    SilverSchemaField("mta_tax", "double", True),
    SilverSchemaField("tip_amount", "double", True),
    SilverSchemaField("tolls_amount", "double", True),
    SilverSchemaField("improvement_surcharge", "double", True),
    SilverSchemaField("total_amount", "double", True),
    SilverSchemaField("congestion_surcharge", "double", True),
    SilverSchemaField("Airport_fee", "double", True),
)


# Rejection reasons
#
# Each reason maps to one domain constraint. A row can accumulate multiple
# reasons in the ``_rejection_reasons`` array column. A row with an empty
# array is accepted; a row with any entries is rejected.
#
# Reasons are organized into two phases:
#
#   Pre-normalization: fire on Bronze-typed columns before type casts.
#   These catch values that would be silently corrupted by casting (e.g.
#   non-integral doubles truncated to int) and nulls in fields that
#   domain constraints depend on (Spark's three-valued logic means
#   ``NULL >= 0`` evaluates to NULL, not True or False, so a null fare
#   would silently pass the NEGATIVE_FARE check without an explicit
#   null rejection).
#
#   Post-normalization: fire on Silver-typed columns after type casts.
#   These enforce domain-level business rules on clean, normalized data.

SILVER_REJECTION_COLUMN: Final[str] = "_rejection_reasons"


class RejectionReason(enum.Enum):
    """Enumerate the domain constraints enforced at the Silver layer."""

    # Pre-normalization: integrality checks on cast targets
    NON_INTEGRAL_PASSENGER_COUNT = "non_integral_passenger_count"
    NON_INTEGRAL_RATECODE = "non_integral_ratecode"

    # Pre-normalization: null policy on constraint-checked fields
    NULL_FARE_AMOUNT = "null_fare_amount"
    NULL_TRIP_DISTANCE = "null_trip_distance"
    NULL_PASSENGER_COUNT = "null_passenger_count"
    NULL_PICKUP_DATETIME = "null_pickup_datetime"
    NULL_DROPOFF_DATETIME = "null_dropoff_datetime"

    # Post-normalization: domain constraints
    NEGATIVE_FARE = "negative_fare"
    NEGATIVE_DISTANCE = "negative_distance"
    PICKUP_AFTER_DROPOFF = "pickup_after_dropoff"
    INVALID_PASSENGER_COUNT = "invalid_passenger_count"


# Domain constraint metadata
#
# Each constraint is described by its rejection reason and a human-readable
# description. The actual Spark column expression lives in the transformation
# package (``silver_validators.py``), not here.

@dataclass(frozen=True, slots=True)
class DomainConstraint:
    """Describe one Silver-layer domain constraint."""

    reason: RejectionReason
    description: str


SILVER_PRE_NORMALIZATION_CONSTRAINTS: Final[tuple[DomainConstraint, ...]] = (
    DomainConstraint(
        RejectionReason.NON_INTEGRAL_PASSENGER_COUNT,
        "passenger_count must be integral before cast to int",
    ),
    DomainConstraint(
        RejectionReason.NON_INTEGRAL_RATECODE,
        "RatecodeID must be integral before cast to int",
    ),
    DomainConstraint(
        RejectionReason.NULL_FARE_AMOUNT,
        "fare_amount must not be null",
    ),
    DomainConstraint(
        RejectionReason.NULL_TRIP_DISTANCE,
        "trip_distance must not be null",
    ),
    DomainConstraint(
        RejectionReason.NULL_PASSENGER_COUNT,
        "passenger_count must not be null",
    ),
    DomainConstraint(
        RejectionReason.NULL_PICKUP_DATETIME,
        "tpep_pickup_datetime must not be null",
    ),
    DomainConstraint(
        RejectionReason.NULL_DROPOFF_DATETIME,
        "tpep_dropoff_datetime must not be null",
    ),
)

SILVER_POST_NORMALIZATION_CONSTRAINTS: Final[tuple[DomainConstraint, ...]] = (
    DomainConstraint(
        RejectionReason.NEGATIVE_FARE,
        "fare_amount must be non-negative",
    ),
    DomainConstraint(
        RejectionReason.NEGATIVE_DISTANCE,
        "trip_distance must be non-negative",
    ),
    DomainConstraint(
        RejectionReason.PICKUP_AFTER_DROPOFF,
        "tpep_pickup_datetime must precede tpep_dropoff_datetime",
    ),
    DomainConstraint(
        RejectionReason.INVALID_PASSENGER_COUNT,
        "passenger_count must be between 0 and 9 inclusive after normalization to integer",
    ),
)

SILVER_DOMAIN_CONSTRAINTS: Final[tuple[DomainConstraint, ...]] = (
    SILVER_PRE_NORMALIZATION_CONSTRAINTS + SILVER_POST_NORMALIZATION_CONSTRAINTS
)


# Path derivation

SILVER_LAYER_NAME: Final[str] = "silver"
SILVER_REJECTED_LAYER_NAME: Final[str] = "silver_rejected"


def derive_period_id(year: int, month: int) -> str:
    """Transform a year and month into a canonical YYYY-MM period identifier."""
    return f"{year:04d}-{month:02d}"


# Supported-slice validation

def is_supported_silver_slice(cab_type: str, year: int, month: int) -> bool:
    """Return whether the requested Silver slice is supported."""
    return cab_type in SILVER_SUPPORTED_CAB_TYPES and (year, month) in SILVER_SUPPORTED_PERIODS


def get_supported_silver_slice_checks(cab_type: str, year: int, month: int) -> tuple[CheckTuple, ...]:
    """Return semantic support checks for a Silver transform slice."""
    return (
        (cab_type in SILVER_SUPPORTED_CAB_TYPES, "cab_type", cab_type),
        ((year, month) in SILVER_SUPPORTED_PERIODS, "period", derive_period_id(year, month)),
    )


def validate_supported_silver_slice(cab_type: str, year: int, month: int) -> None:
    """Validate that a Silver transform slice is supported by the current contract."""
    raise_on_violations(
        get_supported_silver_slice_checks(cab_type=cab_type, year=year, month=month),
        "Unsupported Silver transform slice",
    )


# Schema accessor

def get_silver_schema_fields(cab_type: str) -> tuple[SilverSchemaField, ...]:
    """Return the expected Silver output schema for a cab type."""
    if cab_type == CAB_TYPE_YELLOW:
        return SILVER_YELLOW_SCHEMA_FIELDS
    raise InvalidRequestError(
        f"Unsupported Silver cab type: {cab_type}",
        violations=(("cab_type", cab_type),),
    )


# Normalization spec

@dataclass(frozen=True, slots=True)
class TypeNormalization:
    """Describe one Bronze-to-Silver type cast."""

    column_name: str
    bronze_type: str
    silver_type: str


SILVER_YELLOW_TYPE_NORMALIZATIONS: Final[tuple[TypeNormalization, ...]] = (
    TypeNormalization("passenger_count", "double", "int"),
    TypeNormalization("RatecodeID", "double", "int"),
)
