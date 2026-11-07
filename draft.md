# DataMate: Draft End-to-End Process

Goal: for each question, return a typed answer (or `unanswerable` / `abstain`) in `submission.jsonl`, plus a per-question log. Docker and time limits are out of scope for this draft.

Design principles:
- The LLM proposes; deterministic code verifies. Every LLM output (mapping, rule, status label) is checked by code before use. Anything that fails verification is marked absent or unknown, never guessed.
- All arithmetic and filtering run as deterministic code, so numbers are reproducible and auditable.
- Answer only when the estimated probability of being correct is above 50% (see step 7).

Confirmed decisions:
- Model licenses: confirmed.
- Abstain threshold: 50%.
- Scoring: see "Scoring" below.

## Scoring

Per question (rulebook §9): correct +1, wrong −1, abstain 0. Unanswerable answered as a value counts as wrong (README).

The rulebook gives per-question values only. The aggregate formula is not written in the rulebook or the regulations PDF. Working assumption: Penalized Accuracy = (sum of scores) / (total questions). This must be checked against the leaderboard once results are published.

Consequence: answering has expected value p − (1 − p) = 2p − 1, which is positive only if p > 0.5. This matches the 50% threshold.

## Data scope

- Training: 30 businesses (`biz_101`–`biz_130`), 3000 questions.
- Public test: 14 businesses (`dev_001`–`dev_014`), 1000 questions.
- Private test: unseen businesses, possibly with business types absent from training.

Layout varies widely. Examples from the file list:
- Flat single table: `biz_101/ban_hang.csv`, `dev_009/danh_sach_hoa_don.csv`.
- Normalized multi-table CSVs: `biz_127`, `dev_010` (`don_hang`, `chi_tiet_don`, `khach_hang`, `san_pham`, `thanh_toan`).
- SQLite: `biz_123/pos.sqlite`, `dev_006/pos.sqlite`.
- Excel: `dev_001`, `dev_002` (monthly files, multiple sheets).

File names and column names are not reliable keys. The README varies per business.

The rulebook varies per split, not per business. Each split has one rulebook that applies to every question in that split:
- Training (`train/rulebook.md`) and public test (`public_test/rulebook.md`) differ. The public test version adds sector sections for Mẹ và bé, Sửa xe, and Cửa hàng điện thoại, and changes rules in §2, §3, and §5. For example, the training version has "Giá bán X ngày D" and "Tồn theo đơn vị lớn" in §5, which the public test version does not.
- The private test rulebook is not available. Rule code must load the split's rulebook at runtime and must not be tuned to the training rulebook text.

Because of this, no hand-written per-business schema works. Step 2 is a generic pipeline with LLM-assisted schema understanding and deterministic checks.

## Pipeline

### 1. Load inputs
- Read `questions.jsonl` (id, business_id, question, as_of_date).
- Load the split rulebook (`rulebook.md`). It applies to every question in the split.
- For each business, run step 2 on its folder.

Output: question list, split rulebook, per-business capability records.

### 2. Understand and normalize each business

Runs once per business at inference time, on the files in its folder. No cache across businesses, because private test businesses are unseen.

The split rulebook is the default for every question in the split. The business README overrides it for that business. So step 2 extracts the README's rules as well as its data layout.

#### 2.1 Inventory (deterministic)
- Walk the business folder recursively.
- Identify each file by content: sqlite by header (`SQLite format 3`), xlsx by zip structure, csv by text parsing. Do not rely on names.
- For each table (csv file, xlsx sheet, sqlite table), record: source file, sheet or table name, row count, headers, and 10 sample rows.
- Read `README.md` in full and keep it as text.

Output: inventory.

#### 2.2 Read raw (deterministic)
- Detect encoding and strip a UTF-8 BOM.
- Find the header row: the first row where most non-empty cells are text. Skip title rows above it.
- Forward-fill blanks in merged group columns (order-level fields) from the cell above. Apply only to group columns, not all columns.
- Record the header row index and the number of filled cells.

#### 2.3 Schema understanding (LLM-assisted, then verified)

Step A. For each table, the LLM receives the table's headers, sample rows, and the README text. It returns JSON, constrained to a fixed schema:
- `role`: one of order lines, orders, customers, products, stock, purchases, payments, debts, expenses, staff, service records, other;
- `grain`: one row per order line, per order, per event, or per record;
- `columns`: for each source column, the canonical concept it maps to, or `none`;
- `joins`: candidate keys to other tables (e.g. order id, product name, customer name);
- a self-reported confidence for each mapping.

The concept list is fixed. It is built from the column descriptions in the READMEs and the terms used in both rulebooks, not from one rulebook. For example: order_id, order_date, order_time, status, customer_code, customer_name, product_name, product_code, unit, quantity, unit_price, line_amount, order_amount, discount, cost_price, staff, payment_method, debt_amount, payment_date, import_date, supplier, stock_qty, expiry_date, lot, prescription_flag, plate_no, and others as needed.

Step B. Deterministic verification of each mapping. Examples:
- Date concept: most values parse as dates.
- Money or quantity concept: most values parse as numbers.
- Status concept: values are a small set, mappable to the four canonical statuses (step 2.4).
- Order grain: order_id repeats across lines of one order; order fields are identical within an order.
- Line amount: approximately quantity × unit_price (within a tolerance, after any discount).
- Order amount: equals the sum of its line amounts, when both exist.
- Joins: the candidate key matches the other table on most rows.

Each mapping gets a verification score (share of checks passed). Final confidence = verification score combined with the LLM self-report.

Step C. Repair loop. If a mapping fails, return the failing checks to the LLM and ask again. At most 2 retries.

Step D. Threshold. A concept below 50% confidence is marked absent. Working assumption: the same 50% threshold as answers. This is a choice, not a confirmed decision.

Output per business: table roles, mappings with scores, absent concepts, joins with match rates.

Model: **Qwen2.5-7B-Instruct** (see model summary). A larger model may be needed here if verification pass rates are low; test on training businesses first.

#### 2.4 Parse values (deterministic, with LLM-proposed mappings verified by code)
- **Numbers:** strip thousands separators. Decide the decimal format per column from all of its values (for example, all values matching `d{1,3}(.ddd)+` means dot is a thousands separator). Flag ambiguous columns.
- **Dates:** try the formats that parse most values in the column (`dd/mm/yyyy`, `d thg m yyyy`, `yyyy-mm-dd`, Excel serials). If a date has no year, use the sheet's year (rulebook §8). Keep the time of day when present.
- **Statuses:** the LLM proposes a mapping from the file's labels to the four canonical values (hoàn thành, huỷ, trả hàng, đang giao). Code checks that every label has exactly one target. Any unmapped label is flagged and never guessed.
- **Compound cells** (for example dev_002 toppings, joined with ` + `): the LLM proposes a parse spec (separator, pattern for extra parts, where money appears). Code applies it and checks the sum rule: line amount = main item amount + extra amounts. A spec that fails the check is rejected and the cell is left unsplit with a flag.

#### 2.5 Normalize entities (deterministic)
- **Products:** key = accent-free, lowercase, single-spaced text. Display name = the most frequent correctly accented spelling in the file. Log every merged variant (biz_101 has 70 spellings for 60 products).
- **Customers:** if the file has a customer code, use it. Otherwise use the name, following rulebook §3: differing only in spelling (case, accents, spacing) is the same customer; differing in characters or digits is a different customer.
- Inside an order, blank customer cells take the value from the first line of that order.
- Remaining `Khách lẻ` cells, and cells still blank, become walk-ins.
- Log every merge and rename.

#### 2.6 README rules (LLM-assisted, verified)
- **Unit conversions:** parse with a regex first. dev_001's format is `<product>: 1 <unit> = <n> <base unit>`. The LLM handles only lines that the regex does not match.
- Key each factor by **(product, unit name)**, not product alone. Examples from dev_001: Lifebuoy 90g has lốc = 6 and thùng = 72; Gạo ST25 has bao = 5 kg and bao 25kg = 25 kg.
- Check the file's converted-quantity column against the table. Record every disagreement.
- Policy: use the README factor. If the README has no entry for a (product, unit) pair but the file has a converted quantity, derive the factor as converted ÷ quantity and flag it. Units with no entry get factor 1, but a non-base unit with no entry is flagged.
- **Other conventions** (topping format, walk-ins, date format, data end date, year rules): the LLM extracts them as JSON using the rule IDs defined for the current split's rulebook. Each extracted rule must match a known rule ID, or it is logged and not applied.

#### 2.7 Dedupe (deterministic)
- Dedupe only tables with order grain (from 2.3).
- Within one order id, rows identical in every column count once (rulebook §8).
- Non-order tables keep every row.
- Dedupe across files too, not only within one file. An order id can repeat across monthly files (dev_002).
- Log the number of collapsed rows per table.

#### 2.8 Keep and record (deterministic)
- Keep all rows, including those after `as_of_date`. Filtering is step 5's job.
- Per table: row count, min and max date, concept availability, source files and sheets.
- Per business: data end date from the README and actual max date from the data. Flag any mismatch. Coverage per table lets questions outside the data range be marked `unanswerable` (rulebook §7).
- Log: every LLM output (prompt and response), every verification result, every mapping, every value rewrite, every row changed or removed. Each entry references source file, sheet or table, and row number.

Output:
- canonical tables;
- per-business capability record (roles, mappings with scores, absent concepts, joins, unit factors, rule IDs applied, flags, coverage);
- normalization log.

### 3. Parse the question
- Input: question text (may lack diacritics), as_of_date.
- Output: JSON spec with answer type, metric, entity, filters, ranking criterion, time expression, comparison period, required concepts (used by step 7), and an `answerable` hint.
- Use a constrained-JSON prompt with a fixed schema and a few training examples.
- Normalize diacritic-free input before parsing, or let the model handle it directly.

Models: **Qwen2.5-7B-Instruct**. Start with the smallest model that passes the training set.

### 4. Resolve time
- Convert the time expression to an interval using rulebook §2 (e.g. "tháng này" = day 1 to as_of; "tuần rồi" = Monday to Sunday; "quá N" exclusive; Tết window from `calendar.csv`).
- Output: target interval, comparison interval, and whether the metric is cumulative (balance) or in-period.

Models: none. Implement as rules in code. The LLM only outputs the expression text; code converts it.

### 5. Apply business rules
- Rule set: current split's rulebook defaults, overridden by the business README rules (2.6), overridden by the question wording.
- Sector sections (§6) apply only if the split's rulebook has a section for the business type. Otherwise the general rules apply.
- Filter facts: exclude cancelled and returned orders for revenue; use line totals (no order-level discount, no VAT, no delivery fee).
- Apply unit conversion (base unit, factor from the capability record).
- Apply entity rules (walk-in customers excluded from customer rankings; customer identity from 2.5).
- Apply sector rules when the business type matches (coffee sizes and toppings, pharmacy prescriptions and lots, repair-shop visit counting).
- Record which rules fired in the log trace.

Models: none.

### 6. Compute
- Run the computation as pandas or SQL on the filtered data (e.g. SUM, COUNT, GROUP BY, ORDER BY).
- Store raw results and intermediate values for the log.

Models: none. For hard or unfamiliar questions, a fallback is an LLM that writes pandas or SQL code, run in a sandbox, with the same validation. Suggested: **Qwen2.5-Coder-7B-Instruct**. Use only as a fallback, since generated code is harder to verify.

### 7. Check answerability and confidence
- `unanswerable` if a concept the question needs is absent from the capability record (rulebook §7), or the period lies fully outside the data range. Answer as unanswerable, with the reason.
- Partial overlap: compute on the available portion.
- `abstain` if the question fails to parse, the computation errors, or estimated probability of being correct is at or below 50%.

Confidence estimate: combine the mapping verification scores (step 2), parse consistency (two independent parses of the question agree), and validation checks (step 9). Calibrate the combination on training data, so that the 50% cut-off matches actual accuracy.

Models: none.

### 8. Format the answer
- Apply the output format from rulebook §9: plain numbers (no units or separators), percent as a number with 2 decimals, dates as YYYY-MM-DD or YYYY-MM, names exactly as in the data, lists in the question's order, tables as `{"columns", "rows"}`.
- Round money to the nearest đồng; round percent to 2 decimals.
- Output: `{"id", "type", "answer"}`.

Models: none.

### 9. Validate and write output
- Schema-check each line against its type.
- Ensure each question id appears exactly once; fill missing ids with abstain.
- Write `submission.jsonl` and one log per question (id, final answer, elapsed time, derivation path, rules fired, capability record references).

Models: none.

### 10. Evaluate on training data
- Run the pipeline on `train/questions.jsonl`; compare to `answers.jsonl` (type and value, with alias handling).
- Report penalized accuracy (see Scoring), accuracy, wrong count, and abstain count, by answer type and by business.
- Calibrate the 50% threshold: check that answers above 50% estimated confidence are correct more than half the time.
- Use the error report to fix step 2 (mappings, units, dedupe), step 3 (parsing), step 5 (rules), or step 6 (computation), in that order of likely impact.

Models: none.

## Model summary

| Step | Model | Purpose | Needed? |
|---|---|---|---|
| 2.3 | Qwen2.5-7B-Instruct | Table roles, column mapping, joins | Yes |
| 2.4 | Qwen2.5-7B-Instruct | Status mapping, compound-cell parse spec | Yes |
| 2.6 | Qwen2.5-7B-Instruct | Free-text README rule extraction | Yes |
| 3 | Qwen2.5-7B-Instruct | Question to structured JSON spec | Yes |
| 6 | Qwen2.5-Coder-7B-Instruct | Code generation for hard questions | Optional fallback |

All licenses confirmed. BGE-M3 was removed from step 2: the LLM mapping plus deterministic verification replaces header embedding. Keep it as an option only if the LLM mapping pass rate is low.

All other steps are deterministic code.

## Known facts from the dev_001 and dev_002 READMEs

| | dev_001 | dev_002 |
|---|---|---|
| Sales files | `So_chi_tiet_ban_hang.xlsx` (one file) | `BanHang_T<m>_<yyyy>.xlsx`, 17 monthly files, sheet `DonHang_ChiTiet` |
| Product list | `Danh sách hàng hóa.xlsx`: code, name, group, base unit, price, cost | None. `DanhSachQuyDoi.xlsx` only, for products with a large unit |
| Product code in sales | No (joined by name) | No (joined by name) |
| Date format | not stated | `dd/mm/yyyy` |
| Data end | 08/07/2026 | 24/05/2026 |
| Customer | `Khách hàng`, `Khách lẻ` for walk-ins | `Tên khách hàng`, `Khách lẻ` for walk-ins |
| Line quantity | `Số lượng` and `Số lượng quy đổi` | `Số lượng sản phẩm` |
| Line amount | `Thành tiền` (after discount) | `Tổng tiền` (after discount, includes topping money) |
| Status | `Trạng thái` | `Trạng thái đơn hàng` |
| Toppings | not stated | in the product cell, joined by ` + ` |
| Cost on sale lines | no (cost is in the product list only) | no |

dev_001 cost: the rulebook (§5) defines gross profit using the cost recorded on the sale line. dev_001 records cost only in the product list, so cost-based questions need a rule decision before step 5.

These two businesses are examples for testing step 2. They are not the basis for hand-written rules.

## Open points
- Profile the real files (headers, grain, date and number formats, status values) for the training businesses, starting with the layout examples above.
- Build a small hand-labeled mapping set (for example 5 training businesses) to measure step 2 accuracy: mapping precision, status mapping, join match.
- Confirm the topping rule (portions × line quantity) and the money split against sample rows.
- Decide the dev_001 cost rule (product-list cost only).
- Choose the LLM for step 2 and check its pass rate. Qwen2.5-7B-Instruct is the default; a larger model may be needed.
- Calibrate the confidence estimate and the 50% cut-off on training data.
- Check the Penalized Accuracy aggregate formula against the leaderboard when results are published.
- Private test rulebook is unknown. Keep rule implementations generic per rule ID, and expect sector sections and rule wording to change. Evaluate on public test as the closest proxy.
