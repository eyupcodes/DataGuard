import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from dataguard.core import pii_types, read_rows, scan, schema_model, valid_iban, valid_tc


def test_readers(tmp_path):
    rows = [{"text": "A sufficiently long text", "label": "a"}]
    jsonl = tmp_path / "data.jsonl"
    jsonl.write_text(json.dumps(rows[0]) + "\n\n", encoding="utf-8")
    assert read_rows(jsonl) == rows
    csv = tmp_path / "data.csv"
    csv.write_text("text,label\nA sufficiently long text,a\n", encoding="utf-8")
    assert read_rows(csv) == rows
    parquet = tmp_path / "data.parquet"
    pq.write_table(pa.Table.from_pylist(rows), parquet)
    assert read_rows(parquet) == rows


@pytest.mark.parametrize("content,error", [("bad", "line 1"), ("[]", "object")])
def test_invalid_jsonl(tmp_path, content, error):
    path = tmp_path / "data.jsonl"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match=error):
        read_rows(path)


def test_unknown_format_and_schema(tmp_path):
    with pytest.raises(ValueError, match="Expected"):
        read_rows(tmp_path / "data.txt")
    with pytest.raises(ValueError, match="Schema"):
        schema_model({"x": "unknown"})
    assert scan([], schema={"text": "str"}).findings[0].code == "empty_dataset"


def test_schema_text_and_label_findings():
    rows = [
        {"text": "ok", "label": "a"},
        {"text": " ", "label": "a"},
        {"text": None, "label": "a"},
        {"text": "Long enough here", "label": "a"},
    ]
    report = scan(rows, schema={"text": "str", "id": "int"})
    codes = {f.code for f in report.findings}
    assert {"schema", "text_type", "short_text", "empty_text", "label_imbalance"} <= codes
    assert report.failed("error")
    assert report.labels == {"a": 4}
    clean = scan(
        [{"text": "Long enough here", "label": "a"}, {"text": "Another long text", "label": "b"}],
        schema={"text": "str"},
    )
    assert not clean.failed()
    assert not scan([{"text": "short"}]).failed("error")


def test_pii_patterns_validate_checksums_and_never_echo_values():
    # Published format examples, not user data.
    email = "test@example.org"
    assert pii_types(email) == {"email"}
    assert "phone" in pii_types("+90 555 123 45 67")
    assert valid_tc("10000000146")
    assert not valid_tc("00000000000")
    assert not valid_tc("12345678901")
    assert not valid_tc("abc")
    assert valid_iban("GB82 WEST 1234 5698 7654 32")
    assert not valid_iban("GB00 WEST 1234 5698 7654 32")
    assert not valid_iban("invalid")
    assert "iban" in pii_types("GB82WEST12345698765432")
    assert "iban" in pii_types("Pay GB82 WEST 1234 5698 7654 32 today please")
    assert "phone" in pii_types("05551234567")
    assert "tc_identity" in pii_types("10000000146")
    report = scan([{"text": "A long safe string", "contact": {"email": email}, "label": email}])
    assert "pii_email" in {f.code for f in report.findings}
    assert email not in report.model_dump_json()


def test_duplicate_and_cross_split_locations():
    original = "one two three four five six seven eight nine ten eleven twelve"
    rows = [
        {"text": original},
        {"text": original.upper()},
        {"text": original + " thirteen"},
        {"text": None},
    ]
    report = scan(rows, against=[{"text": original}])
    findings = {f.code: f for f in report.findings}
    assert findings["duplicate_exact"].row == 2
    assert findings["duplicate_near"].row == 3
    assert findings["leak_exact"].severity == "error"
    assert "against_row=1" in findings["leak_near"].detail
    assert report.against_rows == 1
    assert original not in report.model_dump_json()
