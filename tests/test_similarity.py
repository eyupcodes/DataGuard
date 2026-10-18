import pytest

from dataguard.similarity import canonical, matches, shingles, signature


def test_exact_matches_do_not_depend_on_signatures():
    assert canonical("Hello  WORLD") == "hello world"
    result = matches(["Hello world", "hello  WORLD", "unrelated sentence"])
    assert [(m.row, m.other_row, m.kind) for m in result] == [(2, 1, "exact")]
    assert matches(["", " ", ""]) == []


def test_minhash_is_deterministic_and_near_candidates_are_verified():
    text = "one two three four five six seven eight nine ten eleven twelve"
    near = text + " thirteen"
    assert signature(shingles(text)) == signature(shingles(text))
    assert len(signature(set())) == 64
    found = matches([text, near, "completely different content here"])
    assert len(found) == 1
    assert found[0].kind == "near"
    assert found[0].similarity == pytest.approx(10 / 11)
    assert matches([text, near], threshold=1) == []


def test_cross_split_exact_and_near_leakage():
    left = ["one two three four five six seven eight nine ten eleven twelve"]
    right = [left[0].upper(), left[0] + " thirteen", "something unrelated"]
    found = matches(left, right)
    assert [(m.row, m.other_row, m.kind) for m in found] == [(1, 1, "exact"), (2, 1, "near")]
    assert matches([], right) == []
    with pytest.raises(ValueError):
        matches(left, right, 0)


def test_short_text_and_punctuation_only_are_safe():
    assert shingles("one two") == {"one two"}
    assert shingles("!!!") == set()
    assert matches(["one", "one", "!!!", "???"])[0].kind == "exact"
