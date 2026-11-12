#!/usr/bin/env python3
"""Deterministic, data-driven routing from matcher features to shape templates.

The router does not decide answerability. The matcher remains responsible for
profile absence and coverage. Canonical ids come only from concept_dictionary.json
(the router does not read params.json or any rulebook file).
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import re
import unicodedata
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Optional

YEAR_FALLBACK = "sheet_rule"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
OPERATORS = {"equals", "in", "present", "absent", "count_eq", "gt"}
ANSWER_TYPES = {"number", "percent", "date", "string", "boolean", "list", "table"}
MATCHING_FIELDS = {
    "question_id", "status", "reason", "step", "metric", "entities", "filters",
    "concepts", "absent", "status_map", "units", "time", "ranking", "answer_type",
    "defaults_overridden", "default_flagged", "trace", "exclusions",
}
REQUIRED_MATCHING_FIELDS = MATCHING_FIELDS - {"exclusions"}
EXTRACTION_FIELDS = {"question_id", "text", "folded", "spans", "flags"}
SPAN_FIELDS = {
    "start", "end", "orig_start", "orig_end", "surface", "category", "term_id",
    "candidates", "rulebook_ref", "value", "raw_components", "pattern",
}
SPAN_CATEGORIES = {
    "metric", "entity", "filter", "time", "ranking", "answer_type", "number",
    "date", "comparator", "superlative", "direction", "free",
}


def _fold(value: str) -> str:
    value = value.replace("đ", "d").replace("Đ", "D").casefold()
    decomposed = unicodedata.normalize("NFD", value)
    return " ".join("".join(c for c in decomposed if not unicodedata.combining(c)).split())


def _valid_date(value: Any) -> bool:
    if not isinstance(value, str) or not DATE_RE.fullmatch(value):
        return False
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _unsupported(reason: str) -> "Adaptation":
    return Adaptation(status="unsupported", reason=reason)


@dataclass(frozen=True)
class Period:
    kind: Optional[str] = None
    month: Optional[int] = None
    year: Optional[int] = None
    date: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    anchor: Optional[str] = None   # relative reading: 'current' / 'previous' (week, month), 'Q1'..'Q4' (quarter)


@dataclass(frozen=True)
class Features:
    metric: Optional[str] = None
    answer_type: Optional[str] = None
    group: Optional[str] = None
    ranking: Optional[dict] = None
    k: Optional[int] = None
    superlative_term: Optional[str] = None
    comparator: Optional[str] = None
    threshold: Optional[int] = None
    periods: tuple[Period, ...] = ()
    time_kind: Optional[str] = None
    date: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    change: Optional[str] = None
    unit: Optional[str] = None
    per: Optional[str] = None
    stock_comparator: Optional[str] = None
    stock_value: Optional[int] = None
    entity_concepts: tuple[str, ...] = ()
    filter_concepts: tuple[str, ...] = ()
    status_filter: Optional[str] = None
    trace: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Adaptation:
    """Envelope for the adapter: features exist only when status is 'ok'."""
    status: str
    reason: Optional[str] = None
    features: Optional[Features] = None


@dataclass(frozen=True)
class RouteResult:
    status: str
    shape: Optional[str]
    rule_id: Optional[str]
    reason: Optional[str]


@dataclass(frozen=True)
class CanonicalCatalog:
    """Canonical ids, read only from concept_dictionary.json. No other file is consulted."""
    dictionary: dict
    metric_ids: frozenset[str]        # kind 'concept'
    concept_ids: frozenset[str]       # kind 'concept' or 'grouping' (ids that may appear in required_concepts)
    grouping_ids: frozenset[str]
    comparator_ids: frozenset[str]
    time_kind_ids: frozenset[str]
    status_ids: frozenset[str]

    @classmethod
    def from_path(cls, dictionary_path: str | Path) -> "CanonicalCatalog":
        dictionary = json.loads(Path(dictionary_path).read_text(encoding="utf-8"))
        entries = dictionary["entries"]

        def ids(kind: str) -> frozenset[str]:
            return frozenset(e["canonical"] for e in entries if e["kind"] == kind)

        metric_ids, grouping_ids = ids("concept"), ids("grouping")
        return cls(
            dictionary=dictionary,
            metric_ids=metric_ids,
            concept_ids=metric_ids | grouping_ids,
            grouping_ids=grouping_ids,
            comparator_ids=ids("comparator"),
            time_kind_ids=ids("time_kind"),
            status_ids=ids("status"),
        )

    def time_kind(self, value: Any) -> tuple[Optional[str], Optional[str]]:
        """Accept a dictionary id as is, or a bare name whose 'time:' form is a dictionary id ('month' -> 'time:month')."""
        if isinstance(value, str):
            for candidate in (value, f"time:{value}"):
                if candidate in self.time_kind_ids:
                    return candidate, None
        return None, "unknown canonical time kind " + repr(value)


def load_templates(path: str | Path) -> dict[str, dict]:
    templates: dict[str, dict] = {}
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            template = json.loads(line)
            shape = template["shape"]
            if shape in templates:
                raise ValueError(f"duplicate template shape {shape!r} at line {line_number}")
            templates[shape] = template
    return templates


def _condition_value(condition: dict, feature: str, catalog: CanonicalCatalog) -> Optional[str]:
    values = condition.get("value") if condition["op"] == "in" else [condition.get("value")]
    if values is None:
        values = [None]
    if not isinstance(values, list):
        return f"operator 'in' requires a list for feature {feature!r}"
    catalogs = {
        "metric": catalog.metric_ids,
        "group": catalog.grouping_ids,
        "comparator": catalog.comparator_ids,
        "stock_comparator": catalog.comparator_ids,
        "time_kind": catalog.time_kind_ids,
        "status_filter": catalog.status_ids,
        "answer_type": ANSWER_TYPES,
        "change": {"diff", "pct"},
    }
    allowed = catalogs.get(feature)
    if allowed is not None:
        for value in values:
            if value is not None and value not in allowed:
                return f"unknown canonical id {value!r} for feature {feature!r}"
    return None


def validate_rules(rules: dict, templates: dict[str, dict], catalog: CanonicalCatalog) -> None:
    feature_names = set(Features.__dataclass_fields__)
    shape_metrics = rules.get("shape_metrics")
    if not isinstance(shape_metrics, dict) or set(shape_metrics) != set(templates):
        raise ValueError("shape_metrics must contain every template shape exactly once")
    for shape, metrics in shape_metrics.items():
        if not isinstance(metrics, list) or not metrics:
            raise ValueError(f"shape_metrics[{shape!r}] must be a non-empty list")
        unknown = sorted(set(metrics) - catalog.metric_ids)
        if unknown:
            raise ValueError(f"unknown metric(s) in shape_metrics[{shape!r}]: {unknown}")
    seen_ids: set[str] = set()
    routed: set[str] = set()
    for rule in rules.get("rules", []):
        if set(rule) != {"id", "priority", "conditions", "reason"} | ({"shape"} if "shape" in rule else {"status"}):
            raise ValueError(f"invalid rule fields for {rule.get('id')!r}")
        rule_id = rule["id"]
        if rule_id in seen_ids:
            raise ValueError(f"duplicate rule id {rule_id!r}")
        seen_ids.add(rule_id)
        if not _is_int(rule["priority"]):
            raise ValueError(f"priority must be an integer for {rule_id}")
        if "shape" in rule:
            if rule["shape"] not in templates:
                raise ValueError(f"unknown shape {rule['shape']!r} in {rule_id}")
            routed.add(rule["shape"])
        elif rule["status"] not in {"unsupported", "ambiguous"}:
            raise ValueError(f"invalid status in {rule_id}")
        if not isinstance(rule["reason"], str) or not rule["reason"]:
            raise ValueError(f"missing reason in {rule_id}")
        for condition in rule["conditions"]:
            if set(condition) - {"op", "feature", "value"} or "op" not in condition or "feature" not in condition:
                raise ValueError(f"invalid condition in {rule_id}")
            op = condition["op"]
            feature = condition["feature"]
            if op not in OPERATORS:
                raise ValueError(f"unknown operator {op!r} in {rule_id}")
            if feature not in feature_names:
                raise ValueError(f"unknown feature {feature!r} in {rule_id}")
            if op in {"present", "absent"} and "value" in condition:
                raise ValueError(f"{op} takes no value in {rule_id}")
            if op in {"equals", "in", "count_eq", "gt"} and "value" not in condition:
                raise ValueError(f"{op} requires a value in {rule_id}")
            if op in {"count_eq", "gt"} and not _is_int(condition["value"]):
                raise ValueError(f"{op} requires an integer in {rule_id}")
            error = _condition_value(condition, feature, catalog)
            if error:
                raise ValueError(f"{error} in {rule_id}")
    not_routed = rules.get("not_routed", {})
    if not isinstance(not_routed, dict):
        raise ValueError("not_routed must be an object")
    if set(not_routed) & routed:
        raise ValueError("a shape cannot be both routed and not_routed")
    missing = set(templates) - routed - set(not_routed)
    if missing:
        raise ValueError(f"template coverage is incomplete: {sorted(missing)}")
    if set(not_routed) - set(templates):
        raise ValueError("not_routed contains an unknown shape")
    for shape, reason in not_routed.items():
        if not isinstance(reason, str) or not reason:
            raise ValueError(f"not_routed reason missing for {shape}")


def _present(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, (tuple, list, dict, str)) and len(value) == 0:
        return False
    return True


def _matches(condition: dict, features: Features) -> bool:
    actual = getattr(features, condition["feature"])
    op = condition["op"]
    if op == "present":
        return _present(actual)
    if op == "absent":
        return not _present(actual)
    if op == "equals":
        return actual == condition["value"]
    if op == "in":
        return actual in condition["value"]
    if op == "count_eq":
        return isinstance(actual, (tuple, list, dict, str)) and len(actual) == condition["value"]
    if op == "gt":
        return isinstance(actual, (int, float)) and not isinstance(actual, bool) and actual > condition["value"]
    raise ValueError(f"unknown operator {op!r}")


def route(features: Features, rules: dict) -> RouteResult:
    """Evaluate complete priority levels in ascending order, never file order alone."""
    ordered = sorted(rules["rules"], key=lambda rule: (rule["priority"], rule["id"]))
    index = 0
    while index < len(ordered):
        priority = ordered[index]["priority"]
        level: list[dict] = []
        while index < len(ordered) and ordered[index]["priority"] == priority:
            level.append(ordered[index])
            index += 1
        matches = [rule for rule in level if all(_matches(c, features) for c in rule["conditions"])]
        if not matches:
            continue
        if len(matches) > 1:
            ids = ",".join(rule["id"] for rule in matches)
            return RouteResult("ambiguous", None, None, f"multiple rules matched at priority {priority}: {ids}")
        rule = matches[0]
        if "status" in rule:
            return RouteResult(rule["status"], None, rule["id"], rule["reason"])
        return RouteResult("ok", rule["shape"], rule["id"], rule["reason"])
    return RouteResult("unsupported", None, None, "no rule matched")


def _period_json(period: Period) -> dict:
    slots: dict[str, Any] = {}
    if period.month is not None:
        slots["month"] = period.month
    slots["year"] = period.year if period.year is not None else YEAR_FALLBACK
    if period.date is not None:
        slots["date"] = period.date
    if period.start is not None:
        slots["start"] = period.start
    if period.end is not None:
        slots["end"] = period.end
    return {"kind": period.kind, "slots": slots}


def _slot_value(name: str, features: Features) -> Any:
    first = features.periods[0] if features.periods else Period()
    second = features.periods[1] if len(features.periods) > 1 else Period()
    simple = {
        "metric": features.metric,
        "unit": features.unit,
        "month": first.month,
        "year": first.year if first.year is not None else YEAR_FALLBACK,
        "month_a": first.month,
        "year_a": first.year if first.year is not None else YEAR_FALLBACK,
        "month_b": second.month,
        "year_b": second.year if second.year is not None else YEAR_FALLBACK,
        "per": features.per,
        "group_by": features.group,
        "k": features.k,
        "comparator": features.comparator,
        "threshold": features.threshold,
        "date": features.date,
        "start": features.start,
        "end": features.end,
        "stock_comparator": features.stock_comparator,
        "stock_value": features.stock_value,
    }
    if name == "periods":
        return [_period_json(period) for period in features.periods] or None
    if name not in simple:
        raise KeyError(f"no source for slot {name!r}")
    return simple[name]


def _validate_slot(name: str, value: Any, catalog: CanonicalCatalog) -> Optional[str]:
    if name == "metric" and value not in catalog.metric_ids:
        return f"unknown canonical metric {value!r}"
    if name in {"comparator", "stock_comparator"} and value not in catalog.comparator_ids:
        return f"unknown comparator {value!r}"
    if name == "group_by" and value not in catalog.grouping_ids:
        return f"unknown grouping {value!r}"
    if name == "per" and value not in catalog.metric_ids | catalog.grouping_ids:
        return f"unknown per grouping {value!r}"
    if name == "unit" and value is not None and not isinstance(value, str):
        return "unit must be a string or null"
    if name == "k" and (not _is_int(value) or value < 1):
        return f"k must be a positive integer, got {value!r}"
    if name in {"month", "month_a", "month_b"} and (not _is_int(value) or not 1 <= value <= 12):
        return f"month must be in 1..12, got {value!r}"
    if name in {"year", "year_a", "year_b"}:
        if value != YEAR_FALLBACK and (not _is_int(value) or not 1 <= value <= 9999):
            return f"year must be an ISO year or sheet_rule, got {value!r}"
    if name in {"date", "start", "end"} and not _valid_date(value):
        return f"date must be YYYY-MM-DD, got {value!r}"
    if name == "threshold" and (not _is_int(value) or value <= 0):
        return f"threshold must be a positive integer, got {value!r}"
    if name == "stock_value" and (not _is_int(value) or value < 0):
        return f"stock_value must be a non-negative integer, got {value!r}"
    if name == "periods":
        if not isinstance(value, list) or not value:
            return "periods must be a non-empty list"
        for period in value:
            if not isinstance(period, dict) or set(period) != {"kind", "slots"}:
                return "each period must have kind and slots"
            if period["kind"] not in catalog.time_kind_ids:
                return f"unknown period time kind {period['kind']!r}"
            for slot_name, slot_value in period["slots"].items():
                if slot_name in {"month"} and (not _is_int(slot_value) or not 1 <= slot_value <= 12):
                    return f"month must be in 1..12, got {slot_value!r}"
                if slot_name == "year" and slot_value != YEAR_FALLBACK and not _is_int(slot_value):
                    return f"invalid year {slot_value!r}"
                if slot_name in {"date", "start", "end"} and not _valid_date(slot_value):
                    return f"date must be YYYY-MM-DD, got {slot_value!r}"
    return None


def _fill_slots(node: Any, features: Features, catalog: CanonicalCatalog,
                missing: list[str], errors: list[str]) -> Any:
    if isinstance(node, dict):
        if set(node) == {"slot"}:
            name = node["slot"]
            value = _slot_value(name, features)
            if value is None and name != "unit":
                missing.append(name)
                return None
            error = _validate_slot(name, value, catalog)
            if error:
                errors.append(error)
            return copy.deepcopy(value)
        return {key: _fill_slots(value, features, catalog, missing, errors) for key, value in node.items()}
    if isinstance(node, list):
        return [_fill_slots(value, features, catalog, missing, errors) for value in node]
    return node


def _leftover_slots(node: Any) -> bool:
    if isinstance(node, dict):
        if set(node) == {"slot"}:
            return True
        return any(_leftover_slots(value) for value in node.values())
    if isinstance(node, list):
        return any(_leftover_slots(value) for value in node)
    return False


def _required_concepts(body: dict, catalog: CanonicalCatalog) -> list[str]:
    found: set[str] = set()

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in {"metric", "field", "group_by", "per", "entity"} and isinstance(value, str):
                    if value in catalog.concept_ids:
                        found.add(value)
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(body)
    return sorted(found)


class ShapeRouter:
    def __init__(self, templates_path: str | Path, rules_path: str | Path, dictionary_path: str | Path):
        self.templates_path = Path(templates_path)
        self.rules_path = Path(rules_path)
        self.templates = load_templates(self.templates_path)
        self.rules = json.loads(self.rules_path.read_text(encoding="utf-8"))
        self.catalog = CanonicalCatalog.from_path(dictionary_path)
        validate_rules(self.rules, self.templates, self.catalog)

    def adapter_from_matching(self, obj: dict) -> Adaptation:
        return _adapter_from_matching(obj, self.catalog)

    def instantiate(self, template: dict, features: Features) -> tuple[Optional[dict], Optional[str]]:
        shape = template["shape"]
        accepted = self.rules["shape_metrics"][shape]
        if features.metric not in accepted:
            return None, f"metric incompatible: shape {shape!r} does not accept {features.metric!r}"
        template_metric = template["common"].get("metric")
        if isinstance(template_metric, str) and template_metric != features.metric:
            return None, (
                f"template metric {template_metric!r} does not match question metric {features.metric!r}"
            )
        missing: list[str] = []
        errors: list[str] = []
        body_source = {key: value for key, value in template.items() if key not in {"shape", "status", "suggested_new"}}
        body = _fill_slots(body_source, features, self.catalog, missing, errors)
        if missing:
            return None, "unfilled slots: " + ", ".join(sorted(set(missing)))
        if errors:
            return None, "; ".join(sorted(set(errors)))
        if _leftover_slots(body):
            return None, "leftover slot object"
        return body, None

    def process(self, question_id: str, business_id: str, as_of_date: str,
                features: Features) -> dict:
        base = {
            "question_id": question_id,
            "business_id": business_id,
            "as_of_date": as_of_date,
            "status": "unsupported",
            "shape": None,
            "body": None,
            "required_concepts": [],
            "rule_id": None,
            "reason": None,
            "trace": list(features.trace),
        }
        current = features
        if current.ranking is not None and current.k is None and current.superlative_term == "nhat":
            current = replace(
                current,
                k=1,
                trace=current.trace + ("default:k=1 because superlative term is 'nhất'",),
            )
        base["trace"] = list(current.trace)
        selected = route(current, self.rules)
        base["rule_id"] = selected.rule_id
        base["trace"].append("route:" + (selected.rule_id or "no_rule"))
        if selected.status != "ok":
            base["status"] = selected.status
            base["reason"] = selected.reason
            if selected.reason == "no rule matched" and current.metric and current.answer_type:
                compatible = any(
                    current.metric in self.rules["shape_metrics"][shape]
                    and template["answer"]["type"] == current.answer_type
                    and "per" not in template["shape_fields"]
                    for shape, template in self.templates.items()
                )
                if not compatible:
                    base["reason"] = f"no template: {current.metric} {current.answer_type}"
            return base
        base["shape"] = selected.shape
        template = self.templates.get(selected.shape)
        if template is None:
            base["reason"] = f"no template: {selected.shape}"
            return base
        body, error = self.instantiate(template, current)
        base["trace"].append("instantiate")
        if error:
            base["reason"] = error
            return base
        base["status"] = "ok"
        base["body"] = body
        base["required_concepts"] = _required_concepts(body, self.catalog)
        base["reason"] = None
        return base

    def process_matching(self, business_id: str, as_of_date: str, obj: dict) -> dict:
        matching = obj.get("matching", obj) if isinstance(obj, dict) else {}
        question_id = matching.get("question_id", "") if isinstance(matching, dict) else ""
        adaptation = self.adapter_from_matching(obj)
        if adaptation.status != "ok":
            return {
                "question_id": question_id,
                "business_id": business_id,
                "as_of_date": as_of_date,
                "status": "unsupported",
                "shape": None,
                "body": None,
                "required_concepts": [],
                "rule_id": None,
                "reason": adaptation.reason or "adapter rejected matcher output",
                "trace": ["adapter"],
            }
        return self.process(question_id, business_id, as_of_date, adaptation.features)


def _adapter_from_matching(obj: Any, catalog: CanonicalCatalog) -> Adaptation:
    if not isinstance(obj, dict):
        return _unsupported("matcher output must be an object")
    extraction = None
    if "matching" in obj or "extraction" in obj:
        unknown = sorted(set(obj) - {"matching", "extraction"})
        if unknown:
            return _unsupported("unknown field(s): " + ", ".join(unknown))
        if "matching" not in obj:
            return _unsupported("missing required field(s): matching")
        matching = obj["matching"]
        extraction = obj.get("extraction")
    else:
        matching = obj
    if not isinstance(matching, dict):
        return _unsupported("matching must be an object")
    unknown = sorted(set(matching) - MATCHING_FIELDS)
    if unknown:
        return _unsupported("unknown field(s): " + ", ".join(unknown))
    missing = sorted(REQUIRED_MATCHING_FIELDS - set(matching))
    if missing:
        return _unsupported("missing required field(s): " + ", ".join(missing))
    if not isinstance(matching["question_id"], str) or not matching["question_id"]:
        return _unsupported("missing required field(s): question_id")
    if extraction is not None:
        extraction_error = _validate_extraction(extraction, matching["question_id"])
        if extraction_error:
            return _unsupported(extraction_error)
    matcher_status = matching["status"]
    if matcher_status not in {"ok", "unsupported", "ambiguous", "unanswerable"}:
        return _unsupported("unknown matcher status " + repr(matcher_status))
    metric = matching["metric"]
    if metric is not None and metric not in catalog.metric_ids:
        return _unsupported("unknown canonical metric " + repr(metric))
    if matcher_status != "ok":
        reason = matching.get("reason") or matching.get("step") or "matcher not routable"
        return _unsupported(f"matcher {matcher_status}: {reason}")
    if metric is None:
        return _unsupported("missing required field(s): metric")
    answer_type = matching["answer_type"]
    if answer_type not in ANSWER_TYPES:
        return _unsupported("unknown answer type " + repr(answer_type))

    entity_concepts: list[str] = []
    if not isinstance(matching["entities"], list):
        return _unsupported("entities must be a list")
    for entity in matching["entities"]:
        if not isinstance(entity, dict) or set(entity) != {"span", "concept", "bound_key"}:
            return _unsupported("unknown or missing field in entities entry")
        if entity["concept"] not in catalog.metric_ids:
            return _unsupported("unknown canonical concept " + repr(entity["concept"]))
        if not isinstance(entity["bound_key"], str):
            return _unsupported("entity bound_key must be a string")
        entity_concepts.append(entity["concept"])

    concepts = matching["concepts"]
    if not isinstance(concepts, dict):
        return _unsupported("concepts must be an object")
    for concept, binding in concepts.items():
        if concept not in catalog.metric_ids:
            return _unsupported("unknown canonical concept " + repr(concept))
        if not isinstance(binding, dict) or set(binding) - {"header", "source", "tables"}:
            return _unsupported("unknown field in concepts binding")
        if not {"header", "source"} <= set(binding):
            return _unsupported("missing required field in concepts binding")

    absent = matching["absent"]
    if not isinstance(absent, list):
        return _unsupported("absent must be a list")
    for item in absent:
        if not isinstance(item, dict) or set(item) != {"concept", "reason"}:
            return _unsupported("unknown or missing field in absent entry")
        for concept in item["concept"].split("|"):
            if concept not in catalog.metric_ids:
                return _unsupported("unknown canonical concept " + repr(concept))
        if item["reason"] not in {"declared", "unmapped"}:
            return _unsupported("unknown absence reason " + repr(item["reason"]))

    status_map = matching["status_map"]
    if not isinstance(status_map, dict):
        return _unsupported("status_map must be an object")
    for canonical in status_map.values():
        if canonical not in catalog.status_ids:
            return _unsupported("unknown canonical status " + repr(canonical))
    exclusions = matching.get("exclusions", [])
    if not isinstance(exclusions, list) or any(value not in catalog.status_ids for value in exclusions):
        return _unsupported("unknown canonical status in exclusions")

    units = matching["units"]
    if not isinstance(units, list):
        return _unsupported("units must be a list")
    for unit in units:
        if not isinstance(unit, dict) or set(unit) != {"product", "unit", "factor", "base_unit"}:
            return _unsupported("unknown or missing field in units entry")
        if not isinstance(unit["unit"], str):
            return _unsupported("unit must be a string")
    if len(units) > 1:
        return _unsupported("multiple units cannot fill one unit slot")
    unit = units[0]["unit"] if units else None

    filter_concepts: list[str] = []
    comparator = None
    threshold = None
    stock_comparator = None
    stock_value = None
    status_filter = None
    filters = matching["filters"]
    if not isinstance(filters, list):
        return _unsupported("filters must be a list")
    for filter_item in filters:
        if not isinstance(filter_item, dict) or set(filter_item) != {"concept", "op", "value"}:
            return _unsupported("unknown or missing field in filters entry")
        concept = filter_item["concept"]
        op = filter_item["op"]
        value = filter_item["value"]
        if concept not in catalog.metric_ids:
            return _unsupported("unknown canonical filter concept " + repr(concept))
        if op is not None and op not in catalog.comparator_ids:
            return _unsupported("unknown comparator " + repr(op))
        filter_concepts.append(concept)
        if concept == "order_status":
            if value not in catalog.status_ids:
                return _unsupported("unknown canonical status " + repr(value))
            status_filter = value
        elif concept == "debt_balance":
            if comparator is not None:
                return _unsupported("multiple debt comparators are ambiguous")
            comparator, threshold = op, value
        elif concept == "stock_end_qty":
            stock_comparator, stock_value = op, value
        elif op is not None:
            if comparator is not None:
                return _unsupported("multiple comparators are ambiguous")
            comparator, threshold = op, value

    time_value = matching["time"]
    time_kind = None
    date = start = end = None
    periods: tuple[Period, ...] = ()
    trace: list[str] = []
    if time_value is not None:
        if not isinstance(time_value, dict) or set(time_value) != {"kind", "from", "to", "coverage"}:
            return _unsupported("unknown or missing field in time")
        time_kind, error = catalog.time_kind(time_value["kind"])
        if error:
            return _unsupported(error)
        if time_kind != time_value["kind"]:
            trace.append(f"time_kind:{time_value['kind']}->{time_kind}")
        start, end = time_value["from"], time_value["to"]
        if not _valid_date(start) or not _valid_date(end):
            return _unsupported("time from/to must be YYYY-MM-DD")
        if time_value["coverage"] not in {"full", "partial"}:
            return _unsupported("unknown time coverage " + repr(time_value["coverage"]))
        if time_kind in {"as_of", "time:day"}:
            date = end
        month = dt.date.fromisoformat(start).month if time_kind == "time:month" else None
        year = dt.date.fromisoformat(start).year if time_kind == "time:month" else None
        periods = (Period(time_kind, month, year, date, start, end),)

    ranking_value = matching["ranking"]
    ranking = None
    k = None
    if ranking_value is not None:
        if not isinstance(ranking_value, dict) or set(ranking_value) != {"criterion", "direction", "k"}:
            return _unsupported("unknown or missing field in ranking")
        criterion = ranking_value["criterion"]
        if criterion not in catalog.metric_ids:
            return _unsupported("unknown ranking criterion " + repr(criterion))
        if ranking_value["direction"] not in {"asc", "desc"}:
            return _unsupported("unknown ranking direction " + repr(ranking_value["direction"]))
        k = ranking_value["k"]
        if k is not None and (not _is_int(k) or k < 1):
            return _unsupported(f"k must be a positive integer, got {k!r}")
        ranking = dict(ranking_value)

    superlative_term = None
    if extraction is not None:
        superlatives = [span for span in extraction["spans"] if span["category"] == "superlative"]
        if len(superlatives) > 1:
            return _unsupported("multiple superlative readings are ambiguous")
        if superlatives:
            superlative_term = _fold(superlatives[0]["surface"])

    if not isinstance(matching["defaults_overridden"], list) or not isinstance(matching["trace"], list):
        return _unsupported("defaults_overridden and trace must be lists")
    if not isinstance(matching["default_flagged"], bool):
        return _unsupported("default_flagged must be boolean")
    features = Features(
        metric=metric,
        answer_type=answer_type,
        ranking=ranking,
        k=k,
        superlative_term=superlative_term,
        comparator=comparator,
        threshold=threshold,
        periods=periods,
        time_kind=time_kind,
        date=date,
        start=start,
        end=end,
        unit=unit,
        stock_comparator=stock_comparator,
        stock_value=stock_value,
        entity_concepts=tuple(sorted(set(entity_concepts))),
        filter_concepts=tuple(sorted(set(filter_concepts))),
        status_filter=status_filter,
        trace=tuple(trace),
    )
    return Adaptation(status="ok", features=features)


def _validate_extraction(extraction: Any, question_id: str) -> Optional[str]:
    if not isinstance(extraction, dict):
        return "extraction must be an object"
    unknown = sorted(set(extraction) - EXTRACTION_FIELDS)
    if unknown:
        return "unknown extraction field(s): " + ", ".join(unknown)
    missing = sorted(EXTRACTION_FIELDS - set(extraction))
    if missing:
        return "missing required extraction field(s): " + ", ".join(missing)
    if extraction["question_id"] != question_id:
        return "extraction and matching question_id differ"
    if not isinstance(extraction["spans"], list) or not isinstance(extraction["flags"], list):
        return "extraction spans and flags must be lists"
    for span in extraction["spans"]:
        if not isinstance(span, dict):
            return "extraction span must be an object"
        unknown = sorted(set(span) - SPAN_FIELDS)
        if unknown:
            return "unknown extraction span field(s): " + ", ".join(unknown)
        required = {"start", "end", "orig_start", "orig_end", "surface", "category", "term_id", "candidates", "rulebook_ref"}
        if required - set(span):
            return "missing required extraction span field"
        if span["category"] not in SPAN_CATEGORIES:
            return "unknown extraction span category " + repr(span["category"])
    return None


def adapter_from_matching(obj: dict, *, dictionary_path: str | Path) -> Adaptation:
    """Adapt only documented matcher/extraction fields; reject unknowns without guessing.

    NOT USED by the feature path. The CLI (main) and feature_extractor.py route Features directly,
    so this adapter is kept only for the matcher contract (role_analysis.md Prompt 4) and has no caller here.
    """
    catalog = CanonicalCatalog.from_path(dictionary_path)
    return _adapter_from_matching(obj, catalog)


def dumps(value: Any) -> str:
    """Canonical JSON used by determinism tests and byte-level comparisons."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def features_from_dict(d: dict) -> Features:
    """Build Features from a features.jsonl 'features' object. Unknown keys are rejected, not dropped."""
    unknown = sorted(set(d) - set(Features.__dataclass_fields__))
    if unknown:
        raise ValueError("unknown Features field(s): " + ", ".join(unknown))
    values = dict(d)
    values["periods"] = tuple(Period(**p) for p in d.get("periods", ()))
    values["entity_concepts"] = tuple(d.get("entity_concepts", ()))
    values["filter_concepts"] = tuple(d.get("filter_concepts", ()))
    values["trace"] = tuple(d.get("trace", ()))
    return Features(**values)


def main(argv: Optional[list[str]] = None) -> int:
    """Route a features.jsonl file (written by feature_extractor.py) into routes.jsonl.

    Each input line carries an envelope status. Non-ok envelopes are passed through with that status,
    so the router never upgrades an extractor 'ambiguous' or 'unsupported' into a shape.
    """
    import argparse
    import sys
    from collections import Counter

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--features", required=True)
    ap.add_argument("--templates", required=True)
    ap.add_argument("--rules", required=True)
    ap.add_argument("--dictionary", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    router = ShapeRouter(a.templates, a.rules, a.dictionary)
    status, reasons = Counter(), Counter()
    lines = []
    for raw in Path(a.features).read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        rec = json.loads(raw)
        feats = features_from_dict(rec["features"])
        if rec["status"] == "ok":
            out = router.process(rec["question_id"], rec["business_id"], rec["as_of_date"], feats)
        else:
            out = {"question_id": rec["question_id"], "business_id": rec["business_id"],
                   "as_of_date": rec["as_of_date"], "status": rec["status"], "shape": None,
                   "body": None, "required_concepts": [], "rule_id": None,
                   "reason": rec["reason"], "trace": list(feats.trace)}
        out["free_tokens"] = rec.get("free_tokens", [])
        status[out["status"]] += 1
        if out["reason"]:
            reasons[out["reason"]] += 1
        lines.append(dumps(out))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"routed: {sum(status.values())}  status: {dict(status)}", file=sys.stderr)
    for reason, c in reasons.most_common(10):
        print(f"  {c:5d}  {reason}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
