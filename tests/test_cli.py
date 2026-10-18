import json

from typer.testing import CliRunner

from dataguard.cli import app

runner = CliRunner()


def test_clean_and_warning_exit_codes(tmp_path):
    path = tmp_path / "input.jsonl"
    path.write_text('{"text":"A sufficiently long example"}\n', encoding="utf-8")
    assert runner.invoke(app, ["check", str(path)]).exit_code == 0
    path.write_text('{"text":"short"}\n', encoding="utf-8")
    result = runner.invoke(app, ["check", str(path)])
    assert result.exit_code == 1
    assert "short_text" in result.stdout
    assert runner.invoke(app, ["check", str(path), "--fail-on", "error"]).exit_code == 0


def test_reports_escape_html_and_redact_values(tmp_path):
    data = tmp_path / "input.jsonl"
    data.write_text(json.dumps({"text": "test@example.org", "label": "<script>"}), encoding="utf-8")
    json_out, html_out = tmp_path / "reports/out.json", tmp_path / "reports/out.html"
    result = runner.invoke(
        app, ["check", str(data), "--json-report", str(json_out), "--html-report", str(html_out)]
    )
    assert result.exit_code == 1
    assert json.loads(json_out.read_text())["rows"] == 1
    assert "test@example.org" not in json_out.read_text()
    assert "<script>" not in html_out.read_text()
    assert "&lt;script&gt;" in html_out.read_text()


def test_schema_and_input_errors(tmp_path):
    data, schema = tmp_path / "in.jsonl", tmp_path / "schema.json"
    data.write_text('{"text": 123}', encoding="utf-8")
    schema.write_text('{"text":"str"}', encoding="utf-8")
    assert runner.invoke(app, ["check", str(data), "--schema", str(schema)]).exit_code == 1
    schema.write_text("[]", encoding="utf-8")
    assert runner.invoke(app, ["check", str(data), "--schema", str(schema)]).exit_code == 2
    schema.write_text("{broken", encoding="utf-8")
    assert runner.invoke(app, ["check", str(data), "--schema", str(schema)]).exit_code == 2
    assert runner.invoke(app, ["check", str(data), "--json-report", str(data)]).exit_code == 2
    assert runner.invoke(app, ["check", str(tmp_path / "absent.jsonl")]).exit_code == 2


def test_unknown_format_and_output_collision(tmp_path):
    data = tmp_path / "in.txt"
    data.write_text("something", encoding="utf-8")
    assert runner.invoke(app, ["check", str(data)]).exit_code == 2
    assert (
        runner.invoke(
            app, ["check", str(data), "--json-report", "same", "--html-report", "same"]
        ).exit_code
        == 2
    )


def test_against_cli_reports_leakage_and_protects_reference(tmp_path):
    data = tmp_path / "train.jsonl"
    reference = tmp_path / "test.jsonl"
    content = '{"text":"A sufficiently long shared example"}\n'
    data.write_text(content, encoding="utf-8")
    reference.write_text(content, encoding="utf-8")
    result = runner.invoke(app, ["check", str(data), "--against", str(reference)])
    assert result.exit_code == 1
    assert "leak_exact: 1" in result.stdout
    assert (
        runner.invoke(
            app, ["check", str(data), "--against", str(reference), "--json-report", str(reference)]
        ).exit_code
        == 2
    )
    assert reference.read_text(encoding="utf-8") == content
