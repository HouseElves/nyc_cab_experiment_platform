"""
Define the Silver transform request type.

Class Relationships
-------------------

.. mermaid::

    classDiagram

        dataclass <|-- SilverTransformRequest
        _Validated <|-- SilverTransformRequest

        class SilverTransformRequest {
            <<immutable>>
            string cab_type
            integer year
            integer month
            }
"""

from dataclasses import dataclass
from typing import ClassVar

from nyc_cab._validation import _Validated, CheckSpec, CheckTuple
from nyc_cab.contracts.silver import derive_period_id


# pylint: disable=duplicate-code
# See design_log.md decision 28: duplication is intentional and tracked.
# The shared abstraction will be extracted when the Silver API stabilizes.


@dataclass(frozen=True)
class SilverTransformRequest(_Validated):
    """Describe a Silver transform request for one monthly slice."""

    cab_type: str
    year: int
    month: int

    _type_check_specs: ClassVar[tuple[CheckSpec, ...]] = (
        ("cab_type", str),
        ("year", int, bool),
        ("month", int, bool),
    )

    @property
    def period_id(self) -> str:
        """Return the canonical YYYY-MM period identifier."""
        return derive_period_id(self.year, self.month)

    def _structural_checks(self) -> tuple[CheckTuple, ...]:
        """Return structural validation rules for this request."""
        return (
            (self.cab_type.strip() != "", "cab_type", self.cab_type),
            (1 <= self.month <= 12, "month", self.month),
            (1900 <= self.year <= 2100, "year", self.year),
        )
