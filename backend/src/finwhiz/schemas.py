"""Strict model outputs. Public request errors are translated by the API."""

from typing import Literal
from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Point(StrictModel):
    label: str
    value: float
    source_ids: list[str]


class Chart(StrictModel):
    type: Literal["none", "bar", "line"]
    title: str
    unit: str
    points: list[Point]


class Calculation(StrictModel):
    label: str
    operation: Literal[
        "sum",
        "difference",
        "ratio",
        "percentage",
        "percent_change",
        "gross_margin",
        "gross_margin_excluding_revenue",
    ]
    unit: str
    values: list[float]
    source_ids: list[str]


class Answer(StrictModel):
    status: Literal["answered", "insufficient_evidence", "needs_clarification"]
    answer: str
    citation_ids: list[str]
    chart: Chart
    calculations: list[Calculation]


class Ranking(StrictModel):
    ids: list[str]


class Sentence(StrictModel):
    text: str
    source_ids: list[str]


class Repair(StrictModel):
    sentences: list[Sentence]
    calculations: list[Calculation]


class Claim(StrictModel):
    claim: str
    supported: bool
    citationCorrect: bool


class Review(StrictModel):
    claims: list[Claim]
    numericCorrect: bool
    unitsCorrect: bool
    periodsCorrect: bool
    notes: str
