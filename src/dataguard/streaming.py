"""Bounded row windows and reports, with a temporary disk index for exact matches."""

import hashlib
import sqlite3
from collections import Counter
from contextlib import closing
from itertools import islice
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import Field

from .core import Finding, Report, iter_rows, scan
from .similarity import canonical


class StreamingReport(Report):
    finding_counts: dict[str, int] = Field(default_factory=dict)
    severity_counts: dict[str, int] = Field(default_factory=dict)
    findings_truncated: bool = False
    near_match_scope: str = "within_primary_batch_only"
    batch_size: int

    def failed(self, level: str = "warning") -> bool:
        return bool(
            self.severity_counts.get("error", 0)
            or (level == "warning" and self.severity_counts.get("warning", 0))
        )


def scan_stream(
    path: Path,
    *,
    text_field: str = "text",
    label_field: str = "label",
    min_length: int = 10,
    schema: dict[str, str] | None = None,
    against: Path | None = None,
    near_threshold: float = 0.8,
    batch_size: int = 128,
    max_findings: int = 1000,
    max_labels: int = 1000,
) -> StreamingReport:
    """Exact matches span the whole input; near discovery is limited to each batch.

    Cross-file matching is exact only. Counts cover all findings even when their
    stored details are capped. Too many distinct labels fail explicitly.
    """
    if batch_size < 1 or max_findings < 0 or max_labels < 1:
        raise ValueError("Invalid streaming capacity")
    # Validate configuration even when the input is empty.
    scan([], schema=schema, near_threshold=near_threshold)
    report = StreamingReport(source=path.name, rows=0, batch_size=batch_size)
    labels: Counter[str] = Counter()

    def add(finding: Finding) -> None:
        for counts, key in (
            (report.finding_counts, finding.code),
            (report.severity_counts, finding.severity),
        ):
            counts[key] = counts.get(key, 0) + 1
        if len(report.findings) < max_findings:
            report.findings.append(finding)
        else:
            report.findings_truncated = True

    def key_for(row: dict) -> bytes | None:
        text = row.get(text_field)
        value = canonical(text) if isinstance(text, str) else ""
        return hashlib.sha256(value.encode()).digest() if value else None

    with TemporaryDirectory(prefix="dataguard-") as directory:
        with closing(sqlite3.connect(str(Path(directory) / "exact.sqlite"))) as index:
            index.execute("PRAGMA cache_size = -2048")
            index.execute(
                "CREATE TABLE seen (scope TEXT, digest BLOB, row INTEGER, "
                "PRIMARY KEY (scope, digest)) WITHOUT ROWID"
            )
            if against is not None:
                report.against_rows = 0
                for n, row in enumerate(iter_rows(against, batch_size), 1):
                    report.against_rows = n
                    key = key_for(row)
                    if key is not None:
                        index.execute(
                            "INSERT OR IGNORE INTO seen VALUES ('reference', ?, ?)", (key, n)
                        )
                    if n % batch_size == 0:
                        index.commit()
                index.commit()
            rows = iter_rows(path, batch_size)
            while batch := list(islice(rows, batch_size)):
                offset = report.rows
                partial = scan(
                    batch,
                    text_field=text_field,
                    label_field=label_field,
                    min_length=min_length,
                    schema=schema,
                    near_threshold=near_threshold,
                )
                report.rows += partial.rows
                for finding in partial.findings:
                    if finding.code in {"duplicate_exact", "label_imbalance"}:
                        continue
                    if finding.row is not None:
                        finding.row += offset
                    if finding.code == "duplicate_near":
                        location, score = finding.detail.split("; ", 1)
                        other = int(location.split("=", 1)[1]) + offset
                        finding.detail = f"other_row={other}; {score}"
                    add(finding)
                for label, count in partial.labels.items():
                    if label not in labels and len(labels) >= max_labels:
                        raise ValueError("Streaming label capacity exceeded")
                    labels[label] += count
                for n, row in enumerate(batch, offset + 1):
                    key = key_for(row)
                    if key is None:
                        continue
                    for scope, code, severity, location in (
                        ("primary", "duplicate_exact", "warning", "other_row"),
                        ("reference", "leak_exact", "error", "against_row"),
                    ):
                        first = index.execute(
                            "SELECT row FROM seen WHERE scope = ? AND digest = ?", (scope, key)
                        ).fetchone()
                        if first:
                            add(
                                Finding(
                                    code=code,
                                    severity=severity,
                                    row=n,
                                    field=text_field,
                                    detail=f"{location}={first[0]}; similarity=1.000",
                                )
                            )
                    index.execute("INSERT OR IGNORE INTO seen VALUES ('primary', ?, ?)", (key, n))
                index.commit()
    report.labels = dict(sorted(labels.items()))
    if not report.rows:
        add(Finding(code="empty_dataset", severity="error"))
    if labels and max(labels.values()) / sum(labels.values()) > 0.8:
        add(Finding(code="label_imbalance", detail="Largest class exceeds 80%"))
    return report
