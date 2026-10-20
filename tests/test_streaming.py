import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from typer.testing import CliRunner

from dataguard.cli import app
from dataguard.core import scan
from dataguard.streaming import scan_stream


def write_rows(path, rows):
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    return path


@pytest.mark.parametrize("extension", ["jsonl", "csv", "parquet"])
def test_formats_and_cross_batch_exact_matching(tmp_path, extension):
    rows = [
        {"text": "Long enough text", "label": "a"},
        {"text": "Entirely different words", "label": "b"},
        {"text": " LONG enough TEXT  ", "label": "b"},
    ]
    path = tmp_path / f"input.{extension}"
    if extension == "jsonl":
        write_rows(path, rows)
    elif extension == "csv":
        path.write_text(
            "text,label\nLong enough text,a\nEntirely different words,b\n LONG enough TEXT  ,b\n",
            encoding="utf-8",
        )
    else:
        pq.write_table(pa.Table.from_pylist(rows), path)
    report = scan_stream(path, batch_size=1)
    assert report.rows == 3
    assert report.labels == {"a": 1, "b": 2}
    assert report.finding_counts == {"duplicate_exact": 1}
    assert report.findings[0].row == 3
    assert report.findings[0].detail == "other_row=1; similarity=1.000"


def test_reference_errors_survive_detail_cap_and_redact(tmp_path):
    reference = write_rows(
        tmp_path / "heldout.jsonl",
        [{"text": "test@example.org"}, {"text": "test@example.org"}, {"text": None}],
    )
    data = write_rows(
        tmp_path / "data.jsonl",
        [{"text": "test@example.org", "label": "test@example.org"}, {"text": "short"}],
    )
    report = scan_stream(data, against=reference, batch_size=1, max_findings=1)
    assert report.against_rows == 3
    assert report.findings_truncated
    assert len(report.findings) == 1
    assert report.finding_counts["leak_exact"] == 1
    assert report.failed("error")
    assert "test@example.org" not in report.model_dump_json()
    assert report.labels == {"[redacted]": 1}
    result = CliRunner().invoke(
        app,
        [
            "check",
            str(data),
            "--against",
            str(reference),
            "--streaming",
            "--max-findings",
            "0",
            "--fail-on",
            "error",
        ],
    )
    assert result.exit_code == 1
    assert "leak_exact: 1" in result.stdout
    assert "capped at 0" in result.stdout


def test_near_matches_global_locations_and_declared_limits(tmp_path):
    text = "one two three four five six seven eight nine ten eleven twelve"
    rows = [
        {"text": "unrelated batch one words"},
        {"text": "distinct batch one words"},
        {"text": text},
        {"text": text + " thirteen"},
    ]
    path = write_rows(tmp_path / "data.jsonl", rows)
    report = scan_stream(path, batch_size=2)
    match = next(f for f in report.findings if f.code == "duplicate_near")
    assert match.row == 4
    assert match.detail.startswith("other_row=3;")
    assert report.near_match_scope == "within_primary_batch_only"
    limited = scan_stream(path, batch_size=1)
    assert "duplicate_near" not in limited.finding_counts
    assert not limited.failed()


def test_empty_schema_invalid_rows_and_capacity_errors(tmp_path):
    path = write_rows(tmp_path / "data.jsonl", [])
    report = scan_stream(path, max_findings=0)
    assert report.failed("error")
    assert report.finding_counts == {"empty_dataset": 1}
    with pytest.raises(ValueError, match="Schema"):
        scan_stream(path, schema={"text": "unsupported"})
    for options in ({"batch_size": 0}, {"max_findings": -1}, {"max_labels": 0}):
        with pytest.raises(ValueError, match="capacity"):
            scan_stream(path, **options)
    write_rows(path, [{"text": None, "label": "a"}, {"label": "b"}])
    report = scan_stream(path, schema={"text": "str"})
    assert report.finding_counts["schema"] == 2
    with pytest.raises(ValueError, match="label capacity"):
        scan_stream(path, batch_size=1, max_labels=1)
    result = CliRunner().invoke(app, ["check", str(path), "--streaming", "--max-labels", "1"])
    assert result.exit_code == 2


def test_global_label_imbalance_and_normal_scan_equivalence(tmp_path):
    rows = [
        {"text": "different sample alpha", "label": "a"},
        {"text": "different sample beta", "label": "b"},
    ]
    path = write_rows(tmp_path / "data.jsonl", rows)
    report = scan_stream(path, batch_size=1)
    assert report.findings == scan(rows).findings == []
    write_rows(path, [{"text": "short", "label": "a"}] * 5)
    report = scan_stream(path, batch_size=2)
    assert report.finding_counts["label_imbalance"] == 1
    assert report.finding_counts["duplicate_exact"] == 4
    assert report.failed()
    assert not report.failed("error")


def test_streaming_cli_reports_and_protects_inputs(tmp_path):
    path = write_rows(tmp_path / "data.jsonl", [{"text": "test@example.org"}])
    output = tmp_path / "report.json"
    runner = CliRunner()
    result = runner.invoke(app, ["check", str(path), "--streaming", "--json-report", str(output)])
    assert result.exit_code == 1
    assert "cross-file: exact only" in result.stdout
    assert json.loads(output.read_text())["finding_counts"] == {"pii_email": 1}
    assert (
        runner.invoke(
            app, ["check", str(path), "--streaming", "--json-report", str(path)]
        ).exit_code
        == 2
    )
