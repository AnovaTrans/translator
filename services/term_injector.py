"""
services/term_injector.py
─────────────────────────
Segment-level termbase injection.

Before segments are serialised into the %SEGMENTS% prompt slot this module
scans each segment's source text against the termbase and prepends inline
[TERM: "X" → "Y"] annotations for every matched term.  Forcing the correct
approved translation into the model's immediate generation context prevents
the LLM from silently reverting to training-data defaults for short or
isolated segments (e.g. table labels like "Input Voltage: …").

Public API
──────────
inject_term_annotations(segments, termbase_path, source_key)
    Standalone entry point — loads the CSV itself.  Matches the function
    signature required by the feature specification.

annotate_segments_with_terms(segments, terms, source_key)
    Same logic but accepts an already-loaded list of (source, target) pairs.
    Use this inside PromptBuilder to avoid reading the CSV a second time.

find_term_matches(text, terms)
    Returns (source_term, target_term, first_position) for every matched term,
    sorted left-to-right.  Exported so PromptBuilder can compute prefix lines
    without re-implementing the matching logic.
"""
from __future__ import annotations

import csv
import re
from typing import Optional


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def inject_term_annotations(
    segments: list[dict],
    termbase_path: str,
    source_key: str = "source",
) -> list[dict]:
    """
    Return a new list of segment dicts with [TERM] annotations prepended where
    termbase matches are found.  The original dicts are **never mutated**.

    Args:
        segments:      ``[{"id": str, "source": str}, …]``
        termbase_path: Path to CSV file (header row: source, target, domain,
                       notes).  Only the first two columns are used.
        source_key:    Key for source text inside each segment dict.

    Returns:
        New list of segment dicts.  Segments with no matches are passed through
        unchanged.  Annotated segments have one or more ``[TERM: "X" → "Y"]``
        lines prepended to the value at *source_key*, separated by newlines.
    """
    terms = _load_termbase(termbase_path)
    return _annotate_segments(segments, terms, source_key)


def annotate_segments_with_terms(
    segments: list[dict],
    terms: list[tuple[str, str]],
    source_key: str = "source",
) -> list[dict]:
    """
    Same behaviour as :func:`inject_term_annotations` but accepts pre-loaded
    term pairs instead of a file path.

    Use this variant inside :class:`~services.prompt_builder.PromptBuilder`
    (which already holds the terms from TBMatcher) to avoid reading the CSV a
    second time.

    Args:
        segments:   ``[{"id": str, "source": str}, …]``
        terms:      ``[(source_term, target_term), …]`` — may be unsorted.
        source_key: Key for source text inside each segment dict.
    """
    return _annotate_segments(segments, terms, source_key)


def find_term_matches(
    text: str,
    terms: list[tuple[str, str]],
) -> list[tuple[str, str, int]]:
    """
    Return ``[(source_term, target_term, first_position), …]`` for every term
    that matches *text*, sorted left-to-right by first occurrence.

    Exported so :class:`~services.prompt_builder.PromptBuilder` can build
    prefix lines without re-implementing the matching logic.

    Matching rules
    ──────────────
    * Case-insensitive, whole-phrase matching.
    * Word-boundary enforcement via ``(?<!\\w)`` / ``(?!\\w)`` look-arounds so
      partial-word matches (e.g. "Voltage" inside "Overvoltage") are rejected.
    * Overlap resolution: a shorter span that is completely contained within a
      longer matched span is discarded — the longer match wins.
    * Each unique source term is reported at most once; its ordering position is
      its **first** occurrence in *text* (left-to-right).
    """
    return _find_term_matches(text, terms)


# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────────────

def _load_termbase(path: str) -> list[tuple[str, str]]:
    """Load (source, target) pairs from CSV, skipping the header row."""
    terms: list[tuple[str, str]] = []
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            next(reader, None)          # skip header
            for row in reader:
                if len(row) >= 2:
                    src = row[0].strip()
                    tgt = row[1].strip()
                    if src and tgt:
                        terms.append((src, tgt))
    except (OSError, StopIteration):
        pass
    return terms


def _build_pattern(term: str) -> re.Pattern:
    """
    Compile a case-insensitive word-boundary pattern for *term*.

    Uses ``(?<!\\w)`` / ``(?!\\w)`` look-arounds instead of bare ``\\b`` so
    that multi-word phrases containing non-alphanumeric characters
    (e.g. "V/f Control", "RS-485") are handled correctly.
    """
    return re.compile(
        r"(?<!\w)" + re.escape(term) + r"(?!\w)",
        re.IGNORECASE,
    )


def _find_term_matches(
    text: str,
    terms: list[tuple[str, str]],
) -> list[tuple[str, str, int]]:
    """Core matching implementation — see :func:`find_term_matches` for docs."""
    if not terms or not text:
        return []

    # Sort longest-first.  When we record spans below, longer spans are found
    # first which simplifies overlap detection: a short span whose entire range
    # is covered by a longer span is simply dominated.
    sorted_terms = sorted(terms, key=lambda t: len(t[0]), reverse=True)

    # ── Pass 1: collect all match spans ───────────────────────────────────────
    # raw: (start, end, canonical_src, tgt)
    raw: list[tuple[int, int, str, str]] = []
    for src_term, tgt_term in sorted_terms:
        if len(src_term) < 2:
            continue
        try:
            pattern = _build_pattern(src_term)
        except re.error:
            continue
        for m in pattern.finditer(text):
            raw.append((m.start(), m.end(), src_term, tgt_term))

    if not raw:
        return []

    # ── Pass 2: remove dominated spans ────────────────────────────────────────
    # Span X is dominated if there exists span Y such that
    #   Y.start ≤ X.start  AND  Y.end ≥ X.end  AND  len(Y) > len(X)
    def _is_dominated(idx: int) -> bool:
        xs, xe = raw[idx][0], raw[idx][1]
        x_len = xe - xs
        for j, (ys, ye, *_) in enumerate(raw):
            if j == idx:
                continue
            if ys <= xs and ye >= xe and (ye - ys) > x_len:
                return True
        return False

    kept = [r for i, r in enumerate(raw) if not _is_dominated(i)]

    # ── Pass 3: deduplicate by source term (case-folded), keep first hit ──────
    kept.sort(key=lambda r: r[0])           # sort by start position first
    seen: set[str] = set()
    result: list[tuple[str, str, int]] = []
    for start, _end, src, tgt in kept:
        key = src.casefold()
        if key not in seen:
            seen.add(key)
            result.append((src, tgt, start))

    # Already sorted by position from the sort above.
    return result


def _build_annotation(matches: list[tuple[str, str, int]]) -> str:
    """Return the stacked [TERM] lines (no trailing newline)."""
    return "\n".join(
        f'[TERM: "{src}" \u2192 "{tgt}"]'
        for src, tgt, _pos in matches
    )


def _annotate_segments(
    segments: list[dict],
    terms: list[tuple[str, str]],
    source_key: str,
) -> list[dict]:
    """Shared implementation used by both public entry points."""
    result: list[dict] = []
    for seg in segments:
        source_text: str = seg.get(source_key, "")
        matches = _find_term_matches(source_text, terms)
        if not matches:
            result.append(dict(seg))        # unchanged, but still a fresh dict
            continue
        new_seg = dict(seg)
        new_seg[source_key] = _build_annotation(matches) + "\n" + source_text
        result.append(new_seg)
    return result
