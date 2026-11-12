# Routing report

## Version

- Routing rules: `src/shape_rules.json` version 2 (previously 1)
- Templates: `src/answer_schema.jsonl` 25 shapes (previously 23)
- Date: 2026-11-12
- Scope: Option A, first step: `time:day` and `as_of` only

## Change

1. **New templates (2):** `aggregate_day` (revenue, time `day` with a date slot) and `count_day` (order_id count, time `day`). Both copy the body of `aggregate` and `count` with only the time object changed. No other template was touched.
2. **New rules (2):**
   - `R110_aggregate_day` (priority 110): revenue, number, `time:day`, one period, no ranking, no change, no entity, no filter.
   - `R120_count_day` (priority 120): order_id, number, `time:day`, one period, no ranking, no change, no entity, no filter.
   The entity and filter gates are the same as in `R090_count` and `R100_aggregate`.
3. **`shape_metrics`:** added `aggregate_day: [revenue]` and `count_day: [order_id]`.
4. **Tests (additions only):**
   - Per-rule positive and near-miss tests for R110 and R120.
   - R110 and R120 added to the covered-rule set.
   - Template count assertion 23 -> 25, because two templates were added.
   - Fixtures `aggregate_day.json` and `count_day.json`.
   No existing assertion was changed or removed.

**`as_of` was not changed.** The extractor produces `as_of` only for debt balance and stock/expiry, and those are already covered by R040, R030, and the unrouted entity/stock cases. The extractor produced no `as_of` revenue or order-count question, so there was no candidate for a new `as_of` rule. A rule would need at least 20 matches to be added.

## Candidate counts (train, extractor-ok questions)

| Rule | Shape | Matches | Gold type agreement |
|---|---|---|---|
| R110_aggregate_day | aggregate_day | 82 | 82 / 82 (number) |
| R120_count_day | count_day | 28 | 28 / 28 (number) |

Both exceed the 20-match threshold.

## Results

Run on `train/questions.jsonl` (3000 questions), split `src/splits/split_seed20260512.json`.

| Split | Questions | Routed ok (before -> after) | Type agreement (after) |
|---|---|---|---|
| all | 3000 | 342 -> 452 | 447 agree, 5 gold unanswerable |
| dev | 2248 | 234 -> 301 | 296 agree, 5 gold unanswerable |
| held-out | 752 | 108 -> 151 | 151 agree |

- Previously routed ok questions changed: 0.
- Unit tests: 86 pass (84 before, plus 2 new per-rule tests).
- Determinism: the router output for the same input is byte-identical across runs (`test_cli_pipeline.py`).

## Limits and caveats

- **Type agreement only.** Values were not compared, because no executor exists under `src/`. The 110 new routes match the gold answer type, not necessarily the gold value.
- **Held-out is not clean.** The two rules were chosen by looking at the train counts for the whole training set, including the held-out businesses. The held-out agreement above is therefore optimistic.
- **Entity-bearing day questions are still unrouted.** 65 time:day questions with an entity (revenue 14, order_id 51) are excluded by the same gate that protects `R090_count` and `R100_aggregate`.
- **Analysis not refreshed.** `src/analysis.md` still reports the previous router numbers (342 ok). It was not updated in this change.

---

## Version 3

- Routing rules: `src/shape_rules.json` version 3
- Templates: `src/answer_schema.jsonl` 27 shapes
- Date: 2026-11-12
- Scope: Option A, steps 1-3 as revised

### Change

1. **Step 1, `last_n_days`: NOT ADDED.** Failed the 20-match rule. The gate (revenue, number, `last_n_days`, one period, no ranking, no change, no entity, no filter) matches 18 training questions. The order_id gate matches 4. No template or rule was added for it. Expected gain was revised down from 35 to 18.
2. **Step 2, `range`: ADDED.** Quarter date ranges only (all 67 range questions carry Q1-Q4 anchors).
   - Template `aggregate_range` (revenue, slots `start`/`end`) and rule `R140_aggregate_range` (priority 140). Matches 32 train questions, all gold number.
   - Template `count_range` (order_id count, slots `start`/`end`) and rule `R150_count_range` (priority 150). Matches 28 train questions, all gold number.
   - Same gates as `R100`/`R090` (no ranking, no change, no entity, no filter).
   - Each rule clears the 20-match threshold on its own.
3. **Step 3, `week`: RECORDED AS OPTION (a), NOT IMPLEMENTED.** Week questions stay unsupported. No `time_anchor` feature was added. The 17 routable number questions are below the 20-match threshold, and the start/end values already carry the dates.
4. **Tests (additions only):**
   - Per-rule positive and near-miss tests for R140 and R150.
   - A check that the `aggregate_range` and `count_range` fixtures are calendar quarters.
   - Covered-rule set extended; template count 25 -> 27.
   - Fixtures `tests/fixtures/aggregate_range.json` and `tests/fixtures/count_range.json`.

### Results

Train, 3000 questions, split `src/splits/split_seed20260512.json`.

| Split | Routed ok (v2 -> v3) | Type agreement (v3) |
|---|---|---|
| all | 452 -> 512 | 507 agree, 5 gold unanswerable |
| dev | 301 -> 340 | 335 agree, 5 gold unanswerable |
| held-out | 151 -> 172 | 172 agree |

- Previously routed ok questions changed: 0.
- New routes: 60 (`aggregate_range` 32, `count_range` 28), all gold type number.
- Unit tests: 89 pass (86 before).

### Limits and caveats

- Type agreement only. Values were not compared.
- Held-out is not clean. The range rules were chosen from train counts across all businesses.
- Entity-bearing range questions (7) and week questions (77 unrouted) remain unsupported.
- `last_n_days` (18 revenue, 4 order_id) and `week` need a rule-policy decision. See the Version 3 change list.

### Decision record (after version 3)

- `last_n_days`: left unrouted (approved). Revenue gate 18 and order_id gate 4 are below the 20-match rule. No template or rule added.

---

## Version 4

- Routing rules: `src/shape_rules.json` version 3 (unchanged)
- Templates: `src/answer_schema.jsonl` 27 shapes (unchanged)
- Config changed: `src/cues.json` `filler` list (+21 words)
- Date: 2026-11-12
- Scope: steps 1-3 of the post-v3 plan

### Change

1. **Step 1, `last_n_days`:** left unrouted (see decision record above).
2. **Step 2, filler cleanup:** added 21 words to `cues.json` `filler`. `filler` only removes a token from the leftover list, which sets `entity_concepts`. Added: `truoc, lien, quan, theo, kem, ke, hon, chiem, len, cung, dem, ky, sang, vs, dong, dat, trung, binh, thu, xet, coi, check, hnay, hqua, th, bua, may, ti, nhiu`. Not added, on purpose: words that can name a product or segment (`nhom`, `nhap`, `nuoc`, `hoa`, `thuoc`, `than`, `thiet`, `size`) and single letters (`l`, `m`, `x`, `z`).
3. **Step 3, re-count for option C:** recounted after the filler change (below).

### Results

Train, 3000 questions, split `src/splits/split_seed20260512.json`.

| Split | Routed ok (v3 -> v4) | Type agreement (v4) |
|---|---|---|
| all | 512 -> 593 | 587 agree, 6 gold unanswerable |
| dev | 340 -> 405 | 399 agree, 6 gold unanswerable |
| held-out | 172 -> 188 | 188 agree |

- Previously routed ok questions changed: 0.
- Newly routed: 81 (count 37, aggregate 27, count_day 14, aggregate_range 1, aggregate_day 1). 80 gold number, 1 gold unanswerable (`count`). All through existing rules; no rule or template changed.
- Newly ambiguous: 0.
- Unit tests: 89 pass. No test changed.

### Step 3: option C recount

- `quantity_base` unrouted: 349. Gold: number 213, percent 48, date 46, list 20, string 13, unanswerable 9.
- Of the 213 number questions, 200 still have an entity and 13 do not. The entity values are real product text (for example "nep hoa vang", "size l/m"), so a total would ignore the product and be wrong.
- Conclusion: a `quantity_base` number template alone gains at most about 13 questions. Option C depends on entity binding (option B), which is not in this part.

### Limits and caveats

- Type agreement only. Values were not compared.
- The filler list was chosen by reading leftover examples on train, so the held-out numbers are optimistic (same caveat as v2 and v3).
- Trace strings in `routes.jsonl` change for questions whose leftover words changed; routing status and shape do not.
- `top_k_table` for debt rankings (19 gold-table as_of questions) is not routed. No change in this version.
