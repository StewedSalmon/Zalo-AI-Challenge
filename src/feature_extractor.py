#!/usr/bin/env python3
"""Compatibility entry point for Query Spec v1.

The implementation lives in query_parser.py, date_resolver.py, query_spec.py and
query_validator.py. This module keeps the old command name working:

    python feature_extractor.py --questions ... --dictionary ... --cues ... --out ...

The --rules argument is accepted but ignored so pipeline/run_pipeline.py does not
crash during migration. No routing or template file is read.
"""
from __future__ import annotations

from query_parser import (  # noqa: F401
    Lexicon,
    extract_record,
    fold_phrase,
    fold_token,
    main,
    parse_question,
    span_json,
    tokenize,
)

if __name__ == "__main__":
    raise SystemExit(main())