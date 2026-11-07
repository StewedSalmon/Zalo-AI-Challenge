# Environment Setup Requirements (DataMate, Zalo AI Challenge 2026)

Source: `README.md`, `public_test/rulebook.md`, `rep.md`.

None of the three files pins a version, base image, or model name. Items marked "(inferred)" are implied by the spec but not named explicitly.

## 1. Packaging and runtime

- **Docker**: required. The submission is a Docker image containing code, weights, and `predict.sh` (README: Submission; rep.md: S11).
- **Dockerfile**: required to build the image (rep.md: S11).
- **predict.sh**: entry point inside the image. Reads `/data`, writes `/result/submission.jsonl` and per-question logs to `/result/logs/` (README: Submission; rep.md: S0, S10).
- **Model weights**: must be bundled inside the image. No model is named in any file (rep.md: S11).
- **Cold-run timing**: the image must complete a cold run on `/data` within time. Inference time is the tie-breaker (README: Scoring; rep.md: S11).

## 2. Language and libraries (inferred / optional)

- **Python**: not named, but rep.md refers to pandas and deterministic code (S7). Version not specified.
- **pandas**: named as an option for computation ("pandas or SQL", rep.md: S7).
- **SQL engine**: named as an alternative to pandas for computation (rep.md: S7). Engine not specified.
- **CSV reader**: input format (README: Task; rep.md: S3).
- **Excel reader (XLSX)**: input format (README: Task; rep.md: S3). Library not specified (inferred: e.g. openpyxl).
- **SQLite reader**: input format (README: Task; rep.md: S3). Library not specified (inferred: Python `sqlite3` stdlib).
- **Vietnamese text normalization**: needed for questions without diacritics, e.g. "thang nay hang nao ban cham nhat" (rep.md: S1). Library not specified.
- **Number and date parsing**: e.g. "560,000" to 560000, "1 thg 6 2025" to a date (rep.md: S3). Implementation not specified.
- **JSON / JSONL writer**: output format (README: Submission; rulebook §9).

## 3. Models (optional, not named)

- **Question understanding (S4)**: extracts answer type, metric, entity, filters, and time expression into a structured spec. The method is not specified; rep.md does not name an LLM or NLP model.
- **Answerability / confidence (S9)**: abstain when confidence is below threshold (rep.md: S9, "above 50%"). The confidence source is not specified.

## 4. Data files and reference resources

- **calendar.csv**: lunar calendar table in the same folder as the rulebook, used for Tết windows (rulebook §2). Required when a question involves Tết.
- **Business README.md** per shop under `/data`: read for unit conversion tables and shop-specific rules (rulebook §10; rep.md: S2, S3).
- **Rulebook.md** per split: default rules (rulebook; rep.md: S2).
- **questions.jsonl**: input per question (README: Task; rep.md: S0).
- **answers.jsonl** (training only): used for evaluation (README: Rules and data; rep.md: S12).
- **train/questions.jsonl**: used for the evaluation loop (rep.md: S12). Path is referenced but the folder is not included in the files provided.

## 5. Tooling for development and evaluation

- **Evaluation script**: compares predictions to `answers.jsonl` with type and value match plus alias handling. Reports accuracy and penalty by type and business (rep.md: S12).
- **Schema validator**: checks each output line against its type format and confirms every question id appears once (rulebook §9; rep.md: S10).
- **Logging**: per-question record with id, final answer, elapsed time, and derivation path (README: Submission; rep.md: S10).

## 6. Missing from the files (to confirm with organizers)

- Python version and base Docker image.
- Exact library versions (pandas, Excel and SQLite readers).
- Which model(s) and weights, if any, are used for question understanding.
- Time limit for the cold run.
- Penalized Accuracy formula (README says it did not come through in the paste; rep.md uses rulebook values +1 / -1 / 0 as a working assumption).
- Whether `train/` data (questions, answers, business files) is available in the working folder.
