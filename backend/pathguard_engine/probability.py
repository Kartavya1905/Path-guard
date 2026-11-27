"""Probability estimates and their provenance.

Representation: probabilities are stored and calculated as exact `Decimal`
fractions in [0, 1] (0.85 == 85%). The UI converts to percentages at the edge
with `from_percent` / `to_percent`. Floats are never used in calculations.

An estimate is conditional on reaching its decision state under the stated
context, history and controls. It is one of:
- point:   a single value
- range:   low < high bounds
- unknown: no numeric value at all (never treated as zero or an average)

`source` records where the number came from (measured, expert, hypothetical,
unknown) and `verification` records whether a human has reviewed it. Both are
separate from where a suggestion originated (rule, AI, user), which lives on
the transition or risk that carries the estimate.
"""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, PositiveInt, model_validator

MAX_FRACTION_PLACES = 10

Probability = Annotated[
    Decimal, Field(ge=Decimal(0), le=Decimal(1), decimal_places=MAX_FRACTION_PLACES)
]
NonBlank = Annotated[str, Field(min_length=1, max_length=2000)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MeasuredSource(_Strict):
    type: Literal["measured"] = "measured"
    data_source: NonBlank
    count: NonNegativeInt
    denominator: PositiveInt
    date_from: date
    date_to: date
    context: NonBlank

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.count > self.denominator:
            raise ValueError("count cannot exceed denominator")
        if self.date_from > self.date_to:
            raise ValueError("date_from must be on or before date_to")
        return self


class ExpertSource(_Strict):
    type: Literal["expert"] = "expert"
    reviewer: NonBlank
    rationale: NonBlank
    assumptions: list[NonBlank] = []
    review_date: date


class HypotheticalSource(_Strict):
    type: Literal["hypothetical"] = "hypothetical"
    assumption: NonBlank
    label: Literal["Hypothetical"] = "Hypothetical"


class UnknownSource(_Strict):
    type: Literal["unknown"] = "unknown"


Provenance = Annotated[
    MeasuredSource | ExpertSource | HypotheticalSource | UnknownSource,
    Field(discriminator="type"),
]


class Estimate(_Strict):
    kind: Literal["point", "range", "unknown"] = "unknown"
    value: Probability | None = None
    low: Probability | None = None
    high: Probability | None = None
    source: Provenance = Field(default_factory=UnknownSource)
    verification: Literal["unverified", "reviewed"] = "unverified"
    reviewed_by: str | None = None
    reviewed_on: date | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.kind == "point":
            if self.value is None or self.low is not None or self.high is not None:
                raise ValueError("a point estimate needs value and no low/high")
        elif self.kind == "range":
            if self.value is not None or self.low is None or self.high is None:
                raise ValueError("a range estimate needs low and high and no value")
            if self.low >= self.high:
                raise ValueError("range low must be less than high")
        elif any(v is not None for v in (self.value, self.low, self.high)):
            raise ValueError("an unknown estimate cannot carry numbers")

        numeric = self.kind != "unknown"
        if numeric and self.source.type == "unknown":
            raise ValueError("a numeric estimate needs a source (measured, expert, hypothetical)")
        if not numeric and self.source.type != "unknown":
            raise ValueError("an unknown estimate must have source 'unknown'")
        if self.verification == "reviewed" and not (self.reviewed_by and self.reviewed_on):
            raise ValueError("a reviewed estimate needs reviewed_by and reviewed_on")
        return self

    @property
    def is_numeric(self) -> bool:
        return self.kind != "unknown"

    @classmethod
    def unknown(cls) -> "Estimate":
        return cls()

    @classmethod
    def hypothetical(cls, percent: str | int, assumption: str) -> "Estimate":
        return cls(
            kind="point",
            value=from_percent(percent),
            source=HypotheticalSource(assumption=assumption),
        )


def from_percent(percent: str | int | Decimal) -> Decimal:
    """'85' or '72.675' -> Decimal('0.85') / Decimal('0.72675'). Rejects floats."""
    if isinstance(percent, float):
        raise TypeError("pass percentages as str, int or Decimal, not float")
    return Decimal(str(percent)) / Decimal(100)


def to_percent(fraction: Decimal) -> Decimal:
    return fraction * Decimal(100)
