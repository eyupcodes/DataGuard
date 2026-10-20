# Changelog

## Unreleased

- Add incremental JSONL/CSV and batched Parquet readers.
- Add `--streaming` with a temporary disk index for global exact duplicates and exact split leakage, capped finding details, complete finding counts and bounded label cardinality.
- Declare streaming near-match coverage explicitly: within each primary batch only; cross-file matching is exact only.

- Deterministic MinHash/LSH candidate discovery with exact Jaccard verification.
- Normalized exact/near duplicate findings and `--against` train/test leakage checks.
- JSONL/CSV/optional Parquet loading; strict schemas; text length, PII and label checks.
- Local CLI with CI exit codes and redacted JSON/HTML reports.
- Test coverage gate of 85% and Linux/Windows CI.
