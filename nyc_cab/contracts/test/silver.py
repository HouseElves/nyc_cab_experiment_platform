"""
Tests for :mod:`nyc_cab.contracts.silver`.

These tests cover the Silver v1 contract surface: schema fields, rejection
reason enumeration, domain constraint metadata (pre- and post-normalization
groups), type normalization specs, and slice-support validation.
"""

from __future__ import annotations

import dataclasses

import pytest

from nyc_cab.contracts.silver import (
    CAB_TYPE_YELLOW,
    SILVER_DOMAIN_CONSTRAINTS,
    SILVER_PARTITION_COLUMNS,
    SILVER_POST_NORMALIZATION_CONSTRAINTS,
    SILVER_PRE_NORMALIZATION_CONSTRAINTS,
    SILVER_REJECTION_COLUMN,
    SILVER_SUPPORTED_CAB_TYPES,
    SILVER_SUPPORTED_PERIODS,
    SILVER_YELLOW_SCHEMA_FIELDS,
    SILVER_YELLOW_TYPE_NORMALIZATIONS,
    DomainConstraint,
    RejectionReason,
    SilverSchemaField,
    TypeNormalization,
    get_silver_schema_fields,
    get_supported_silver_slice_checks,
    is_supported_silver_slice,
    validate_supported_silver_slice,
)
from nyc_cab.exceptions import InvalidRequestError


# --- Module-level constants -------------------------------------------------


def test_cab_type_yellow_constant() -> None:
    """``CAB_TYPE_YELLOW`` exposes the canonical yellow identifier."""
    assert CAB_TYPE_YELLOW == "yellow"


def test_supported_cab_types_contains_yellow_only() -> None:
    """Yellow is currently the only supported cab type."""
    assert SILVER_SUPPORTED_CAB_TYPES == (CAB_TYPE_YELLOW,)


def test_supported_periods_lists_jan_and_feb_2023() -> None:
    """The contract supports exactly January and February 2023."""
    assert SILVER_SUPPORTED_PERIODS == ((2023, 1), (2023, 2))


def test_partition_columns_match_documented_layout() -> None:
    """Partition columns match the cab_type/year/month layout."""
    assert SILVER_PARTITION_COLUMNS == ("cab_type", "year", "month")


def test_rejection_column_name() -> None:
    """The rejection column uses the underscore-prefixed convention."""
    assert SILVER_REJECTION_COLUMN == "_rejection_reasons"


# --- SilverSchemaField ------------------------------------------------------


def test_silver_schema_field_is_frozen() -> None:
    """``SilverSchemaField`` instances reject attribute mutation."""
    field = SilverSchemaField(name="x", spark_type="int", nullable=True)
    with pytest.raises(dataclasses.FrozenInstanceError):
        field.name = "y"  # type: ignore[misc]


def test_silver_schema_field_stores_all_attributes() -> None:
    """The dataclass exposes its three constructor arguments by name."""
    field = SilverSchemaField(name="passenger_count", spark_type="int", nullable=True)
    assert field.name == "passenger_count"
    assert field.spark_type == "int"
    assert field.nullable is True


# --- RejectionReason --------------------------------------------------------


def test_rejection_reason_has_eleven_members() -> None:
    """Silver v1 defines exactly eleven rejection reasons."""
    assert len(RejectionReason) == 11


def test_rejection_reason_values_are_snake_case_strings() -> None:
    """Each rejection reason value is a lowercase snake_case string."""
    for reason in RejectionReason:
        assert reason.value == reason.value.lower()
        assert " " not in reason.value


def test_rejection_reason_includes_integrality_checks() -> None:
    """The enum includes non-integral rejection reasons for both cast targets."""
    values = {r.value for r in RejectionReason}
    assert "non_integral_passenger_count" in values
    assert "non_integral_ratecode" in values


def test_rejection_reason_includes_null_checks() -> None:
    """The enum includes null rejection reasons for all five constraint-checked fields."""
    values = {r.value for r in RejectionReason}
    assert "null_fare_amount" in values
    assert "null_trip_distance" in values
    assert "null_passenger_count" in values
    assert "null_pickup_datetime" in values
    assert "null_dropoff_datetime" in values


# --- DomainConstraint -------------------------------------------------------


def test_domain_constraint_is_frozen() -> None:
    """``DomainConstraint`` instances reject attribute mutation."""
    constraint = DomainConstraint(RejectionReason.NEGATIVE_FARE, "test")
    with pytest.raises(dataclasses.FrozenInstanceError):
        constraint.description = "changed"  # type: ignore[misc]


def test_domain_constraints_has_eleven_entries() -> None:
    """Silver v1 defines exactly eleven domain constraints total."""
    assert len(SILVER_DOMAIN_CONSTRAINTS) == 11


def test_domain_constraints_cover_all_rejection_reasons() -> None:
    """Every rejection reason has a corresponding domain constraint."""
    constraint_reasons = {c.reason for c in SILVER_DOMAIN_CONSTRAINTS}
    all_reasons = set(RejectionReason)
    assert constraint_reasons == all_reasons


def test_domain_constraints_have_nonempty_descriptions() -> None:
    """Every domain constraint has a non-empty description."""
    for constraint in SILVER_DOMAIN_CONSTRAINTS:
        assert constraint.description.strip() != ""


# --- Pre/post normalization constraint groups -------------------------------


def test_pre_normalization_constraints_has_seven_entries() -> None:
    """Seven constraints fire before type normalization."""
    assert len(SILVER_PRE_NORMALIZATION_CONSTRAINTS) == 7


def test_post_normalization_constraints_has_four_entries() -> None:
    """Four constraints fire after type normalization."""
    assert len(SILVER_POST_NORMALIZATION_CONSTRAINTS) == 4


def test_pre_plus_post_equals_all() -> None:
    """The pre and post groups concatenate to the full constraint set."""
    assert SILVER_DOMAIN_CONSTRAINTS == (
        SILVER_PRE_NORMALIZATION_CONSTRAINTS + SILVER_POST_NORMALIZATION_CONSTRAINTS
    )


def test_pre_normalization_covers_integrality_and_nulls() -> None:
    """Pre-normalization constraints include all integrality and null checks."""
    pre_reasons = {c.reason for c in SILVER_PRE_NORMALIZATION_CONSTRAINTS}
    assert RejectionReason.NON_INTEGRAL_PASSENGER_COUNT in pre_reasons
    assert RejectionReason.NON_INTEGRAL_RATECODE in pre_reasons
    assert RejectionReason.NULL_FARE_AMOUNT in pre_reasons
    assert RejectionReason.NULL_PASSENGER_COUNT in pre_reasons
    assert RejectionReason.NULL_PICKUP_DATETIME in pre_reasons


def test_post_normalization_covers_domain_rules() -> None:
    """Post-normalization constraints include all domain business rules."""
    post_reasons = {c.reason for c in SILVER_POST_NORMALIZATION_CONSTRAINTS}
    assert RejectionReason.NEGATIVE_FARE in post_reasons
    assert RejectionReason.NEGATIVE_DISTANCE in post_reasons
    assert RejectionReason.PICKUP_AFTER_DROPOFF in post_reasons
    assert RejectionReason.INVALID_PASSENGER_COUNT in post_reasons


def test_no_overlap_between_pre_and_post() -> None:
    """No rejection reason appears in both pre and post groups."""
    pre_reasons = {c.reason for c in SILVER_PRE_NORMALIZATION_CONSTRAINTS}
    post_reasons = {c.reason for c in SILVER_POST_NORMALIZATION_CONSTRAINTS}
    assert pre_reasons.isdisjoint(post_reasons)


# --- TypeNormalization ------------------------------------------------------


def test_type_normalization_is_frozen() -> None:
    """``TypeNormalization`` instances reject attribute mutation."""
    norm = TypeNormalization("col", "double", "int")
    with pytest.raises(dataclasses.FrozenInstanceError):
        norm.column_name = "changed"  # type: ignore[misc]


def test_type_normalizations_has_two_entries() -> None:
    """Silver v1 defines exactly two type normalizations."""
    assert len(SILVER_YELLOW_TYPE_NORMALIZATIONS) == 2


def test_type_normalizations_target_passenger_count_and_ratecode() -> None:
    """The normalizations target passenger_count and RatecodeID."""
    names = {n.column_name for n in SILVER_YELLOW_TYPE_NORMALIZATIONS}
    assert names == {"passenger_count", "RatecodeID"}


def test_type_normalizations_cast_double_to_int() -> None:
    """Both normalizations cast from double to int."""
    for norm in SILVER_YELLOW_TYPE_NORMALIZATIONS:
        assert norm.bronze_type == "double"
        assert norm.silver_type == "int"


# --- Schema -----------------------------------------------------------------


def test_silver_schema_has_19_fields() -> None:
    """The Silver v1 Yellow cab schema has exactly 19 fields."""
    assert len(SILVER_YELLOW_SCHEMA_FIELDS) == 19


def test_silver_schema_passenger_count_is_int() -> None:
    """``passenger_count`` is normalized to int in the Silver schema."""
    field = [f for f in SILVER_YELLOW_SCHEMA_FIELDS if f.name == "passenger_count"][0]
    assert field.spark_type == "int"


def test_silver_schema_ratecode_is_int() -> None:
    """``RatecodeID`` is normalized to int in the Silver schema."""
    field = [f for f in SILVER_YELLOW_SCHEMA_FIELDS if f.name == "RatecodeID"][0]
    assert field.spark_type == "int"


def test_silver_schema_preserves_non_normalized_types() -> None:
    """Columns not in the normalization spec retain their Bronze types."""
    field = [f for f in SILVER_YELLOW_SCHEMA_FIELDS if f.name == "fare_amount"][0]
    assert field.spark_type == "double"


# --- Slice validation -------------------------------------------------------


def test_is_supported_accepts_yellow_jan_2023() -> None:
    """Yellow January 2023 is a supported slice."""
    assert is_supported_silver_slice("yellow", 2023, 1) is True


def test_is_supported_rejects_unsupported_cab() -> None:
    """An unsupported cab type returns False."""
    assert is_supported_silver_slice("green", 2023, 1) is False


def test_is_supported_rejects_unsupported_period() -> None:
    """An unsupported period returns False."""
    assert is_supported_silver_slice("yellow", 2023, 3) is False


def test_slice_checks_all_pass_for_supported() -> None:
    """Every check passes for a supported slice."""
    checks = get_supported_silver_slice_checks("yellow", 2023, 1)
    assert all(passed for (passed, _, _) in checks)


def test_slice_checks_flag_unsupported_cab() -> None:
    """An unsupported cab type produces a failing check."""
    checks = get_supported_silver_slice_checks("green", 2023, 1)
    failed = [(name, value) for (passed, name, value) in checks if not passed]
    assert ("cab_type", "green") in failed


def test_validate_slice_silent_for_supported() -> None:
    """The validator returns silently for a supported slice."""
    validate_supported_silver_slice("yellow", 2023, 1)


def test_validate_slice_raises_for_unsupported() -> None:
    """The validator raises for an unsupported slice."""
    with pytest.raises(InvalidRequestError):
        validate_supported_silver_slice("green", 2023, 3)


# --- Schema accessor -------------------------------------------------------


def test_get_schema_fields_returns_yellow() -> None:
    """The accessor returns the yellow schema for the yellow cab type."""
    fields = get_silver_schema_fields("yellow")
    assert len(fields) == 19


def test_get_schema_fields_raises_for_unknown_cab() -> None:
    """Unknown cab types produce a typed error."""
    with pytest.raises(InvalidRequestError):
        get_silver_schema_fields("magenta")