# DataMate — Zalo AI Challenge 2026

Agent that answers everyday Vietnamese questions directly on small-business structured data
(CSV / Excel / SQLite). Input is one business's data package plus a question and an `as_of_date`;
output is one typed answer (`number`, `percent`, `date`, `string`, `boolean`, `list`, `table`,
`unanswerable`, `abstain`) per question, scored by Penalized Accuracy (correct +1, wrong −1,
abstain 0; abstain on a gold-unanswerable question +1).

The `src/` question-understanding part (Person 3) is the active development area documented here; `pipeline/` holds the earlier end-to-end integration pipeline.

## Repository layout (committed files only)

```
├── README.md                         # this file
├── env_setup_requirements.md         # environment/packaging requirements extracted from the spec
├── rep.md                            # working notes
├── zac2026_DataMate_docker_guideline_final.pdf
├── zalo-ai-challenge-regulations-en.pdf
├── pipeline/                         # end-to-end integration pipeline (precomputation + executor)
│   ├── run_pipeline.py               #   pipeline entry point
│   ├── evaluate.py                   #   pipeline evaluation
│   ├── run_log_train.json            #   last full pipeline run log
│   ├── src/execute.py                #   pipeline execution stage
│   ├── table_io.py                   #   shared table reader
│   ├── cache/                        #   per-business precomputation caches (*.pkl, committed)
│   ├── logs/train/                   #   decisions.jsonl, summary.json
│   └── tests/                        #   test_units.py, test_regression.py
├── public_test/                      # organizers' public test split — never used for tuning
│   ├── questions.jsonl, rulebook.md, calendar.csv
│   └── data/dev_001 … dev_014/       #   per-business data files + README.md
├── reports/
│   ├── change_report.md              # versioned change log (Version 1 = current)
│   ├── executor_report.md            # six-shape executor report
│   └── routing_report.md             # pre-v1 shape-router era (superseded)
├── src/                              # Person 3: Question Understanding (+ executor bridge)
│   ├── query_parser.py               #   questions → Query Spec v1 (CLI entry point)
│   ├── date_resolver.py              #   time normalization against as_of_date + rulebook
│   ├── query_spec.py                 #   Query Spec v1 record definition
│   ├── query_validator.py            #   JSON contract validation
│   ├── feature_extractor.py          #   legacy compatibility wrapper (old pipeline CLI)
│   ├── evaluate_routes.py            #   coverage + answer-type agreement report
│   ├── spec_adapter.py               #   Query Spec v1 → executor adapter + value scoring run
│   ├── concept_dictionary.json       #   metric/concept lexicon (closed set of 6 metric ids)
│   ├── cues.json                     #   question cues: aliases, shift values, time aliases, stopwords
│   ├── report.md                     #   concept-dictionary era report (superseded by reports/)
│   ├── executor/                     #   six-shape value executor (Person 4 bridge)
│   │   ├── reader.py, ingest.py, aggregate.py, run.py
│   │   └── mappings/biz_{108,110,119,120,128}.json
│   ├── splits/split_seed20260512.json  # recorded dev/held-out business split
│   └── analysis_out/value_run_summary.json  # committed value-run summary (see Results)
├── tests/                            # Person 3 unit tests
│   ├── test_query_parser.py, test_date_resolver.py, test_query_spec.py, test_query_validator.py
│   ├── test_extractor.py, test_cli_pipeline.py, test_coverage_3000.py, test_executor.py
│   └── fixtures/                     #   per-shape templates + executor fixture business biz_fx
└── train/                            # train split (with reference answers)
    ├── questions.jsonl, answers.jsonl, rulebook.md, calendar.csv, suggested_answers.jsonl
    └── data/biz_101 … biz_130/       #   per-business data files + README.md
```

Untracked working files (excluded above by design): `src/analysis.md`, generated outputs under
`src/analysis_out/` (`query_specs_train.jsonl`, `value_run.jsonl`, …), `src/out/`, `__pycache__/`.

## How this part processes a question (step by step)

Person 3 = Question Understanding: turn a Vietnamese question into a structured, validated
computation request. No answerability decision, no value execution inside the parser.

1. **Fold and tokenize** (`query_parser.py`): lowercase, strip diacritics, `đ`→`d`; keep `/ % .`
   so `6/2025` stays one token. Every token keeps both the folded form (for matching) and the
   original accented surface (for output).
2. **Load lexicon** (`concept_dictionary.json` + `cues.json`): alias → canonical id index;
   load-time checks reject an alias mapping to two ids and time-alias collisions.
3. **Claim numeric/time spans**: `top N`, dates `d/m[/yyyy]`, `tháng m [năm y]`, `m/yyyy`,
   `quý q`, `năm y`, amounts (`5tr`, `1.200.000`, `1 triệu`), relative aliases (`tháng này`,
   `tuần rồi`, `hôm qua`).
4. **Resolve time** (`date_resolver.py`): month/quarter/year/day/week/month-to-date/YTD/
   last-N-days/Tết/relative aliases → concrete periods with `start`/`end`. A month without a
   year uses the current year unless the month is after `as_of_date` (rulebook §2).
   `cùng kỳ năm trước` derives from the primary period, not a separate full-year period.
5. **Infer operation and metric**: sum, count, average, ratio/share, percent change, difference,
   rank/argmax/argmin, lookup, series, delta, bucket. Metric ids are a closed set of 6
   (`revenue`, `quantity_base`, `order_id`, `debt_balance`, `stock_end_qty`, `expiry_date`);
   multiple metric candidates go to `notes`, not a failure.
6. **Normalize filters and thresholds**: row conditions → `filters[]` (`field`/`op`/`value`/`unit`);
   per-customer "mua ít nhất N đơn" → `having` on a distinct order count; threshold numbers are
   claimed so they never leak into entity text.
7. **Preserve entities as text**: entity text is the raw question surface (not a resolved id);
   bare time words that also occur in product names (`Nam`, `quy`) do not split product entities.
8. **Derive `answer_type`** from cues (`bao nhiêu`, `nào`, `liệt kê`, `%`, `ngày nào`) — a
   discriminator only, not a value.
9. **Emit Query Spec v1**: `status`, `reason`, `schema_version`, and a `query` object carrying
   all fields (`operation`, `metric`, `distinct`, `per`, `filters`, `having`, `numerator`,
   `denominator`, `entities`, `time`, `group_by`, `sort_by`, `sort_order`, `limit`, `unit`,
   `answer_type`, `notes`). `status=ok` means a valid spec, **not** answerability.
10. **Validate** (`query_validator.py`): JSON contract only (types, closed vocabularies).
11. **Evaluate** (`evaluate_routes.py`): coverage counts plus answer-type agreement against
    `train/answers.jsonl`, dev/held-out separated via the recorded split.
12. **Value agreement** (`spec_adapter.py`): adapt the executor-compatible family to the
    six-shape executor, execute on train data, and score against gold under the scoring matrix
    (correct +1 / wrong −1 / abstain semantics; abstain on gold-unanswerable +1).

## How to run

From the repository root, Python 3. Standard library only; no build step. Outputs go to
`src/analysis_out/`. Set `PYTHONDONTWRITEBYTECODE=1` to avoid `__pycache__` writes.

```powershell
# 1. Parse all train questions into Query Spec v1
python src\query_parser.py --questions train\questions.jsonl `
    --dictionary src\concept_dictionary.json --cues src\cues.json `
    --out src\analysis_out\query_specs_train.jsonl

# 2. Validate the contract
python src\query_validator.py --query-specs src\analysis_out\query_specs_train.jsonl `
    --out src\analysis_out\validation_train.json

# 3. Coverage + answer-type agreement (dev/held-out reported separately)
python src\evaluate_routes.py --questions train\questions.jsonl `
    --query-specs src\analysis_out\query_specs_train.jsonl --answers train\answers.jsonl `
    --split src\splits\split_seed20260512.json --out src\analysis_out\coverage_train.json

# 4. Value agreement run (executor family; writes value_run.jsonl + value_run_summary.json)
python src\spec_adapter.py --questions train\questions.jsonl `
    --query-specs src\analysis_out\query_specs_train.jsonl --answers train\answers.jsonl `
    --data train\data --split src\splits\split_seed20260512.json --out src\analysis_out

# 5. Unit tests (question understanding + executor)
python -m unittest discover -s tests -p "test_*.py"

# 6. Pipeline (integration) tests
python -m unittest discover -s pipeline\tests -p "test_*.py"
```

Options: `query_parser.py --confirmed-only` ignores dictionary entries marked `proposed`;
`--debug` adds spans/free tokens/trace; `--rules` is accepted but ignored (old-pipeline
compatibility). `feature_extractor.py` keeps the legacy command form for the old pipeline.

Competition rules honored: development uses train data only; public/private test data are never
opened for tuning. All runs are deterministic and offline (no network, no LLM calls).

## Dependencies

- **Python 3.13** (as used in development; any recent Python 3 works).
- **Standard library only** for `src/` and `pipeline/` — no third-party runtime packages
  (CSV via `csv`, Excel via `zipfile` + `xml.etree`, SQLite via `sqlite3`).
- `underthesea` is installed in the development environment but **not imported** by any current
  code.
- No network access, no model weights, no external services (competition rule: fully offline
  inference).

## Test results

| Suite | Command | Result |
|---|---|---|
| `tests/` (query understanding + executor) | step 5 | **70 tests, OK** (2.2 s) |
| `pipeline/tests/` (integration) | step 6 | 33 tests, **1 pre-existing error** (`test_regression` Biz101: `'1 thg 6 20'` does not match `%d/%m/%Y`) — dates from before this change set, not touched here |

Train evaluation (details in `reports/change_report.md`, Version 1):

| Measure | Result |
|---|---|
| Parser status ok | 3000 / 3000; validator errors 0 |
| Answer-type agreement | 2757 / 2929 = **94.13%** (label only) |
| Value agreement (executor family, 92 questions on 5 mapped businesses) | **89 / 92 = 96.7%**; 3 revenue over-counts on biz_119/biz_120 logged for diagnosis |
| CLI determinism | byte-identical outputs across runs (checked by test and by re-running the value run) |

## Versioning and reports

Development is tracked as versions in [`reports/change_report.md`](reports/change_report.md).
Version 1 (2026-11-13) is the current state: Query Spec v1 extraction for all 3000 train
questions, shape routing removed, value-agreement run added. Earlier eras (shape router,
concept dictionary) are documented in `reports/routing_report.md` and `src/report.md` and are
superseded.

## Status / known limits

- Parser `status=ok` is not answerability — Person 4 owns execution and answerability.
- 192 questions still have `metric=null`; entity binding is heuristic (deliberate for Person 3).
- Value agreement covers only the executor family (filter-free revenue/order-count on the 5
  businesses with mappings — all in the dev bucket); held-out businesses have no mappings yet.
- The 3 value mismatches are executor-side over-counts on multi-day revenue windows; the
  per-question log (`src/analysis_out/value_run.jsonl`, untracked) holds the diagnostic detail.
