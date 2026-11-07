Problem summary
- Input per question: id, business_id, question (Vietnamese), as_of_date, plus the business's files under /data.
- Output: one line per question in /result/submission.jsonl, typed as number, percent, string, date, boolean, list, table, unanswerable, or abstain.
- Scoring (rulebook §9): correct +1, wrong -1, abstain 0. The README's "Penalized Accuracy" formula was not included, so I'm using the rulebook values as the working assumption. Answer only when confidence is above 50%.
- Deliverables: (a) submission.jsonl, (b) a Docker image with predict.sh that reads /data and writes /result, (c) per-question logs in /result/logs/ (id, final answer, elapsed time, derivation path).
Stage-by-stage decomposition
S0. Runtime I/O
- Input: /data tree, questions.jsonl.
- Deliverable: a driver that iterates over questions and always emits a line for each id, including abstain fallbacks. Missing lines count as abstain, so no question should be dropped.
S1. Question intake
- Deliverable: a parsed record (id, business_id, question text, as_of_date).
- Handle questions written without diacritics (e.g. the README sample "thang nay hang nao ban cham nhat").
S2. Context loading
- Load rulebook.md (the split's default rules)
- Load calendar.csv if the question involves Tết (lunar calendar).
- Deliverable: a rule set = rulebook defaults overridden by the business README and by the question's own wording. Per §0, the question wins over the rulebook, and the rulebook wins over nothing.
S3. Data ingestion and normalization (per business)
- Load the business README.md
- Load CSV, XLSX, or SQLite. Map Vietnamese column names to canonical fields (e.g. "Nội dung" = product name, "Đối tác" = customer).
- Normalize numbers ("560,000" to 560000) and dates ("1 thg 6 2025" to a date). Apply the rulebook year rule (§8) for dates without a year.
- Normalize status labels to the four canonical values: hoàn thành, huỷ, trả hàng, đang giao.
- Dedupe: identical rows within the same order are an export error (count once); identical rows across different orders are two sales (count both).
- Fill blank cells within an order group from the first line.
- Deliverable: canonical tables (orders, lines, customers, products, unit conversions, purchases, cash, debts, calendar), with a data-coverage record (min and max dates).
S4. Question understanding
- Extract: answer type, metric (revenue, quantity, count, debt, inventory, ...), entity, filters, ranking criterion, time expression, and comparison period.
- Deliverable: a structured query spec (JSON) with the target answer type and output format.
- Ambiguity resolves to the rulebook default.
S5. Time resolution
- Convert phrases to intervals using rulebook §2: "tháng này" (1st to as_of), "tuần rồi" (Mon–Sun), "quá N" (exclusive), "N ngày gần nhất" (inclusive of today), Tết windows, and "đến hết ngày X" (balance as of X, cumulative from data start).
- Deliverable: target interval(s) and comparison interval(s), with the flag for whether the period is cumulative or in-period.
S6. Business-rule application
- Apply revenue rules (§1): exclude cancelled and returned orders; a partial return excludes the whole order; use line totals with no order-level discount, no VAT, and no delivery fee.
- Apply unit conversion (§10): convert quantities to base units.
- Apply entity rules (§3): customer identity, walk-in exclusion, and the "Khách lẻ" handling.
- Apply sector rules when they match (§6): coffee sizes, pharmacy prescription and lot rules, construction delivery rules.
- Deliverable: a filtered fact set plus a rule-application trace (which rules fired).
S7. Computation
- Compute with deterministic code (pandas or SQL), not model arithmetic.
- Deliverable: raw result and intermediate values, saved to the log.
S8. Answer formatting
- Apply the type format (§9): numbers without units or separators, percent as a plain number with 2 decimals, dates as YYYY-MM-DD or YYYY-MM, names as they appear in the data, lists in the required order, tables as {"columns", "rows"} with numeric cells as JSON numbers.
- Money rounds to the nearest đồng. Percent rounds to 2 decimals.
- Deliverable: {"id", "type", "answer"}.
S9. Answerability decision
- Return unanswerable (with reason) when:
- the shop does not record the required field (§7), or
- the period lies entirely outside the data range.
- Partial overlap with the data range: compute on the available portion.
- Abstain when confidence is below threshold or derivation failed.
- Deliverable: one of answer / unanswerable / abstain, with reason.
S10. Validation and logging
- Schema check each output line against its type. Check that every question id is present exactly once.
- Write one log record per question: id, final answer, elapsed time, derivation path.
- Deliverable: submission.jsonl and /result/logs/*.
S11. Packaging
- Dockerfile with code, weights, and predict.sh.
- Verify a cold run on /data completes within time, since the tie-breaker is total inference time.
- Deliverable: a runnable image.
S12. Train evaluation loop
- Run the system on train/questions.jsonl, compare to answers.jsonl (match type and value, with alias handling), and report accuracy and penalty by answer type and by business.
- Deliverable: an error report used to iterate on S4 to S9.
biz_101 specifics (instance rules)
- Shape: one flat CSV, one row per order line. Columns: Giá bán, Kết quả, Nội dung, Ngày tháng, Đơn vị (tính), so_luong, Thanh tien, Quy ra ĐV lẻ, Mã hóa đơn, Đối tác.
- Data range: through 24/08/2026. Questions with as_of later than that have partial coverage. Periods wholly after 24/08 are unanswerable.
- Status: the rows I saw use Xong, which must map to hoàn thành. The other status values have not been checked yet; I only read 5 rows, so this needs profiling.
- Customers: "Đối tác" = "Khách lẻ" for walk-ins. Walk-ins are excluded from customer rankings and debt tables, but included in totals.
- Unit conversion: Quy ra ĐV lẻ is pre-computed, but I should verify it against the README table. Some products have two units (e.g. Xà bông Lifebuoy 90g: lốc = 6, thùng = 72). The README also gives Gạo ST25 "1 bao = 5 kg" and "1 bao 25kg = 25 kg", which means the unit name determines the factor.
- Not recorded: there is no payment method, delivery fee, order-level discount, or cost-of-goods column. Questions on those (payment split, delivery revenue, profit) should be unanswerable under §7, and profit or cost questions cannot be answered from this file.
- Ambiguity: the README is thin, so rely on rulebook defaults and verify on training questions for this business.
Next step I'd suggest: profile the full ban_hang.csv (status values, unit names, date range, and whether Quy ra ĐV lẻ matches the conversion table) before writing the S3 loader.