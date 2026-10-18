"""Small-dataset quality checks; findings contain locations, never raw values."""

import csv
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from .similarity import matches


class Finding(BaseModel):
    code: str
    severity: Literal["error", "warning"] = "warning"
    row: int | None = None
    field: str | None = None
    detail: str = ""


class Report(BaseModel):
    source: str
    rows: int
    findings: list[Finding] = Field(default_factory=list)
    labels: dict[str, int] = Field(default_factory=dict)
    against_rows: int | None = None

    def failed(self, level: str = "warning") -> bool:
        return any(f.severity == "error" or level == "warning" for f in self.findings)


def read_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".jsonl":
        rows = []
        for n, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at line {n}") from exc
    elif path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    elif path.suffix.lower() == ".parquet":
        try:
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise ValueError("Parquet requires pip install 'dataguard-local[parquet]'") from exc
        rows = pq.read_table(path).to_pylist()
    else:
        raise ValueError("Expected .jsonl, .csv or .parquet")
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("Each row must be an object")
    return rows


def schema_model(spec: dict[str, str]) -> type[BaseModel]:
    types = {"str": str, "int": int, "float": float, "bool": bool}
    if not spec or any(t not in types for t in spec.values()):
        raise ValueError("Schema maps required fields to str, int, float or bool")
    return create_model(
        "DatasetRow",
        __config__=ConfigDict(strict=True, extra="allow"),
        **{key: (types[value], ...) for key, value in spec.items()},
    )


def valid_tc(value: str) -> bool:
    if len(value) != 11 or not value.isascii() or not value.isdigit() or value[0] == "0":
        return False
    d = list(map(int, value))
    return (7 * sum(d[:9:2]) - sum(d[1:8:2])) % 10 == d[9] and sum(d[:10]) % 10 == d[10]


def valid_iban(value: str) -> bool:
    value = re.sub(r"\s", "", value).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", value):
        return False
    # MOD-97 check; country-specific lengths are intentionally not inferred.
    digits = "".join(str(ord(c) - 55) if c.isalpha() else c for c in value[4:] + value[:4])
    return int(digits) % 97 == 1


def pii_types(text: str) -> set[str]:
    kinds = set()
    if re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", text):
        kinds.add("email")
    phones = re.findall(r"(?<!\w)\+?\d[\d ().-]{8,20}\d(?!\w)", text)
    if any(10 <= len(re.sub(r"\D", "", phone)) <= 15 for phone in phones):
        kinds.add("phone")
    if any(valid_tc(m) for m in re.findall(r"(?<!\d)\d{11}(?!\d)", text)):
        kinds.add("tc_identity")
    iban_candidates = re.findall(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]){11,30}\b", text.upper())
    if any(
        valid_iban(re.sub(r"\s", "", value)[:length])
        for value in iban_candidates
        for length in range(15, 35)
        if len(re.sub(r"\s", "", value)) >= length
    ):
        kinds.add("iban")
    return kinds


def scan(
    rows: list[dict[str, Any]],
    source: str = "dataset",
    text_field: str = "text",
    label_field: str = "label",
    min_length: int = 10,
    schema: dict[str, str] | None = None,
    against: list[dict[str, Any]] | None = None,
    near_threshold: float = 0.8,
) -> Report:
    report = Report(source=source, rows=len(rows))
    model = schema_model(schema) if schema is not None else None
    if not rows:
        report.findings.append(Finding(code="empty_dataset", severity="error"))
    labels: Counter[str] = Counter()
    for n, row in enumerate(rows, 1):
        if model:
            try:
                model.model_validate(row)
            except ValidationError as exc:
                for err in exc.errors(include_input=False):
                    report.findings.append(
                        Finding(
                            code="schema",
                            severity="error",
                            row=n,
                            field=".".join(map(str, err["loc"])),
                            detail=err["type"],
                        )
                    )
        text = row.get(text_field)
        if not isinstance(text, str):
            report.findings.append(
                Finding(code="text_type", severity="error", row=n, field=text_field)
            )
        elif not text.strip():
            report.findings.append(Finding(code="empty_text", row=n, field=text_field))
        elif len(text.strip()) < min_length:
            report.findings.append(Finding(code="short_text", row=n, field=text_field))
        # Scan nested and non-text fields as well; do not retain matched values.
        for key, value in row.items():
            for kind in sorted(pii_types(json.dumps(value, ensure_ascii=False))):
                report.findings.append(Finding(code=f"pii_{kind}", row=n, field=str(key)))
        if label_field in row:
            # Do not echo potential personal data in labels either.
            label = str(row[label_field])
            label = "[redacted]" if pii_types(label) else label
            labels[label] += 1
    report.labels = dict(sorted(labels.items()))
    if labels and max(labels.values()) / sum(labels.values()) > 0.8:
        report.findings.append(Finding(code="label_imbalance", detail="Largest class exceeds 80%"))
    texts = [row.get(text_field) if isinstance(row.get(text_field), str) else "" for row in rows]
    for match in matches(texts, threshold=near_threshold):
        report.findings.append(
            Finding(
                code=f"duplicate_{match.kind}",
                row=match.row,
                field=text_field,
                detail=f"other_row={match.other_row}; similarity={match.similarity:.3f}",
            )
        )
    if against is not None:
        report.against_rows = len(against)
        reference = [
            row.get(text_field) if isinstance(row.get(text_field), str) else "" for row in against
        ]
        for match in matches(reference, texts, threshold=near_threshold):
            report.findings.append(
                Finding(
                    code=f"leak_{match.kind}",
                    severity="error",
                    row=match.row,
                    field=text_field,
                    detail=f"against_row={match.other_row}; similarity={match.similarity:.3f}",
                )
            )
    return report
