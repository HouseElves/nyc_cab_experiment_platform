# pylint: disable=protected-access
"""
Tests for :mod:`nyc_cab._validation`.

These tests cover the validation protocol's full surface area:

* :func:`raise_on_violations` — the central aggregation point
* :class:`_Validated` — the mix-in's full lifecycle
* The constructor-argument count check
* Two-tuple and three-tuple ``CheckSpec`` forms
* The ``validate`` / ``is_valid`` / ``validity_check`` triad
* The default ``_structural_checks`` returning an empty sequence

Test methods exercise protected members of ``_Validated`` directly; the
module-level pylint suppression above acknowledges that this is intentional.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import ClassVar

import pytest

from nyc_cab._validation import (
    CheckSpec,
    CheckTuple,
    _Validated,
    raise_on_violations,
)
from nyc_cab.exceptions import InvalidRequestError

pytestmark = pytest.mark.unit


# --- raise_on_violations ----------------------------------------------------


def test_raise_on_violations_silent_when_all_pass() -> None:
    """No exception is raised when every check passed."""
    raise_on_violations([(True, "x", 1), (True, "y", 2)], "should not fire")


def test_raise_on_violations_silent_on_empty_sequence() -> None:
    """An empty check sequence does not raise."""
    raise_on_violations([], "should not fire")


def test_raise_on_violations_raises_when_any_check_fails() -> None:
    """A single failed check triggers the exception."""
    with pytest.raises(InvalidRequestError) as info:
        raise_on_violations([(True, "x", 1), (False, "y", 2)], "boom")
    assert info.value.violations == (("y", 2),)
    assert str(info.value) == "boom"


def test_raise_on_violations_aggregates_multiple_failures() -> None:
    """All failed checks are present in the violations tuple."""
    with pytest.raises(InvalidRequestError) as info:
        raise_on_violations(
            [(False, "x", 1), (True, "y", 2), (False, "z", 3)],
            "multi",
        )
    assert info.value.violations == (("x", 1), ("z", 3))


def test_raise_on_violations_preserves_message_text() -> None:
    """The supplied message becomes the exception's string form."""
    with pytest.raises(InvalidRequestError) as info:
        raise_on_violations([(False, "x", 1)], "specific message")
    assert "specific message" in str(info.value)


# --- Helpers used across multiple _Validated tests --------------------------


@dataclass(frozen=True)
class _SimpleValidated(_Validated):
    """A minimal validated dataclass for protocol testing."""

    name: str
    count: int

    _type_check_specs: ClassVar[tuple[CheckSpec, ...]] = (
        ("name", str),
        ("count", int, bool),
    )

    def _structural_checks(self) -> tuple[CheckTuple, ...]:
        return (
            (self.name.strip() != "", "name", self.name),
            (self.count >= 0, "count", self.count),
        )


@dataclass(frozen=True)
class _NoStructuralChecks(_Validated):
    """A validated dataclass that does not override structural checks."""

    label: str

    _type_check_specs: ClassVar[tuple[CheckSpec, ...]] = (
        ("label", str),
    )


# --- _Validated.create_validated --------------------------------------------


def test_create_validated_happy_path() -> None:
    """Valid inputs produce a fully-validated instance."""
    instance = _SimpleValidated.create_validated("widget", 5)
    assert instance.name == "widget"
    assert instance.count == 5


def test_create_validated_too_few_args_raises_type_error() -> None:
    """Fewer arguments than fields raises ``TypeError``."""
    with pytest.raises(TypeError) as info:
        _SimpleValidated.create_validated("widget")
    assert "expected 2 arguments" in str(info.value)
    assert "received 1" in str(info.value)


def test_create_validated_too_many_args_raises_type_error() -> None:
    """More arguments than fields raises ``TypeError``."""
    with pytest.raises(TypeError) as info:
        _SimpleValidated.create_validated("widget", 5, "extra")
    assert "expected 2 arguments" in str(info.value)
    assert "received 3" in str(info.value)


def test_create_validated_rejects_wrong_required_type() -> None:
    """A value of the wrong required type is rejected."""
    with pytest.raises(InvalidRequestError) as info:
        _SimpleValidated.create_validated(123, 5)
    assert info.value.violations == (("name", 123),)


def test_create_validated_rejects_excluded_type() -> None:
    """A value of the excluded type is rejected even when it matches the required type."""
    with pytest.raises(InvalidRequestError) as info:
        _SimpleValidated.create_validated("widget", True)
    assert info.value.violations == (("count", True),)


def test_create_validated_aggregates_type_check_failures() -> None:
    """Multiple type-check failures aggregate into one exception."""
    with pytest.raises(InvalidRequestError) as info:
        _SimpleValidated.create_validated(123, "not-an-int")
    names = [v[0] for v in info.value.violations]
    assert names == ["name", "count"]


def test_create_validated_runs_structural_checks_after_type_checks() -> None:
    """Structural checks fire when type checks pass but values are invalid."""
    with pytest.raises(InvalidRequestError) as info:
        _SimpleValidated.create_validated("   ", -1)
    names = [v[0] for v in info.value.violations]
    assert "name" in names
    assert "count" in names


def test_create_validated_with_no_structural_checks_subclass() -> None:
    """A subclass that does not override ``_structural_checks`` still validates type-only."""
    instance = _NoStructuralChecks.create_validated("any-label-works")
    assert instance.label == "any-label-works"


# --- _Validated.validate ----------------------------------------------------


def test_validate_runs_structural_checks() -> None:
    """``validate`` raises when a directly-constructed instance is invalid."""
    instance = _SimpleValidated("", -1)
    with pytest.raises(InvalidRequestError) as info:
        instance.validate()
    names = [v[0] for v in info.value.violations]
    assert "name" in names
    assert "count" in names


def test_validate_silent_when_instance_is_valid() -> None:
    """``validate`` is silent on a valid instance."""
    instance = _SimpleValidated("widget", 5)
    instance.validate()  # no exception


# --- _Validated.is_valid ----------------------------------------------------


def test_is_valid_returns_true_for_valid_instance() -> None:
    """``is_valid`` returns ``True`` when validation passes."""
    instance = _SimpleValidated("widget", 5)
    assert instance.is_valid() is True


def test_is_valid_returns_false_for_invalid_instance() -> None:
    """``is_valid`` returns ``False`` when validation would raise."""
    instance = _SimpleValidated("", -1)
    assert instance.is_valid() is False


# --- _Validated.validity_check ----------------------------------------------


def test_validity_check_returns_check_tuple_for_valid_instance() -> None:
    """``validity_check`` returns a passing tuple for a valid instance."""
    instance = _SimpleValidated("widget", 5)
    passed, name, value = instance.validity_check("payload")
    assert passed is True
    assert name == "payload"
    assert value is instance


def test_validity_check_returns_check_tuple_for_invalid_instance() -> None:
    """``validity_check`` returns a failing tuple for an invalid instance."""
    instance = _SimpleValidated("", -1)
    passed, name, value = instance.validity_check("payload")
    assert passed is False
    assert name == "payload"
    assert value is instance


# --- _Validated._structural_checks default ----------------------------------


def test_default_structural_checks_returns_empty_sequence() -> None:
    """The default ``_structural_checks`` returns an empty tuple."""

    @dataclass(frozen=True)
    class _Bare(_Validated):
        x: str
        _type_check_specs: ClassVar[tuple[CheckSpec, ...]] = (("x", str),)

    instance = _Bare("anything")
    assert len(tuple(instance._structural_checks())) == 0


# --- _Validated._constructor_type_checks ------------------------------------


def test_constructor_type_checks_two_tuple_spec() -> None:
    """Two-tuple specs check only the required type."""

    @dataclass(frozen=True)
    class _TwoTuple(_Validated):
        flag: bool
        _type_check_specs: ClassVar[tuple[CheckSpec, ...]] = (
            ("flag", bool),
        )

    checks = _TwoTuple._constructor_type_checks((True,))
    assert checks == ((True, "flag", True),)


def test_constructor_type_checks_three_tuple_spec_excludes_type() -> None:
    """Three-tuple specs reject values of the excluded type."""

    @dataclass(frozen=True)
    class _ThreeTuple(_Validated):
        n: int
        _type_check_specs: ClassVar[tuple[CheckSpec, ...]] = (
            ("n", int, bool),
        )

    passing = _ThreeTuple._constructor_type_checks((5,))
    assert passing == ((True, "n", 5),)

    failing = _ThreeTuple._constructor_type_checks((True,))
    assert failing == ((False, "n", True),)


# --- Frozenness preservation through inheritance ----------------------------


def test_validated_subclass_is_frozen() -> None:
    """``frozen=True`` is honored on subclasses of the mix-in."""
    instance = _SimpleValidated.create_validated("widget", 5)
    with pytest.raises(dataclasses.FrozenInstanceError):
        instance.name = "something-else"  # type: ignore[misc]
