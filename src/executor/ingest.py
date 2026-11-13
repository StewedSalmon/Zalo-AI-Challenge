"""Per-business order ingestion for the six aggregate-family shapes. Standard library only.

A mapping (src/executor/mappings/<biz>.json) names the files, the columns and the raw-to-canonical
status map for one business. Nothing is guessed: a missing column or an unknown status is an error,
and rows that cannot be read are counted in the load report, not silently dropped.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from .reader import read_business

CANON_STATUS = {"hoàn thành", "huỷ", "trả hàng", "đang giao"}
COUNTED = {"hoàn thành", "đang giao"}          # rulebook §1: cancelled and returned orders are out
MAPPINGS_DIR = Path(__file__).resolve().parent / "mappings"
EXCEL_EPOCH = date(1899, 12, 30)


class IngestError(Exception):
    """Raised for business-level problems (no mapping, missing column)."""


def parse_date(value) -> Optional[date]:
    """dd/mm/yyyy, yyyy-mm-dd, 'yyyy-mm-dd hh:mm:ss', or an Excel serial number."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < 20000:
            return None
        return EXCEL_EPOCH + timedelta(days=int(value))
    text = str(value).strip()
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if m:
        return date(int(m[3]), int(m[2]), int(m[1]))
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        return date(int(m[1]), int(m[2]), int(m[3]))
    return None


def parse_money(value) -> Optional[int]:
    """Whole VND. Thousands separators ',' or '.' are removed; anything else raises ValueError."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"not money: {value!r}")
    if isinstance(value, (int, float)):
        return int(round(value))
    text = str(value).strip().replace(" ", "")
    if re.fullmatch(r"-?\d{1,3}([,.]\d{3})+", text):
        return int(re.sub(r"[,.]", "", text))
    if re.fullmatch(r"-?\d+(\.\d+)?", text):
        return int(round(float(text)))
    raise ValueError(f"not money: {value!r}")


@dataclass
class Order:
    order_id: str
    day: date
    status: str           # canonical
    revenue: int          # order line total (Tổng tiền hàng), VND


@dataclass
class OrderTable:
    business_id: str
    orders: dict = field(default_factory=dict)          # order_id -> Order
    data_start: Optional[date] = None
    data_end: Optional[date] = None
    report: Counter = field(default_factory=Counter)    # row-level counters for the load report
    unknown_status: Counter = field(default_factory=Counter)
    unknown_days: list = field(default_factory=list)    # dates (or None) of rows with unmapped status


def load_mapping(business_id: str, mappings_dir: Path = MAPPINGS_DIR) -> dict:
    path = mappings_dir / f"{business_id}.json"
    if not path.exists():
        raise IngestError(f"no mapping for {business_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_orders(business_id: str, data_root: Path, mappings_dir: Path = MAPPINGS_DIR) -> OrderTable:
    mp = load_mapping(business_id, mappings_dir)
    cols = mp["columns"]
    status_map = mp["status_map"]
    table = OrderTable(business_id)
    folder = data_root / business_id
    sheets = read_business(folder, files=mp["files"])
    seen_files = {s.file for s in sheets}
    missing_files = set(mp["files"]) - seen_files
    if missing_files:
        raise IngestError(f"mapped files not found: {sorted(missing_files)}")
    for s in sheets:
        for col in (cols["order_id"], cols["date"], cols["status"], cols["revenue"]):
            if col not in s.header:
                break
        else:
            _ingest_sheet(s, cols, status_map, table)
            continue
        table.report["sheets_skipped_no_columns"] += 1
    if table.report["sheets_read"] == 0:
        raise IngestError("no sheet has the mapped columns")
    for o in table.orders.values():
        table.data_start = o.day if table.data_start is None else min(table.data_start, o.day)
        table.data_end = o.day if table.data_end is None else max(table.data_end, o.day)
    return table


def _ingest_sheet(s, cols: dict, status_map: dict, table: OrderTable) -> None:
    table.report["sheets_read"] += 1
    for row in s.rows:
        table.report["rows_read"] += 1
        oid = row.get(cols["order_id"])
        if oid in (None, ""):
            table.report["rows_no_order_id"] += 1
            continue
        raw_status = row.get(cols["status"])
        key = "" if raw_status is None else str(raw_status).strip()
        day = parse_date(row.get(cols["date"]))
        if key not in status_map:
            # Not guessed: kept aside, and a question is flagged only if its period contains such a row.
            table.unknown_status[key] += 1
            table.unknown_days.append(day)
            table.report["rows_unknown_status"] += 1
            continue
        canon = status_map[key]
        if canon is None:
            table.report["rows_not_an_order"] += 1
            continue
        if day is None:
            table.report["rows_bad_date"] += 1
            continue
        try:
            revenue = parse_money(row.get(cols["revenue"]))
        except ValueError:
            table.report["rows_bad_money"] += 1
            continue
        if revenue is None:
            table.report["rows_no_revenue"] += 1
            continue
        sid = str(oid).strip()
        prev = table.orders.get(sid)
        if prev is not None:
            # Same order id seen again: identical rows are an export duplicate (rulebook §8), counted once.
            if (prev.day, prev.status, prev.revenue) == (day, canon, revenue):
                table.report["rows_duplicate_identical"] += 1
            else:
                table.report["orders_conflicting_duplicate"] += 1
            continue
        table.orders[sid] = Order(sid, day, canon, revenue)
