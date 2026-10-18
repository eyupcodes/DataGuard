# DataGuard

Local quality checks for ML and LLM datasets. Python 3.11+. No network calls, external services or automatic uploads. MIT licensed. The distribution is named `dataguard-local`; the CLI is `dataguard`.

## Install and try

```sh
python -m pip install -e ".[dev,parquet]"
dataguard check examples/clean.jsonl --json-report reports/clean.json --html-report reports/clean.html
```

The example returns exit **0**. JSONL, CSV and Parquet are supported; Parquet needs the optional `parquet` extra. Inputs are loaded into memory, so this first version targets small and medium datasets.

Checks: strict field/type schema validation, missing/empty/short text, email/phone/Turkish identity/IBAN candidates, label distribution and class imbalance (largest class >80%), exact/near duplicates and cross-file leakage. Reports contain row/field locations and finding types, without matched text or personal values. Label values containing detected PII are redacted.

```sh
dataguard check train.jsonl --text-field content --label-field category --min-length 20 --schema schema.json
dataguard check train.jsonl --against test.jsonl --near-threshold 0.8
```

A schema is a JSON object such as `{"content":"str","category":"str"}`. Every listed field is required; supported types are `str`, `int`, `float`, `bool`, with extra fields allowed. CSV fields are strings: numeric CSV schema fields need preprocessing, since strict validation does not coerce strings.

Exit codes: **0** clean, **1** findings, **2** invalid input/configuration. `--fail-on error` ignores warnings for exit status. Original data is never edited. Report paths must differ from input/configuration files.

PII detection is heuristic, not a privacy guarantee. Identity numbers and IBANs use checksum validation; phone candidates may include other long numbers. There are false positives and false negatives. Human review remains necessary before publishing datasets.

## Duplicates and split leakage

`--against` checks the selected text field against a held-out file in any supported format. A matching primary row is an error (`leak_exact` / `leak_near`) and returns exit 1 even with `--fail-on error`. Same-file duplicates are warnings. Locations are one-based data-row numbers, excluding the CSV header and blank JSONL lines. `other_row` refers to the same file; `against_row` refers to the held-out file.

Exact matches normalize whitespace and case. Each exact duplicate points to the first matching source row. Near matching uses word trigrams, 64 deterministic MinHash permutations and 16 LSH bands, then verifies candidate pairs with exact Jaccard overlap. `--near-threshold` defaults to 0.8 (CLI range 0.01–1). Short texts use their full token sequence as one shingle; empty/punctuation-only texts do not generate near candidates.

**MinHash is approximate candidate discovery:** near matches can be missed. Reported near-match scores are exact Jaccard values for discovered candidates. This is lexical similarity, not semantic deduplication, and is not a guarantee that a train/test split has no leakage. Exact normalized matches are checked independently of MinHash. Large groups of near duplicates can produce many findings; streaming/capacity controls are future work in #3.

## Development

```sh
ruff check .
ruff format --check .
python -m pytest --cov=dataguard --cov-fail-under=85
```

CI covers Python 3.11/3.14 on Linux and 3.11 on Windows. No package or release is automatically published.

## Roadmap

- [#1 Dataset readers, quality checks and reports](https://github.com/eyupcodes/DataGuard/issues/1)
- [#2 MinHash duplicates and cross-split leakage](https://github.com/eyupcodes/DataGuard/issues/2)
- [#3 Streaming large scans](https://github.com/eyupcodes/DataGuard/issues/3)
- [#4 Reusable schemas and policies](https://github.com/eyupcodes/DataGuard/issues/4)
- [#5 Public-data performance benchmark](https://github.com/eyupcodes/DataGuard/issues/5)
