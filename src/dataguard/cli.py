"""CLI exit codes: 0 clean, 1 findings at selected level, 2 invalid input."""

import html
import json
import sqlite3
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from .core import read_rows, scan
from .streaming import StreamingReport, scan_stream

app = typer.Typer(no_args_is_help=True)


class FailOn(StrEnum):
    warning = "warning"
    error = "error"


@app.callback()
def main() -> None:
    """Check local datasets without sending their contents anywhere."""


@app.command()
def check(
    data: Path,
    text_field: str = "text",
    label_field: str = "label",
    min_length: Annotated[int, typer.Option(min=0)] = 10,
    schema: Path | None = None,
    against: Path | None = None,
    near_threshold: Annotated[float, typer.Option(min=0.01, max=1)] = 0.8,
    json_report: Path | None = None,
    html_report: Path | None = None,
    fail_on: FailOn = FailOn.warning,
    streaming: bool = False,
    batch_size: Annotated[int, typer.Option(min=1, max=2048)] = 128,
    max_findings: Annotated[int, typer.Option(min=0)] = 1000,
    max_labels: Annotated[int, typer.Option(min=1)] = 1000,
) -> None:
    """Scan JSONL, CSV or Parquet and optionally write redacted reports."""
    try:
        outputs = [p.resolve() for p in (json_report, html_report) if p]
        inputs = [data.resolve()] + ([schema.resolve()] if schema else [])
        if against:
            inputs.append(against.resolve())
        if any(p in inputs for p in outputs) or len(set(outputs)) != len(outputs):
            raise ValueError("Report paths must differ from input paths and each other")
        spec = json.loads(schema.read_text(encoding="utf-8-sig")) if schema else None
        if spec is not None and (
            not isinstance(spec, dict)
            or any(
                not isinstance(k, str) or not k.isidentifier() or k.startswith("_") for k in spec
            )
        ):
            raise ValueError("Schema must be an object with field names as keys")
        if streaming:
            report = scan_stream(
                data,
                text_field=text_field,
                label_field=label_field,
                min_length=min_length,
                schema=spec,
                against=against,
                near_threshold=near_threshold,
                batch_size=batch_size,
                max_findings=max_findings,
                max_labels=max_labels,
            )
        else:
            report = scan(
                read_rows(data),
                data.name,
                text_field,
                label_field,
                min_length,
                spec,
                read_rows(against) if against else None,
                near_threshold,
            )
        payload = report.model_dump_json(indent=2)
        for path, content in (
            (json_report, payload),
            (
                html_report,
                "<!doctype html><html lang='en'><meta charset='utf-8'>"
                "<title>DataGuard quality report</title><h1>DataGuard quality report</h1><pre>"
                + html.escape(payload)
                + "</pre></html>",
            ),
        ):
            if path:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
    except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
        # Parsing libraries may echo input; keep their raw error text out of reports.
        typer.echo(
            "Input error: check file formats, schema, dependencies and report paths", err=True
        )
        raise typer.Exit(2) from exc
    counts = (
        report.finding_counts
        if isinstance(report, StreamingReport)
        else {
            code: sum(f.code == code for f in report.findings)
            for code in {f.code for f in report.findings}
        }
    )
    typer.echo(f"{report.rows} rows; {sum(counts.values())} findings")
    if isinstance(report, StreamingReport):
        typer.echo("Near matches: within each primary batch only; cross-file: exact only")
        if report.findings_truncated:
            typer.echo(f"Finding details capped at {len(report.findings)}; counts include all")
    for code in sorted(counts):
        typer.echo(f"  {code}: {counts[code]}")
    raise typer.Exit(1 if report.failed(fail_on.value) else 0)
