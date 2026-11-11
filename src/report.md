# Shape-template canonicalization report

## Summary

- Rewrote `src/answer_schema.jsonl` as **23 JSONL lines**, preserving the input's 23 shape names and their order. No shape was added or removed, every status remains `template`, and there is no `unanswerable` line.
- Created `src/concept_dictionary.json` with **29 entries** and **79 folded aliases**. Every alias has a matching accented `display` value.
- Replaced question-supplied values with **47 named slot references**. Removed answer values, empty answer arrays, rounding rules, rounding labels, decimal settings, and answer-level table payloads.
- Preserved `suggested_new: true` only on `share` and `stock_level`.
- Files written by this task: `src/answer_schema.jsonl`, `src/concept_dictionary.json`, and this report. `terms.json` was read only for its existing `term_id` inventory and was not modified.

## Per-shape changes

In the old → new column, “removed” means the member was removed, not replaced with `null`. All `answer` objects now contain only `type`.

| Shape | Fields changed | Placeholders and old → new values |
|---|---|---|
| `aggregate` | `common.time`, `shape_fields.unit`, `shape_fields.rounding_rule`, `answer` | `month:null`, `year:null` → `{"slot":"month"}`, `{"slot":"year"}`; `unit:null` → `{"slot":"unit"}`; `rounding_rule:{money:dong,count:integer}` → removed; `answer.value:null` → removed. |
| `count` | `common.metric`, `common.time`, `shape_fields.entity`, `shape_fields.aggregation`, `answer` | `metric:"order_count"` → `"order_id"`; `entity:"order"` → `"order_id"`; no aggregation → `{"function":"count","distinct":true}`; month/year nulls → named slots; `answer.value:null` → removed. `exclude_status:["huỷ","trả hàng"]` remains canonical under §1 and §8. |
| `average` | `common.time`, `shape_fields.per`, `answer` | `per:"order"` → `"order_id"`; month/year nulls → named slots; `answer.value:null` and `answer.rounding:"dong"` → removed. |
| `average_nonmoney` | `common.metric`, `common.time`, `shape_fields.per`, `answer` | `metric:null` → `{"slot":"metric"}`; `per:null` → `{"slot":"per"}`; month/year nulls → named slots; `answer.value:null` and `answer.decimals:2` → removed. |
| `diff` | `shape_fields.period_a`, `shape_fields.period_b`, `shape_fields.rounding_rule`, `answer` | Period-A month/year nulls → `month_a`/`year_a` slots; period-B nulls → `month_b`/`year_b` slots; `rounding_rule` → removed; `answer.value:null` → removed. `common.time:null` and structural `operation:"a_minus_b"` remain. |
| `ratio` | `common.metric`, `common.time`, `shape_fields.aggregation`, numerator/denominator metrics and filters, `answer` | Every `order_count` → `order_id`; no aggregation → count/distinct aggregation; numerator `field:"status"` → `"order_status"` and `op:"="` → `"eq"`; numerator `value:"huỷ"` remains canonical; denominator `status_scope:"all"` → `exclude_status:[]`; numerator also has `exclude_status:[]`; month/year nulls → slots; `answer.value:null` and `decimals:2` → removed. |
| `pct_change` | `common.time`, `answer` | Month/year nulls → named slots; `answer.value:null` and `decimals:2` → removed. Structural `base:"previous_period"` and `sign:"signed"` remain. |
| `top_1` | `common.time`, `shape_fields.group_by`, `answer` | `group_by:"product"` → `"product_name"`; month/year nulls → slots; `answer.value:null` → removed. Structural `k:1` remains. |
| `top_1_value` | `common.time`, `shape_fields.group_by`, `shape_fields.rounding_rule`, `answer` | `group_by:"day"` → `{"slot":"group_by"}`; month/year nulls → slots; `rounding_rule` → removed; `answer.value:null` → removed. Structural `k:1` remains. |
| `top_k` | `common.time`, `shape_fields.group_by`, `shape_fields.k`, `answer` | `group_by:"product"` → `"product_name"`; `k:null` → `{"slot":"k"}`; month/year nulls → slots; `answer.value:[]` → removed. |
| `threshold_list` | `common.time`, `shape_fields.group_by`, `shape_fields.filter`, `shape_fields.exclude_status`, `shape_fields.exclude_channel`, `answer` | `group_by:"customer"` → `"customer_name"`; `op:">"` → `{"slot":"comparator"}`; `value:1000000` → `{"slot":"threshold"}`; `date:null` → `{"slot":"date"}`; `cumulative_from_data_start:true` → removed because `as_of` carries §2 cumulative semantics; no status/channel exclusions → `exclude_status:["huỷ"]` and `exclude_channel:["COD","sàn TMĐT"]`; `answer.value:[]` → removed. |
| `balance_asof` | `common.time`, `answer` | `date:null` → `{"slot":"date"}`; `cumulative_from_data_start:true` → removed because `as_of` carries §2 cumulative semantics; `answer.value:null` and `rounding:"dong"` → removed. Existing `exclude_status:["huỷ"]` and `exclude_channel:["COD","sàn TMĐT"]` were retained and checked. |
| `breakdown_by_time` | `common.time`, `answer` | `start:null`, `end:null` → `{"slot":"start"}`, `{"slot":"end"}`; `answer.value:[]` → removed. `grain:"day"` is now backed by the dictionary; structural `order:"chronological"` remains. |
| `entity_list` | `common.metric`, `common.time`, `shape_fields.group_by`, `shape_fields.filter`, `shape_fields.sort`, `answer` | Both `inventory` metrics → `stock_end_qty`; `{kind:month,month:null,year:null}` → `{kind:as_of,slots:{date:{"slot":"date"}}}` for end-of-day stock under §2 and §5; `group_by:"product"` → `"product_name"`; `op:">"` → `{"slot":"stock_comparator"}`; `value:0` → `{"slot":"stock_value"}`; `sort:"code_asc"` → `{"criterion":"product_code","direction":"asc"}`; `answer.value:[]` → removed. |
| `multi_value` | `shape_fields.periods`, `answer` | One-element period array containing month/year nulls → `{"slot":"periods"}`; `answer.value:[]` → removed. `common.time:null` and structural `order:"question_order"` remain. |
| `multi_period` | `shape_fields.periods`, `answer` | One-element period array containing month/year nulls → `{"slot":"periods"}`; `answer.value:[]` → removed. `series_by:"month"` is backed by the dictionary; `common.time:null` remains. |
| `argmax_day` | `common.time`, `answer` | Month/year nulls → named slots; `answer.value:null` and `answer.precision:"day"` → removed. Structural `criterion:"max"` and dictionary-backed `granularity:"day"` remain. |
| `date_lookup` | `shape_fields.group_by`, `answer` | `group_by:"product"` → `"product_name"`; `answer.value:null` and `answer.precision:"day"` → removed. `common.time:null` and structural `criterion:"earliest"` remain. |
| `expiry_date` | `common.time`, `shape_fields.group_by`, `answer` | `date:null` → `{"slot":"date"}`; `group_by:"product"` → `"product_name"`; `answer.value:null` and `answer.precision:"day"` → removed. Structural `criterion:"earliest_expiry"` and `scope:"lot_in_stock"` remain. |
| `top_k_table` | `common.time`, `shape_fields.group_by`, `shape_fields.k`, `answer` | `date:null` → `{"slot":"date"}`; `group_by:"customer"` → `"customer_name"`; `k:null` → `{"slot":"k"}`; `answer.columns` and `answer.rows` → removed. Structural `shape_fields.columns:["khach_hang","so_tien_no"]` remains. |
| `trend_check` | `shape_fields.periods`, `answer` | One-element period array containing month/year nulls → `{"slot":"periods"}`; `answer.value:null` → removed. Structural `direction:"increasing"` and `consecutive:true` remain; `common.time:null` remains. |
| `share` | `common.time`, `answer` | Month/year nulls → named slots; `answer.value:null` and `decimals:2` → removed. `suggested_new:true`, canonical `customer_group`, and structural numerator/denominator scopes remain. |
| `stock_level` | `common.metric`, `common.time`, `shape_fields.group_by`, `answer` | `inventory` → `stock_end_qty`; `date:null` → `{"slot":"date"}`; `group_by:"product"` → `"product_name"`; `answer.value:[null,null]` → removed. `suggested_new:true` and the §5 structural values `large_and_base`/`end_of_day` remain. |

## Concept corrections and dictionary decisions

- **Order counts:** `count` and `ratio` now use `order_id` with `{"function":"count","distinct":true}` rather than the non-concept `order_count`. The default `count` shape excludes `huỷ` and `trả hàng` (§1), with deduplication represented by `distinct:true` (§8). The cancellation ratio counts `huỷ` in its numerator and uses an empty exclusion list in both components so its denominator covers every status, as §1 requires.
- **Stock:** `entity_list` and `stock_level` use `stock_end_qty`, not `inventory` (§5). `entity_list` uses an `as_of` time because the question concerns an end-of-day state (§2 and §5).
- **Revenue identifier:** `revenue` remains the named revenue metric supported by `params.defaults.revenue_exclusions` and §1, and is explicitly defined in the new dictionary. It was not silently replaced with a raw-column concept. No line required the stop-and-leave-unchanged fallback after this named-metric check.
- **Canonical groupings and fields:** `product` → `product_name`, `customer` → `customer_name`, and `status` → `order_status`. `day` and `month` have dictionary grouping entries. The one fixed day grouping in `top_1_value` became a question-supplied slot as requested.
- **Statuses:** only canonical status values are used. `huỷ` is the cancellation filter; `trả hàng` appears only in the default revenue/count exclusion, never as an English status label.
- **Debt:** both `threshold_list` and `balance_asof` exclude exactly `COD` and `sàn TMĐT` channels and exactly `huỷ` from statuses. Neither excludes `trả hàng`, so a returned order can remain in debt while unpaid (§4).
- **Sort:** all six sorts are objects with exactly `criterion` and `direction`. The legacy string `code_asc` became `product_code` ascending.
- **Constants in the dictionary:** `còn nợ` carries `debt_balance gt 0` (§4), `còn hàng` carries `stock_end_qty gt 0` (proposed, §2 and §5), and `hết hàng` carries `stock_end_qty eq 0` (§5). All other entries have `constant:null`.

### Proposed entries

The other 24 entries are marked `confirmed`. These five are deliberately marked `proposed`:

| Canonical / kind | Folded aliases | Constant | Why proposed |
|---|---|---|---|
| `stock_end_qty` / `constant_term` | `con hang` | `{"comparator":"gt","value":0}` | §2 identifies `còn hàng` as a remaining state and §5 explicitly defines `hết hàng` as zero, but the positive complement is not stated as a separate canonical term. |
| `day` / `grouping` | `theo ngay`, `moi ngay` | `null` | §2 confirms day semantics and the training questions use daily series, but the rulebook does not name this grouping identifier directly. |
| `month` / `grouping` | `theo thang`, `moi thang` | `null` | §2 confirms month semantics and the training questions use monthly series, but the rulebook does not name this grouping identifier directly. |
| `lte` / `comparator` | `khong qua`, `toi da` | `null` | §2 defines strict/inclusive boundaries, but these exact aliases and the canonical `lte` label are not directly named there. |
| `year` / `time_kind` | `nam` | `null` | §2 supports year expressions, while the exact generic-year interpretation and its slots still depend on the question. |

## Allowed literals and structural constants

Canonical identifiers, answer types, time-kind IDs, shape IDs, and slot names are not question-specific literals. The remaining literal values in the templates are limited to the following structural constants:

| Literal(s) | Location | Reason and citation |
|---|---|---|
| `template`, `suggested_new:true`, the seven answer type strings, and the listed time-kind IDs | Template envelope | Required by the requested template format; answer formats are defined in §9. |
| `k:1` | `top_1`, `top_1_value` | Structural constant of the top-one shape definitions. These are the only two numeric literals in the templates. |
| `function:"count"`, `distinct:true` | `count`, `ratio` | Count aggregation and duplicate handling required by §1 and §8 and by the count/ratio shape definitions. |
| `huỷ`, `trả hàng` | Status filters/exclusions | Canonical statuses under §8; default count exclusions under §1 and debt eligibility under §4. |
| `COD`, `sàn TMĐT` | Both debt shapes | Rulebook-mandated exclusion list for orders that are not customer debt, §4. |
| `a_minus_b` | `diff` | Fixed operation of the `diff` shape; §1 defines asked period minus comparison period. |
| `previous_period`, `signed` | `pct_change` | Fixed comparison base and sign behavior of the shape; §1 defines previous-period percent change and negative decreases. |
| `asc`, `desc` | Sort objects | Required sort-direction values; §9 defines metric-descending and code-ascending ordering. |
| `chronological`, `question_order` | Series shapes | Fixed ordering behavior of their shape definitions; §9 requires the requested list order. |
| `max`, `earliest`, `earliest_expiry` | `argmax_day`, `date_lookup`, `expiry_date` | Fixed criteria of those shape definitions; §3 covers maxima and §6 covers earliest expiry. |
| `lot_in_stock` | `expiry_date` | Fixed scope of the shape; §6 restricts the lookup to lots still in stock. |
| `increasing`, `consecutive:true` | `trend_check` | Fixed condition of the trend-check shape definition. |
| `large_and_base`, `end_of_day` | `stock_level` | Fixed stock representation and state; §5 defines large-plus-base-unit output and end-of-day stock. |
| `group`, `shop_total_including_walk_in` | `share` | Fixed numerator/denominator scopes of the share shape; §1 requires the whole-shop denominator to include walk-in orders. |
| `khach_hang`, `so_tien_no` | `top_k_table.shape_fields.columns` | Fixed column labels of this table shape; §9 defines the table structure. |
| Empty filter/exclusion lists | Various shapes | Structural empty sets, not question values. In particular, the ratio denominator's empty exclusion list represents all statuses under §1. |
| `common.time:null` | Five shapes listed below | Explicitly permitted for shapes without one common time window. |

No template contains a literal threshold, date, count supplied by a question, product/customer name, `1000000`, or stock comparison value. Numeric zero occurs only in dictionary constant terms, where §4 and §5 define it.

## Shapes with no common time

`common.time` is intentionally `null` for `diff`, `multi_value`, `multi_period`, `date_lookup`, and `trend_check`. The first three comparison/series shapes obtain their periods from period-specific objects or the `periods` slot; `date_lookup` retains the input's absence of a common time window; `trend_check` obtains its series from `periods`. Every non-null common time object has exactly `kind` and `slots`, as do `diff.period_a` and `diff.period_b`.

## Unresolved items for review — not decided here

1. **`stock_end_qty` versus `to_date_stock`:** the required `stock_end_qty` correction is present in the templates. `to_date_stock` remains an alternative for review and was not selected.
2. **`entity_list` stock condition:** `còn hàng` (`stock_end_qty gt 0`) versus `hết hàng` (`stock_end_qty eq 0`) must come from each question. The template deliberately exposes `stock_comparator` and `stock_value` slots and chooses neither condition.
3. **`breakdown_by_time` answer form:** whether a particular breakdown should be a list or a table remains undecided. The input's `list` type is retained pending a dedicated comparison with training answers.
4. **Suggested shapes:** `share` and `stock_level` remain marked `suggested_new:true`; their final inclusion is left for review.

## Verification performed

A Python standard-library validator parsed every artifact and recursively checked scalar values, slot objects, identifiers, times, sorts, answer envelopes, debt rules, dictionary entry structure, alias folding, display folding, and alias-to-canonical uniqueness. The final run used UTF-8 stdin/stdout handling; preliminary non-UTF-8 command invocations were discarded rather than treated as artifact failures.

| Check | Result |
|---|---|
| JSON parsing and line count | **PASS** — 23/23 non-empty JSONL lines parse; 23 lines written. |
| Shape names, order, and statuses | **PASS** — all 23 input names occur once in the original order; all statuses are `template`; no `unanswerable` line. |
| Concept, metric, comparator, grouping, and shape identifiers | **PASS** — all scalar references resolve to `params.json`, the concept dictionary, the closed comparator set, or the preserved input shape set. Slot-filled values remain deferred. |
| Literal and placeholder audit | **PASS** — 47 valid slot objects; only two numeric literals (`k:1` twice); only five nulls (the declared no-time shapes); every string matched a canonical identifier, required format value, or a cited structural constant above. |
| Answer objects and types | **PASS** — all 23 answers contain only `type`; every type is one of `number`, `percent`, `date`, `string`, `boolean`, `list`, `table`; every `answer_kind` matches `answer.type`. |
| Time-object consistency | **PASS** — all 18 non-null common time objects have exactly `kind` and `slots`; all their values are slots; both nested `diff` period objects follow the same keys. |
| Sort format | **PASS** — all six sorts are `{"criterion":...,"direction":...}` objects with `asc` or `desc`; no string sort remains. |
| Debt rules | **PASS** — both debt shapes have `exclude_channel:["COD","sàn TMĐT"]` and `exclude_status:["huỷ"]`; neither excludes `trả hàng`. |
| Count/ratio semantics | **PASS** — count uses distinct `order_id` aggregation and canonical §1 exclusions; the ratio denominator has no status exclusions and its numerator filters canonical `huỷ`. |
| Dictionary schema and constants | **PASS** — 29 entries have the required fields, allowed kind/status values, rulebook citations, and constants only for `constant_term` entries. |
| Alias folding and display | **PASS** — all 79 aliases are lowercase/accent-free; all 79 display values fold back to their aliases. |
| Alias uniqueness | **PASS** — 79 alias occurrences map to 79 distinct folded aliases; zero aliases map to two canonical identifiers. |
| Existing term-ID inventory | **PASS** — the 25 existing `terms.json` IDs are unique; the new dictionary introduces no competing `term_id` field and `terms.json` was not edited. |
| Suggested-new flags | **PASS** — exactly `share` and `stock_level` have `suggested_new:true`. |

**Final counts:** 23 template lines, 29 dictionary entries, 79 unique folded aliases, 47 named slots, and zero validation errors in the final run.
