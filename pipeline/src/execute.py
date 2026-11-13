"""Execution with fixed helpers only (pipeline spec: EXECUTION). No free-form code.

Each helper returns its output and a call record: {"code": "src/execute.py::<helper>", "inputs": ..., "output": ...}.
Revenue = sum of line totals over orders whose status is not cancelled or returned (rulebook §1).
An order is excluded as a whole when any of its rows carries an excluded status.
"""
import bisect
import datetime as dt

from match import EXCLUDED_CANONICAL


def _call(calls, name, inputs, output):
    calls.append({"code": "src/execute.py::" + name, "inputs": inputs, "output": output})


def filter_units(units, dates, start, end, status_of, product_key, calls):
    """Keep units dated in [start, end] whose order is not excluded, optionally for one product.
    `units` is sorted by date and `dates` holds the matching dates (both built once per business)."""
    lo = bisect.bisect_left(dates, start)
    hi = bisect.bisect_right(dates, end)
    inwin = units[lo:hi]
    excluded_orders = set()
    for u in inwin:
        canon = status_of(u["status_raw"])
        if u["order_key"] is not None and canon in EXCLUDED_CANONICAL:
            excluded_orders.add(u["order_key"])
    missing = sum(1 for u in inwin if u["status_raw"] is None)
    if missing:
        raise ValueError("missing status on %d row(s) in the window" % missing)
    kept = []
    for u in inwin:
        canon = status_of(u["status_raw"])
        if canon in EXCLUDED_CANONICAL:
            continue
        if u["order_key"] is not None and u["order_key"] in excluded_orders:
            continue
        if product_key is not None and u["product_key"] != product_key:
            continue
        kept.append(u)
    _call(calls, "filter_units", {"window": [start.isoformat(), end.isoformat()], "product_key": product_key,
                                  "rows_in_window": len(inwin), "excluded_orders": len(excluded_orders)},
          {"rows_kept": len(kept)})
    return kept


def aggregate_sum(units, calls):
    total = 0.0
    for u in units:
        total += u["amount"] or 0.0
    _call(calls, "aggregate_sum", {"rows": len(units), "field": "amount"}, {"sum": total})
    return total


def count_orders(units, calls):
    keys = {u["order_key"] for u in units if u["order_key"] is not None}
    missing = sum(1 for u in units if u["order_key"] is None)
    _call(calls, "count_orders", {"rows": len(units), "rows_without_order_key": missing},
          {"count": len(keys)})
    return len(keys)


def format_number(value, calls):
    """Money and counts: plain integer, nearest đồng / unit (rulebook §9)."""
    out = int(round(value))
    _call(calls, "format_number", {"value": value}, {"answer": out})
    return out


def execute(spec, indexed, status_of, calls):
    """Run the closed spec. `indexed` = (units sorted by date, their dates). Returns {'type', 'answer'}."""
    if spec["status"] != "ok":
        raise ValueError("spec not ok")
    units, dates = indexed
    t = spec["time"]
    start = dt.date.fromisoformat(t["from"])
    end = dt.date.fromisoformat(t["to"])
    product_key = spec["entities"][0]["bound_key"] if spec["entities"] else None
    rows = filter_units(units, dates, start, end, status_of, product_key, calls)
    if spec["metric"] == "revenue":
        value = aggregate_sum(rows, calls)
    elif spec["metric"] == "order_count":
        value = count_orders(rows, calls)
    else:
        raise ValueError("unknown metric " + str(spec["metric"]))
    return {"type": spec["answer_type"], "answer": format_number(value, calls)}
