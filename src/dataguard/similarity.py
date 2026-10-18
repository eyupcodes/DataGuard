"""Deterministic MinHash/LSH candidates, verified with exact shingle Jaccard."""

import hashlib
import random
import re
from collections import defaultdict
from dataclasses import dataclass

PRIME = (1 << 61) - 1
PERMUTATIONS = 64
BANDS = 16
_rng = random.Random(42)
_coefficients = [(_rng.randrange(1, PRIME), _rng.randrange(PRIME)) for _ in range(PERMUTATIONS)]


def canonical(text: str) -> str:
    return " ".join(text.casefold().split())


def shingles(text: str) -> set[str]:
    words = re.findall(r"\w+", text.casefold())
    if not words:
        return set()
    width = min(3, len(words))
    return {" ".join(words[i : i + width]) for i in range(len(words) - width + 1)}


def signature(parts: set[str]) -> tuple[int, ...]:
    hashes = [
        int.from_bytes(hashlib.blake2b(p.encode(), digest_size=8).digest(), "big") % PRIME
        for p in parts
    ]
    return tuple(
        min((a * h + b) % PRIME for h in hashes) if hashes else PRIME for a, b in _coefficients
    )


@dataclass(frozen=True)
class Match:
    row: int
    other_row: int
    kind: str
    similarity: float


def matches(left: list[str], right: list[str] | None = None, threshold: float = 0.8) -> list[Match]:
    """One-based locations; same-file rows reference earlier rows only.

    LSH can miss near duplicates. Verified candidates have no approximate
    similarity false positives at the selected threshold. Exact normalized
    matches are indexed separately and never depend on LSH recall.
    """
    if not 0 < threshold <= 1:
        raise ValueError("threshold must be in (0, 1]")
    cross = right is not None
    target = left if right is None else right
    source_parts = [shingles(text) for text in left]
    exact: dict[str, list[int]] = defaultdict(list)
    buckets: dict[tuple[int, tuple[int, ...]], list[int]] = defaultdict(list)
    width = PERMUTATIONS // BANDS
    for i, text in enumerate(left):
        if canonical(text):
            exact[canonical(text)].append(i)
        if source_parts[i]:
            sig = signature(source_parts[i])
            for band in range(BANDS):
                buckets[(band, sig[band * width : (band + 1) * width])].append(i)
    found = []
    for j, text in enumerate(target):
        key = canonical(text)
        exact_ids = [i for i in exact.get(key, []) if cross or i < j]
        # All duplicate rows point to one canonical source, avoiding quadratic reports.
        if exact_ids:
            found.append(Match(j + 1, exact_ids[0] + 1, "exact", 1.0))
        parts = shingles(text)
        if not parts:
            continue
        sig = signature(parts)
        candidates = set()
        for band in range(BANDS):
            candidates.update(buckets.get((band, sig[band * width : (band + 1) * width]), []))
        for i in sorted(candidates):
            if (not cross and i >= j) or canonical(left[i]) == key:
                continue
            union = parts | source_parts[i]
            score = len(parts & source_parts[i]) / len(union) if union else 0
            if score >= threshold:
                found.append(Match(j + 1, i + 1, "near", score))
    return found
