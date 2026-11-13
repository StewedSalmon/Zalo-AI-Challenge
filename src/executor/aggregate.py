"""Value executor for the six aggregate-family shapes: aggregate, aggregate_day, aggregate_range,
count, count_day, count_range.

Inputs: a routed body (route record's "body", filled by shape_router.py) and an OrderTable (ingest.py).
Outputs: ExecResult with status ok | unanswerable | error, the value, and a reason when not ok.

Rulebook defaults applied here (the templates do not carry them):
  §1  revenue and counts use orders whose status is hoàn thành or đang giao; huỷ and trả hàng are out.
  §1  revenue = order line total (Tổng tiền hàng), no order discount, no VAT, no delivery fee.
  §1  period membership is by order date.
  §2  records dated after as_of_date are treated as not yet happened.
  §7  a period entirely outside the data window is unanswerable; a partly covered period uses the covered part.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from .ingest import COUNTED, OrderTable

FAMILY = {"aggregate": "revenue", "aggregate_day": "revenue", "aggregate_range": "revenue",
          "count": "order_id", "count_day": "order_id", "count_range": "order_id"}


@dataclass
class ExecResult:
    status: str                 # ok | unanswerable | error
    value: Optional[int] = None
    answer_type: str = "number"
    error: Optional[str] = None
    detail: Optional[dict] = None


def _period(body: dict) -> tuple[date, date]:
    time = body["common"]["time"]
    kind, slots = time["kind"], time["slots"]
    if kind == "month":
        year, month = slots["year"], slots["month"]
        if year == "sheet_rule":
            raise ValueError("year is 'sheet_rule': not resolved by the router")
        import calendar
        last = calendar.monthrange(year, month)[1]
        return date(year, month, 1), date(year, month, last)
    if kind == "day":
        d = date.fromisoformat(slots["date"])
        return d, d
    if kind == "range":
        return date.fromisoformat(slots["start"]), date.fromisoformat(slots["end"])
    raise ValueError(f"time kind {kind!r} is outside the six-shape family")


def execute(body: dict, shape: str, as_of: date, table: OrderTable) -> ExecResult:
    family = FAMILY.get(shape)
    if family is None:
        return ExecResult("error", error=f"shape {shape!r} is outside the six-shape family")
    if body["common"].get("filters"):
        return ExecResult("error", error="body has common.filters; not in this family")
    unit = body.get("shape_fields", {}).get("unit")
    if unit is not None:
        return ExecResult("error", error=f"unit slot {unit!r} set; unit questions are out of scope")
    try:
        start, end = _period(body)
    except (ValueError, KeyError) as exc:
        return ExecResult("error", error=f"period: {exc}")
    if table.data_start is None:
        return ExecResult("error", error="business has no dated orders")
    if end < table.data_start or start > table.data_end:
        return ExecResult("unanswerable", error=None,
                          detail={"reason": f"period {start}..{end} outside data {table.data_start}..{table.data_end}"})
    eff_end = min(end, as_of)
    if eff_end < start:
        return ExecResult("error", error=f"period {start}..{end} is after as_of_date {as_of}")
    affected = [d for d in table.unknown_days if d is None or start <= d <= eff_end]
    if affected:
        return ExecResult("error", error=f"{len(affected)} row(s) with unmapped status fall in the period: "
                          + ", ".join(f"{k!r}" for k, _ in table.unknown_status.most_common(5)))
    inside = [o for o in table.orders.values()
              if o.status in COUNTED and start <= o.day <= eff_end]
    if family == "revenue":
        total = sum(o.revenue for o in inside)
        return ExecResult("ok", value=int(total), answer_type="number",
                          detail={"orders": len(inside), "from": str(start), "to": str(eff_end)})
    return ExecResult("ok", value=len(inside), answer_type="number",
                      detail={"orders": len(inside), "from": str(start), "to": str(eff_end)})
