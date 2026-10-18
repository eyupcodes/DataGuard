"""CLI exit codes: 0 clean, 1 findings at selected level, 2 invalid input."""

import html
import json
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from .core import read_rows, scan

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
    except (OSError, ValueError, TypeError) as exc:
        # Parsing libraries may echo input; keep their raw error text out of reports.
        typer.echo(
            "Input error: check file formats, schema, dependencies and report paths", err=True
        )
        raise typer.Exit(2) from exc
    typer.echo(f"{report.rows} rows; {len(report.findings)} findings")
    for code in sorted({f.code for f in report.findings}):
        typer.echo(f"  {code}: {sum(f.code == code for f in report.findings)}")
    raise typer.Exit(1 if report.failed(fail_on.value) else 0)
