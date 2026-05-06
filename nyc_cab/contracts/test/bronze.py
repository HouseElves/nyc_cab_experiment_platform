"""Tests for :mod:`nyc_cab.contracts.bronze`.

These tests cover the Bronze v1 contract surface: derivation helpers,
slice-support predicates and check builders, the schema-field accessor,
and the per-field and column-set schema validators.

The current ``BRONZE_RAW_YELLOW_SCHEMA_FIELDS`` tuple is empty pending real
schema transcription. Schema-validator tests use ``monkeypatch.setattr`` to
substitute a minimal three-field stub schema so the validator paths are
exercised against known data.
"""

from __future__ import annotations

import dataclasses

import pytest

from nyc_cab.contracts.bronze import (
    BRONZE_PARTITION_COLUMNS,
    BRONZE_SOURCE_FILENAME_TEMPLATE,
    BRONZE_SOURCE_URL_BASE,
    BRONZE_SUPPORTED_CAB_TYPES,
    BRONZE_SUPPORTED_PERIODS,
    BronzeSchemaField,
    CAB_TYPE_YELLOW,
    derive_bronze_source_filename,
    derive_bronze_source_url,
    derive_period_id,
    get_bronze_raw_schema_fields,
    get_bronze_schema_field_checks,
    get_supported_bronze_slice_checks,
    is_supported_bronze_slice,
    validate_against_bronze_schema,
    validate_bronze_column_set,
    validate_supported_bronze_slice,
)
from nyc_cab.exceptions import InvalidRequestError


# A canonical three-field stub schema used to exercise the per-field and
# column-set validators while the real ``BRONZE_RAW_YELLOW_SCHEMA_FIELDS``
# tuple is still empty.
_STUB_YELLOW_FIELDS: tuple[BronzeSchemaField, ...] = (
    BronzeSchemaField(name="VendorID", spark_type="int", nullable=True),
    BronzeSchemaField(name="trip_distance", spark_type="double", nullable=True),
    BronzeSchemaField(name="payment_type", spark_type="bigint", nullable=False),
)


# --- Module-level constants -------------------------------------------------


def test_cab_type_yellow_constant() -> None:
    """``CAB_TYPE_YELLOW`` exposes the canonical yellow identifier."""
    assert CAB_TYPE_YELLOW == "yellow"


def test_supported_cab_types_contains_yellow_only() -> None:
    """Yellow is currently the only supported cab type."""
    assert BRONZE_SUPPORTED_CAB_TYPES == (CAB_TYPE_YELLOW,)


def test_supported_periods_lists_jan_and_feb_2023() -> None:
    """The contract supports exactly January and February 2023."""
    assert BRONZE_SUPPORTED_PERIODS == ((2023, 1), (2023, 2))


def test_partition_columns_match_documented_layout() -> None:
    """Partition columns match the cab_type/year/month layout."""
    assert BRONZE_PARTITION_COLUMNS == ("cab_type", "year", "month")


def test_source_url_base_targets_cloudfront() -> None:
    """The source URL base targets the canonical TLC CloudFront host."""
    assert BRONZE_SOURCE_URL_BASE.startswith("https://")
    assert "cloudfront.net" in BRONZE_SOURCE_URL_BASE


def test_source_filename_template_uses_expected_placeholders() -> None:
    """The filename template contains the cab_type, year, and month placeholders."""
    assert "{cab_type}" in BRONZE_SOURCE_FILENAME_TEMPLATE
    assert "{year}" in BRONZE_SOURCE_FILENAME_TEMPLATE
    assert "{month:02d}" in BRONZE_SOURCE_FILENAME_TEMPLATE


# --- BronzeSchemaField ------------------------------------------------------


def test_bronze_schema_field_is_frozen() -> None:
    """``BronzeSchemaField`` instances reject attribute mutation."""

    field = BronzeSchemaField(name="x", spark_type="int", nullable=True)
    with pytest.raises(dataclasses.FrozenInstanceError):
        field.name = "y"  # type: ignore[misc]


def test_bronze_schema_field_stores_all_three_attributes() -> None:
    """The dataclass exposes its three constructor arguments by name."""
    field = BronzeSchemaField(name="trip_distance", spark_type="double", nullable=True)
    assert field.name == "trip_distance"
    assert field.spark_type == "double"
    assert field.nullable is True


# --- Transformation helpers -------------------------------------------------


def test_derive_period_id_zero_pads_single_digit_month() -> None:
    """Single-digit months are zero-padded to two characters."""
    assert derive_period_id(2023, 1) == "2023-01"


def test_derive_period_id_handles_two_digit_month() -> None:
    """Two-digit months pass through unchanged."""
    assert derive_period_id(2023, 12) == "2023-12"


def test_derive_bronze_source_filename_uses_template() -> None:
    """The filename derivation produces the canonical TLC filename."""
    assert (
        derive_bronze_source_filename("yellow", 2023, 1)
        == "yellow_tripdata_2023-01.parquet"
    )


def test_derive_bronze_source_filename_zero_pads_month() -> None:
    """The filename derivation zero-pads single-digit months."""
    assert (
        derive_bronze_source_filename("yellow", 2023, 7)
        == "yellow_tripdata_2023-07.parquet"
    )


def test_derive_bronze_source_url_concatenates_base_and_filename() -> None:
    """The URL derivation combines the base URL and the canonical filename."""
    expected = (
        "https://d37ci6vzurychx.cloudfront.net/trip-data/"
        "yellow_tripdata_2023-02.parquet"
    )
    assert derive_bronze_source_url("yellow", 2023, 2) == expected


# --- is_supported_bronze_slice ----------------------------------------------


def test_is_supported_bronze_slice_accepts_yellow_jan_2023() -> None:
    """Yellow January 2023 is a supported slice."""
    assert is_supported_bronze_slice("yellow", 2023, 1) is True


def test_is_supported_bronze_slice_accepts_yellow_feb_2023() -> None:
    """Yellow February 2023 is a supported slice."""
    assert is_supported_bronze_slice("yellow", 2023, 2) is True


def test_is_supported_bronze_slice_rejects_unsupported_cab() -> None:
    """An unsupported cab type returns ``False``."""
    assert is_supported_bronze_slice("green", 2023, 1) is False


def test_is_supported_bronze_slice_rejects_unsupported_period() -> None:
    """An unsupported period returns ``False``."""
    assert is_supported_bronze_slice("yellow", 2023, 3) is False


def test_is_supported_bronze_slice_rejects_when_both_components_unsupported() -> None:
    """Both unsupported cab and period yield ``False``."""
    assert is_supported_bronze_slice("green", 2024, 6) is False


# --- get_supported_bronze_slice_checks --------------------------------------


def test_get_supported_bronze_slice_checks_all_pass_for_supported_slice() -> None:
    """Every check passes for a supported slice."""
    checks = get_supported_bronze_slice_checks("yellow", 2023, 1)
    assert all(passed for (passed, _, _) in checks)


def test_get_supported_bronze_slice_checks_flags_unsupported_cab() -> None:
    """An unsupported cab type produces a single failing check."""
    checks = get_supported_bronze_slice_checks("green", 2023, 1)
    failed = [(name, value) for (passed, name, value) in checks if not passed]
    assert failed == [("cab_type", "green")]


def test_get_supported_bronze_slice_checks_flags_unsupported_period() -> None:
    """An unsupported period produces a single failing check."""
    checks = get_supported_bronze_slice_checks("yellow", 2023, 3)
    failed = [(name, value) for (passed, name, value) in checks if not passed]
    assert failed == [("period", "2023-03")]


def test_get_supported_bronze_slice_checks_flags_both_when_both_fail() -> None:
    """Two unsupported components produce two failing checks."""
    checks = get_supported_bronze_slice_checks("green", 2023, 3)
    failed = [(name, value) for (passed, name, value) in checks if not passed]
    assert failed == [("cab_type", "green"), ("period", "2023-03")]


# --- validate_supported_bronze_slice ----------------------------------------


def test_validate_supported_bronze_slice_silent_for_supported() -> None:
    """The validator returns silently for a supported slice."""
    validate_supported_bronze_slice("yellow", 2023, 1)


def test_validate_supported_bronze_slice_raises_for_unsupported() -> None:
    """The validator raises ``InvalidRequestError`` for an unsupported slice."""
    with pytest.raises(InvalidRequestError) as info:
        validate_supported_bronze_slice("green", 2023, 3)
    names = [v[0] for v in info.value.violations]
    assert names == ["cab_type", "period"]


# --- get_bronze_raw_schema_fields -------------------------------------------


def test_get_bronze_raw_schema_fields_returns_yellow_fields_for_yellow() -> None:
    """The accessor returns the yellow schema for the yellow cab type."""
    # Whether the tuple is empty (current state) or populated (future state),
    # the call must succeed for a supported cab type.
    fields = get_bronze_raw_schema_fields("yellow")
    assert isinstance(fields, tuple)


def test_get_bronze_raw_schema_fields_raises_for_unknown_cab(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown cab types produce a typed ``InvalidRequestError``."""
    monkeypatch.setattr(
        "nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS",
        _STUB_YELLOW_FIELDS,
    )
    with pytest.raises(InvalidRequestError) as info:
        get_bronze_raw_schema_fields("magenta")
    assert info.value.violations == (("cab_type", "magenta"),)


# --- get_bronze_schema_field_checks -----------------------------------------


def test_get_bronze_schema_field_checks_happy_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All checks pass when observation matches the contract exactly."""
    monkeypatch.setattr(
        "nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS",
        _STUB_YELLOW_FIELDS,
    )
    checks = get_bronze_schema_field_checks(
        "yellow",
        ["VendorID", "trip_distance", "payment_type"],
        {"VendorID": "int", "trip_distance": "double", "payment_type": "bigint"},
        {"VendorID": True, "trip_distance": True, "payment_type": False},
    )
    assert all(passed for (passed, _, _) in checks)


def test_get_bronze_schema_field_checks_flags_missing_field_with_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing expected field surfaces the ``<missing>`` marker as the value."""
    monkeypatch.setattr(
        "nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS",
        _STUB_YELLOW_FIELDS,
    )
    checks = get_bronze_schema_field_checks(
        "yellow",
        ["VendorID", "trip_distance"],
        {"VendorID": "int", "trip_distance": "double"},
        {"VendorID": True, "trip_distance": True},
    )
    failed = [(name, value) for (passed, name, value) in checks if not passed]
    assert ("payment_type", "<missing>") in failed


def test_get_bronze_schema_field_checks_skips_subchecks_for_missing_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a field is missing, no spark_type or nullable subcheck is emitted for it."""
    monkeypatch.setattr(
        "nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS",
        _STUB_YELLOW_FIELDS,
    )
    checks = get_bronze_schema_field_checks(
        "yellow",
        ["VendorID", "trip_distance"],
        {"VendorID": "int", "trip_distance": "double"},
        {"VendorID": True, "trip_distance": True},
    )
    names = [name for (_, name, _) in checks]
    assert "payment_type.spark_type" not in names
    assert "payment_type.nullable" not in names


def test_get_bronze_schema_field_checks_flags_wrong_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A spark_type mismatch produces a ``<field>.spark_type`` violation."""
    monkeypatch.setattr(
        "nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS",
        _STUB_YELLOW_FIELDS,
    )
    checks = get_bronze_schema_field_checks(
        "yellow",
        ["VendorID", "trip_distance", "payment_type"],
        {"VendorID": "int", "trip_distance": "string", "payment_type": "bigint"},
        {"VendorID": True, "trip_distance": True, "payment_type": False},
    )
    failed = [(name, value) for (passed, name, value) in checks if not passed]
    assert ("trip_distance.spark_type", "string") in failed


def test_get_bronze_schema_field_checks_flags_wrong_nullable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A nullability mismatch produces a ``<field>.nullable`` violation."""
    monkeypatch.setattr(
        "nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS",
        _STUB_YELLOW_FIELDS,
    )
    checks = get_bronze_schema_field_checks(
        "yellow",
        ["VendorID", "trip_distance", "payment_type"],
        {"VendorID": "int", "trip_distance": "double", "payment_type": "bigint"},
        {"VendorID": True, "trip_distance": True, "payment_type": True},
    )
    failed = [(name, value) for (passed, name, value) in checks if not passed]
    assert ("payment_type.nullable", True) in failed


def test_get_bronze_schema_field_checks_raises_on_inconsistent_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three observation arguments describing different column sets raise ``ValueError``."""
    monkeypatch.setattr(
        "nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS",
        _STUB_YELLOW_FIELDS,
    )
    with pytest.raises(ValueError):
        get_bronze_schema_field_checks(
            "yellow",
            ["VendorID", "trip_distance"],
            {"VendorID": "int"},
            {"VendorID": True, "trip_distance": True},
        )


# --- validate_bronze_column_set ---------------------------------------------


def test_validate_bronze_column_set_silent_for_correct_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The validator returns silently when the column list matches the contract."""
    monkeypatch.setattr(
        "nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS",
        _STUB_YELLOW_FIELDS,
    )
    validate_bronze_column_set("yellow", ["VendorID", "trip_distance", "payment_type"])


def test_validate_bronze_column_set_silent_when_column_list_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty column list against an empty schema produces no violations."""
    monkeypatch.setattr(
        "nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS",
        (),
    )
    validate_bronze_column_set("yellow", [])


def test_validate_bronze_column_set_flags_duplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A duplicate column produces a ``<column>.duplicate_count`` violation."""
    monkeypatch.setattr(
        "nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS",
        _STUB_YELLOW_FIELDS,
    )
    with pytest.raises(InvalidRequestError) as info:
        validate_bronze_column_set(
            "yellow",
            ["VendorID", "trip_distance", "trip_distance", "payment_type"],
        )
    assert ("trip_distance.duplicate_count", 2) in info.value.violations


def test_validate_bronze_column_set_flags_extras(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unexpected extra column produces a ``<column>.unexpected`` violation."""
    monkeypatch.setattr(
        "nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS",
        _STUB_YELLOW_FIELDS,
    )
    with pytest.raises(InvalidRequestError) as info:
        validate_bronze_column_set(
            "yellow",
            ["VendorID", "trip_distance", "payment_type", "rogue_field"],
        )
    assert ("rogue_field.unexpected", "rogue_field") in info.value.violations


def test_validate_bronze_column_set_aggregates_duplicates_and_extras(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Duplicates and extras both surface in the same exception."""
    monkeypatch.setattr(
        "nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS",
        _STUB_YELLOW_FIELDS,
    )
    with pytest.raises(InvalidRequestError) as info:
        validate_bronze_column_set(
            "yellow",
            ["VendorID", "VendorID", "trip_distance", "payment_type", "rogue"],
        )
    names = [v[0] for v in info.value.violations]
    assert "VendorID.duplicate_count" in names
    assert "rogue.unexpected" in names


# --- validate_against_bronze_schema -----------------------------------------


def test_validate_against_bronze_schema_silent_for_matching_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The combined validator returns silently for a fully-matching observation."""
    monkeypatch.setattr(
        "nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS",
        _STUB_YELLOW_FIELDS,
    )
    validate_against_bronze_schema(
        "yellow",
        ["VendorID", "trip_distance", "payment_type"],
        {"VendorID": "int", "trip_distance": "double", "payment_type": "bigint"},
        {"VendorID": True, "trip_distance": True, "payment_type": False},
    )


def test_validate_against_bronze_schema_raises_on_column_set_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Column-set failures raise from the column-set check before per-field checks run."""
    monkeypatch.setattr(
        "nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS",
        _STUB_YELLOW_FIELDS,
    )
    with pytest.raises(InvalidRequestError):
        validate_against_bronze_schema(
            "yellow",
            ["VendorID", "trip_distance", "payment_type", "rogue"],
            {
                "VendorID": "int", "trip_distance": "double",
                "payment_type": "bigint", "rogue": "string",
            },
            {
                "VendorID": True, "trip_distance": True,
                "payment_type": False, "rogue": True,
            },
        )


def test_validate_against_bronze_schema_raises_on_field_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Per-field failures raise after the column-set check passes."""
    monkeypatch.setattr(
        "nyc_cab.contracts.bronze.BRONZE_RAW_YELLOW_SCHEMA_FIELDS",
        _STUB_YELLOW_FIELDS,
    )
    with pytest.raises(InvalidRequestError) as info:
        validate_against_bronze_schema(
            "yellow",
            ["VendorID", "trip_distance", "payment_type"],
            {"VendorID": "string", "trip_distance": "double", "payment_type": "bigint"},
            {"VendorID": True, "trip_distance": True, "payment_type": False},
        )
    assert ("VendorID.spark_type", "string") in info.value.violations
