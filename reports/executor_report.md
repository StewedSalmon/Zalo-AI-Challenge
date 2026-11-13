# Value executor report

## Version 1

- Code: `src/executor/` (`reader.py`, `ingest.py`, `aggregate.py`, `run.py`), mappings in `src/executor/mappings/`
- Tests: `tests/test_executor.py`, fixtures in `tests/fixtures/executor/`
- Routes used: `routes.jsonl` from the router at rules v3, templates 27, cues v4 (filler list)
- Date: 2026-11-13
- Scope: the six aggregate-family shapes (`aggregate`, `aggregate_day`, `aggregate_range`, `count`, `count_day`, `count_range`), dev businesses only
- No file under `train/`, `pipeline/`, the router, the extractor or the templates was changed.

### How to run

```powershell
python src\executor\run.py --routes <routes.jsonl> --answers train\answers.jsonl --questions train\questions.jsonl --data train\data --split src\splits\split_seed20260512.json --part dev --businesses biz_110,biz_108,biz_119,biz_120,biz_128 --out <dir>
```

Each run takes about 100 seconds, mostly reading the large xlsx files. Latest dev run output (this version): `C:\Users\Admin\AppData\Local\Temp\opencode\exec3\` (`log.jsonl`, `errors.jsonl`, `summary.json`). The temp folder is not part of the repo.

### What the executor does

1. Reads the route's filled body (period, metric, exclusions) and the business's data files, using only the standard library (csv, xlsx via zipfile, sqlite).
2. Applies the rulebook defaults the templates do not carry: huỷ and trả hàng orders are out, đang giao is in; revenue is the order total (`Tổng tiền hàng`, sum of lines before the order discount); period membership is by order date; records dated after `as_of_date` are ignored.
3. Returns `ok` with a value, `unanswerable` when the period is entirely outside the data window (§7), or `error` with a reason.
4. Writes one log line per question (`log.jsonl`) and one error line per error (`errors.jsonl`).

### Error log (per question)

Every routed question gets one line in `log.jsonl` with `status`, `value`, `gold`, `agree`, `error`, `elapsed_ms`. Questions with `status: error` also appear in `errors.jsonl`. Error categories this version can produce:

- `load: no mapping for <biz>`: business has no mapping file (no guessing).
- `N row(s) with unmapped status fall in the period`: a status value not in the mapping occurs inside the period (for example `Đổi trả`, `Đang xử lý`). The executor does not map it.
- `period ... is after as_of_date`, `unit slot ... set`, `body has common.filters`, `year is 'sheet_rule'`: out of scope or unresolved.

### Mappings (5 dev businesses)

| Business | File | Layout | Status values mapped | Left unmapped |
|---|---|---|---|---|
| biz_110 | danh_sach_hoa_don.csv | one row per order | Hoàn thành, Hoàn trả (trả hàng), khách hủy (huỷ), Chờ giao (đang giao) | none |
| biz_108 | DanhSachHoaDon.xlsx | one row per order, 1 sheet | Hoàn thành, Đã hủy, Đã trả hàng, Đang giao hàng | none (rows `Đã nhập hàng`, `Đang hoạt động` are not orders and are excluded) |
| biz_119 | DanhSachHoaDon.xlsx | one row per order, one sheet per month | Hoàn thành, Đã hủy, Đã trả hàng, Đang giao hàng | none |
| biz_120 | DanhSachHoaDon.xlsx | one row per order, one sheet per month | ok, Thành công (hoàn thành); Huỷ đơn, hủy đơn (huỷ); trả hàng, Đã trả hàng (trả hàng) | `Đang xử lý` (22 rows; not in any scored period) |
| biz_128 | DanhSachHoaDon.xlsx | one row per order, one sheet per month | xong, Thành công (hoàn thành); Huỷ đơn, hủy (huỷ); hàng trả về, Đổi trả (trả hàng); Đang giao hàng | none |

Assumptions and evidence:
- biz_120 `ok` is mapped to hoàn thành. The README does not define it. Gold agreement on the scored biz_120 questions supports it; the one biz_120 disagreement is a router error (see below).
- biz_128 `Đổi trả` is mapped to trả hàng. The rulebook has only four statuses. Two scored questions (revenue for 2026-04, count for Q1 2026) agree with gold only when these rows are excluded. That is a dev-gold-informed decision, so it is recorded here as an assumption, not a rulebook fact.
- Unmapped statuses are kept out of `status_map` on purpose. The executor flags a question with `error` only when an unmapped row falls inside its period.

Correction made during the run: the first generator wrote unmapped statuses as `null`, which the ingest reads as "not a sales order", so they were dropped silently. The fix removes them from `status_map`. Re-running gave the flags above (4 questions became errors), and the `Đổi trả` decision was then made on that evidence.

### Results

Dev only. Routed six-shape questions in the five mapped businesses: 76.

| Measure | Count |
|---|---|
| Executor status ok | 76 |
| Executor error | 0 |
| Value agrees with gold | 65 |
| Value disagrees with gold | 11 |
| Gold unanswerable | 0 |

Per business (agree / disagree): biz_110 18/0, biz_108 9/3, biz_119 11/2, biz_120 10/1, biz_128 17/5. The disagreement set is the same 11 questions across all runs of this version.

Unit tests: 102 pass (89 before, plus 13 in `tests/test_executor.py`, hand-computed on the fixture).

### The 11 disagreements: router errors, not executor errors

Each disagreement is a question the router routed to a shape whose meaning does not match the question. The executor computed the value of the routed shape correctly.

| Count | Question pattern | Routed to | Cause |
|---|---|---|---|
| 7 | "Giá trị trung bình một đơn tháng M/Y" | `count` | The average per order is in `not_routed` (`average`), but the count rule accepts it. The order-count gate does not exclude "trung bình". |
| 4 | "... ca sáng ..." (morning shift) | `count` or `aggregate` | The shift is not a template field, and the question's shift term is dropped with no route change. The rulebook defines the shift (§2: 06:00 to 13:59). |

These are the same kind of error the rules must catch. Recommended fixes, not applied in this version because the router is out of scope:
1. Add an average-per-order cue to the router's exclusions for `count` and `aggregate`, so such questions fall to `average` (unsupported, since it is in `not_routed`).
2. Treat the shift terms (`ca sáng`, `ca chiều`) as a time feature the family does not support, so the question is unsupported instead of answered over the whole day.

### What is not covered

- **Dev, unmapped:** 175 of 251 dev six-shape routes (16 businesses) have no mapping. Their layouts are: title-row sheets (biz_112, biz_118, biz_130), line-level sales tables with a repeated order id (biz_104, biz_113, biz_121), sqlite (biz_123), three-file splits with a separate product file (biz_115, biz_127), and others. Each needs its own mapping and, for line-level tables, a separate revenue path (sum of line amounts, not the order total).
- **Held-out:** 124 six-shape routes in 9 businesses. No held-out business has a mapping. Writing one would mean inspecting held-out layouts before the single allowed run, so the held-out run was not done. The acceptance rule proposed in the plan (held-out agreement within 5 points of dev, every disagreement explained) has not been applied.
- Values for percent, date, list, table, string shapes: not executed.
- Entity questions and quantity_base questions: not executed (out of the six-shape family).

### Limits and caveats

- Dev agreement is measured on the businesses whose mappings were written after looking at their dev layouts. The 65 agreements are optimistic for those five businesses only.
- The 65 agreements are on value; the answer type was already checked before.
- Mapping `verified` is false in every mapping file; this is a deliberate flag, since only the 76 questions above were checked.
