#!/usr/bin/env python3
"""Structural validation for Query Spec v1.

This validator checks the JSON contract only. It does not decide whether a query is
answerable or executable; Person 4 owns that decision. Empty fields are valid.
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from query_spec import (
    ANSWER_TYPES,
    ENTITY_ROLES,
    FILTER_OPS,
    OPERATIONS,
    SCHEMA_VERSION,
    SORT_ORDERS,
    STATUSES,
)


def _err(out: list[str], path: str, message: str):
    out.append(f"{path}: {message}")


def _check_period(period, path: str, out: list[str]):
    if not isinstance(period, dict):
        _err(out, path, "must be an object")
        return
    kind = period.get("kind")
    if kind is not None and not isinstance(kind, str):
        _err(out, path + ".kind", "must be a string or null")
    month = period.get("month")
    if month is not None and (not isinstance(month, int) or not 1 <= month <= 12):
        _err(out, path + ".month", "must be an integer 1..12 or null")
    year = period.get("year")
    if year is not None and not isinstance(year, int):
        _err(out, path + ".year", "must be an integer or null")
    for key in ("date", "start", "end"):
        value = period.get(key)
        if value is not None:
            try:
                date.fromisoformat(value)
            except (TypeError, ValueError):
                _err(out, f"{path}.{key}", "must be YYYY-MM-DD or null")
    start, end = period.get("start"), period.get("end")
    if start and end and start > end:
        _err(out, path, "start must be <= end")


def _check_filter(item, path: str, out: list[str]):
    if not isinstance(item, dict):
        _err(out, path, "must be an object")
        return
    if not isinstance(item.get("field"), str) or not item.get("field"):
        _err(out, path + ".field", "must be a non-empty string")
    if item.get("op") not in FILTER_OPS:
        _err(out, path + ".op", f"must be one of {sorted(FILTER_OPS)}")
    if "value" not in item:
        _err(out, path, "must contain value")
    unit = item.get("unit")
    if unit is not None and not isinstance(unit, str):
        _err(out, path + ".unit", "must be a string or null")


def _check_component(item, path: str, out: list[str]):
    if not isinstance(item, dict):
        _err(out, path, "must be an object")
        return
    metric = item.get("metric")
    if metric is not None and not isinstance(metric, str):
        _err(out, path + ".metric", "must be a string or null")
    filters = item.get("filters")
    if not isinstance(filters, list):
        _err(out, path + ".filters", "must be a list")
    else:
        for i, f in enumerate(filters):
            _check_filter(f, f"{path}.filters[{i}]", out)


def validate_query(query: dict, path: str = "query") -> list[str]:
    out: list[str] = []
    if not isinstance(query, dict):
        return [f"{path}: must be an object"]

    operation = query.get("operation")
    if operation is not None and operation not in OPERATIONS:
        _err(out, path + ".operation", f"must be one of {sorted(OPERATIONS)} or null")
    answer_type = query.get("answer_type")
    if answer_type is not None and answer_type not in ANSWER_TYPES:
        _err(out, path + ".answer_type", f"must be one of {sorted(ANSWER_TYPES)} or null")
    sort_order = query.get("sort_order")
    if sort_order is not None and sort_order not in SORT_ORDERS:
        _err(out, path + ".sort_order", "must be asc, desc or null")

    for key in ("metric", "per", "group_by", "sort_by", "unit"):
        value = query.get(key)
        if value is not None and not isinstance(value, str):
            _err(out, f"{path}.{key}", "must be a string or null")
    for key in ("limit",):
        value = query.get(key)
        if value is not None and (not isinstance(value, int) or value <= 0):
            _err(out, f"{path}.{key}", "must be a positive integer or null")
    for key in ("distinct",):
        value = query.get(key)
        if value is not None and not isinstance(value, bool):
            _err(out, f"{path}.{key}", "must be a boolean or null")

    filters = query.get("filters")
    if not isinstance(filters, list):
        _err(out, path + ".filters", "must be a list")
    else:
        for i, item in enumerate(filters):
            _check_filter(item, f"{path}.filters[{i}]", out)

    having = query.get("having")
    if having is not None:
        if not isinstance(having, dict):
            _err(out, path + ".having", "must be an object or null")
        else:
            if having.get("op") not in FILTER_OPS:
                _err(out, path + ".having.op", f"must be one of {sorted(FILTER_OPS)}")
            if "value" not in having:
                _err(out, path + ".having", "must contain value")

    for key in ("numerator", "denominator"):
        if query.get(key) is not None:
            _check_component(query[key], f"{path}.{key}", out)

    entities = query.get("entities")
    if not isinstance(entities, list):
        _err(out, path + ".entities", "must be a list")
    else:
        for i, item in enumerate(entities):
            if not isinstance(item, dict):
                _err(out, f"{path}.entities[{i}]", "must be an object")
                continue
            if not isinstance(item.get("text"), str) or not item["text"].strip():
                _err(out, f"{path}.entities[{i}].text", "must be a non-empty string")
            if item.get("role") not in ENTITY_ROLES:
                _err(out, f"{path}.entities[{i}].role", f"must be one of {sorted(ENTITY_ROLES)}")

    time = query.get("time")
    if time is not None:
        if not isinstance(time, dict):
            _err(out, path + ".time", "must be an object or null")
        else:
            periods = time.get("periods")
            if not isinstance(periods, list):
                _err(out, path + ".time.periods", "must be a list")
            else:
                for i, p in enumerate(periods):
                    _check_period(p, f"{path}.time.periods[{i}]", out)
            shift = time.get("shift")
            if shift is not None and not isinstance(shift, str):
                _err(out, path + ".time.shift", "must be a string or null")
            comparison = time.get("comparison")
            if comparison is not None:
                if not isinstance(comparison, dict):
                    _err(out, path + ".time.comparison", "must be an object or null")
                else:
                    for i, p in enumerate(comparison.get("periods", [])):
                        _check_period(p, f"{path}.time.comparison.periods[{i}]", out)

    notes = query.get("notes")
    if not isinstance(notes, list) or not all(isinstance(n, str) for n in notes):
        _err(out, path + ".notes", "must be a list of strings")

    return out


def validate_record(record: dict, strict: bool = False) -> list[str]:
    out: list[str] = []
    if not isinstance(record, dict):
        return ["record: must be an object"]
    if record.get("schema_version") != SCHEMA_VERSION:
        _err(out, "schema_version", f"must be {SCHEMA_VERSION}")
    if not isinstance(record.get("question_id"), str) or not record["question_id"]:
        _err(out, "question_id", "must be a non-empty string")
    if record.get("status") not in STATUSES:
        _err(out, "status", f"must be one of {sorted(STATUSES)}")
    as_of = record.get("as_of_date")
    if not isinstance(as_of, str):
        _err(out, "as_of_date", "must be a string")
    else:
        try:
            date.fromisoformat(as_of)
        except ValueError:
            _err(out, "as_of_date", "must be YYYY-MM-DD")
    out.extend(validate_query(record.get("query"), "query"))
    if strict and out:
        raise ValueError("; ".join(out))
    return out


def iter_records(path):
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            yield json.loads(line)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--query-specs", required=True)
    ap.add_argument("--out", help="optional JSON report")
    a = ap.parse_args(argv)
    total, invalid = 0, 0
    errors = []
    for rec in iter_records(a.query_specs):
        total += 1
        issues = validate_record(rec)
        if issues:
            invalid += 1
            errors.append({"question_id": rec.get("question_id"), "issues": issues})
    report = {"total": total, "invalid": invalid, "errors": errors[:200]}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if a.out:
        Path(a.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if invalid else 0


if __name__ == "__main__":
    raise SystemExit(main())