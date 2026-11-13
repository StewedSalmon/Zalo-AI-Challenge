#!/usr/bin/env python3
"""Query Spec v1 data contract for Person 3 (Question Understanding).

This module owns the JSON shape only. It deliberately has no routing, template,
business-rule or data-access logic. Missing fields are represented as null (scalars)
or [] (lists); they are not filled with guesses.

Serialization is done with dataclasses.asdict, so every declared field is present in
the produced JSON. That keeps the Person 4 contract stable and makes empty fields
explicit.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

SCHEMA_VERSION = 1

OPERATIONS = {
    "sum",
    "count",
    "average",
    "ratio",
    "share",
    "percent_change",
    "difference",
    "rank",
    "argmax",
    "argmin",
    "lookup",
    "series",
    "trend",
    "delta",
    "bucket",
    "unknown",
}
ANSWER_TYPES = {"number", "percent", "date", "string", "boolean", "list", "table"}
FILTER_OPS = {"eq", "ne", "gt", "gte", "lt", "lte"}
SORT_ORDERS = {"asc", "desc"}
ENTITY_ROLES = {"filter", "group_by", "sort_by", "unknown"}
STATUSES = {"ok", "ambiguous", "unsupported", "error"}


@dataclass
class Period:
    kind: Optional[str] = None
    month: Optional[int] = None
    year: Optional[int] = None
    date: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    anchor: Optional[str] = None
    raw: Optional[str] = None


@dataclass
class Filter:
    field: str
    op: str
    value: Any = None
    unit: Optional[str] = None


@dataclass
class Having:
    aggregation: str = "count"
    field: Optional[str] = None
    op: str = "gte"
    value: Any = None
    distinct: bool = True


@dataclass
class Entity:
    text: str
    concept: Optional[str] = None
    role: str = "filter"


@dataclass
class Component:
    metric: Optional[str] = None
    filters: list[Filter] = field(default_factory=list)
    group_by: Optional[str] = None
    scope: Optional[str] = None


@dataclass
class Comparison:
    base: Optional[str] = None
    periods: list[Period] = field(default_factory=list)
    direction: Optional[str] = None


@dataclass
class TimeSpec:
    kind: Optional[str] = None
    periods: list[Period] = field(default_factory=list)
    shift: Optional[str] = None
    comparison: Optional[Comparison] = None


@dataclass
class Query:
    operation: Optional[str] = None
    metric: Optional[str] = None
    distinct: Optional[bool] = None
    per: Optional[str] = None
    filters: list[Filter] = field(default_factory=list)
    having: Optional[Having] = None
    numerator: Optional[Component] = None
    denominator: Optional[Component] = None
    entities: list[Entity] = field(default_factory=list)
    time: Optional[TimeSpec] = None
    group_by: Optional[str] = None
    sort_by: Optional[str] = None
    sort_order: Optional[str] = None
    limit: Optional[int] = None
    unit: Optional[str] = None
    answer_type: Optional[str] = None
    notes: list[str] = field(default_factory=list)


@dataclass
class QueryRecord:
    question_id: str
    business_id: Optional[str]
    as_of_date: str
    question: str
    status: str = "ok"
    reason: Optional[str] = None
    schema_version: int = SCHEMA_VERSION
    query: Query = field(default_factory=Query)


def query_to_dict(query: Query) -> dict:
    return asdict(query)


def record_to_dict(record: QueryRecord) -> dict:
    return asdict(record)


def empty_record(question: dict, status: str = "ok", reason: str | None = None) -> QueryRecord:
    return QueryRecord(
        question_id=question.get("id", ""),
        business_id=question.get("business_id"),
        as_of_date=question.get("as_of_date", ""),
        question=question.get("question", ""),
        status=status,
        reason=reason,
        query=Query(),
    )