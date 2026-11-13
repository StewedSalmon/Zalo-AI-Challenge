# Query Spec v1 change report

## Version 1

- Code: `src/query_spec.py`, `src/date_resolver.py`, `src/query_parser.py`, `src/query_validator.py`
- Entry points: `src/query_parser.py`, `src/feature_extractor.py` (compatibility wrapper), `src/evaluate_routes.py`
- Config: `src/concept_dictionary.json` (`metric_ids`), `src/cues.json` (aliases, shift values, time aliases, stopwords)
- Removed: `src/shape_router.py`, `src/shape_rules.json`, `src/answer_schema.jsonl`
- Tests: new `tests/test_query_spec.py`, `test_date_resolver.py`, `test_query_parser.py`, `test_query_validator.py`, `test_coverage_3000.py`; rewritten `test_extractor.py` and `test_cli_pipeline.py`; removed `test_shape_router.py` and `test_rules_per_rule.py`
- Date: 2026-11-13
- Scope: Person 3 Question Understanding, Query Spec v1 extraction for all train questions. No executor, pipeline, or model behavior was changed.
- Only this report file was changed while writing this report.

## Change

1. **New output contract.** `Query Spec v1` replaces shape routing. A record contains `status`, `reason`, `schema_version`, and a `query` object. The query object always carries all fields: `operation`, `metric`, `distinct`, `per`, `filters`, `having`, `numerator`, `denominator`, `entities`, `time`, `group_by`, `sort_by`, `sort_order`, `limit`, `unit`, `answer_type`, and `notes`. Missing scalars are `null`; missing lists are `[]`.
2. **No routing layer.** `shape_router.py`, `shape_rules.json`, and `answer_schema.jsonl` were removed. Extraction no longer selects a template or decides answerability. `status=ok` means the parser produced a valid Query Spec, not that the question is executable. Person 4 owns execution and answerability.
3. **Time resolution.** `date_resolver.py` resolves explicit months, quarters, years, days, weeks, month-to-date, year-to-date, last-N days, Tết, and relative aliases. It applies the rule that a month without a year uses the current year if the month is not after `as_of_date`, otherwise the previous year. Comparisons use `time.comparison.periods`; `cùng kỳ năm trước` and `cùng kỳ tháng trước` derive from the primary period instead of becoming a separate full-year or full-month period.
4. **Operation and metric inference.** The parser handles sum, count, average, ratio/share, percent change, difference, rank/argmax/argmin, lookup, series, delta, bucket, and unknown. Multiple metric candidates are emitted in `notes`, not treated as a parser failure. Debt movement becomes `delta` on `debt_balance`; status ratios use `order_id` when no explicit metric exists. No new canonical metric was added. The six existing metric ids remain the closed set.
5. **Filters, thresholds, and having.** Row conditions are normalised into `filters[]` with `field`, `op`, `value`, and `unit`. Customer “mua ít nhất N đơn” questions use `having` with a distinct order count instead of becoming a top-N ranking. Threshold numbers are claimed so they do not appear as free entity text.
6. **Entities and text preservation.** Tokens keep both folded text for matching and the original accented surface for output. Entity text is the raw question text, not a resolved id. Bare time words that also occur in product names, such as `Nam`, `nấm`, and `quy`, no longer split product entities. `bill` and `hóa đơn` were added as aliases of the existing `order_id` metric.
7. **CLI and validation.** `feature_extractor.py` keeps the old command and accepts `--rules` but ignores it, so the old pipeline does not crash during migration. `query_validator.py` checks the JSON contract only. `evaluate_routes.py` now reports Query Spec coverage and answer-type agreement instead of shape routes.
8. **Tests.** Old shape and per-rule tests were removed. New tests cover the contract, time resolver, parser behaviour, validator, the compatibility wrapper, CLI determinism, and the full 3000-question train run. No existing executor test was changed.

## Results

Train: `train/questions.jsonl`, 3000 questions. Generated output: `src/analysis_out/query_specs_train.jsonl`.

| Measure | Result |
|---|---|
| Parser status ok | 3000 / 3000 |
| Validator errors | 0 |
| Operation populated | 3000 / 3000 |
| Metric populated | 2808 / 3000 |
| Time populated | 2921 / 3000 |
| Answer type populated | 2998 / 3000 |
| Entity text populated | 1619 / 3000 |

Operation distribution: sum 1345, count 385, ratio 272, rank 256, average 229, percent change 210, argmax 132, share 89, series 54, delta 21, lookup 5, unknown 2.

Answer-type agreement: **2757 / 2929 = 94.13%**. The denominator excludes 70 gold `unanswerable` questions and 1 question where the parser left `answer_type=null`. This measures the label only, not metric choice, time choice, or value correctness.

Largest type mismatches:

| Gold -> parser | Count |
|---|---|
| list -> number | 71 |
| boolean -> number | 21 |
| date -> string | 17 |
| table -> list | 15 |

Unit tests: **70 pass** via `python -m unittest discover -s tests -p "test_*.py"`. This includes the 13 existing executor tests.

Artifacts:

- `src/analysis_out/query_specs_train.jsonl`
- `src/analysis_out/validation_train.json` (`total: 3000`, `invalid: 0`)
- `src/analysis_out/coverage_train.json`

## Limits and caveats

- **Type agreement only.** Values were not executed. A matching answer type does not mean the operation, metric, filters, or final value are correct.
- **Residual null coverage.** 192 questions have `metric=null` and 2 have `operation=unknown`. They validate by contract but may not be executable without a Person 4 fallback.
- **Entity extraction is heuristic.** 1619 questions still carry free-text notes. Product and customer binding is not resolved; this is deliberate for Person 3.
- **No split evaluation.** Results are for the whole train set. The dev/held-out split was not re-run for this report.
- **Parser status is not answerability.** All 3000 records are `ok` at the parser level. Some are semantically unsupported or need clarification.
- **Pipeline not migrated.** `pipeline/run_pipeline.py` was not run or changed. Old artifacts such as `features_train.jsonl`, `routes_train.jsonl`, and `summary_train.json` remain stale and must not be treated as Query Spec output.
- **Held-out is not clean.** Time aliases, cues, and metric choices were reviewed on train questions, so any later held-out measurement would be optimistic.
