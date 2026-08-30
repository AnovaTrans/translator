"""Optional deterministic QA pass.

Backstop checks a pure LLM won't reliably self-catch, tuned against the
platform's reported pain points — especially "false number errors" (values
correct, only the thousands/decimal FORMAT differs). Number comparison is
therefore format-agnostic: separators are stripped before comparing, so
1,000 vs 1.000 vs 1 000 never flags.
"""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

from models.entities import QAError
from services.term_injector import find_term_matches

_TAG = re.compile(r"\{\{\d+\}\}")
_NUM = re.compile(r"\d[\d.,\s ]*\d|\d")
_HAS_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)


def _tags(text: str):
    return sorted(_TAG.findall(text or ""))


_PLACEHOLDER = re.compile(r"\{\{\d+\}\}|\[TB:\d+\]")


def _numbers(text: str):
    """Digit-sequences with separators (.,space) stripped, so only the actual
    digits are compared — format differences are ignored on purpose. Tag and
    [TB:n] placeholders are removed first so their indices aren't read as
    numbers."""
    text = _PLACEHOLDER.sub(" ", text or "")
    out = []
    for m in _NUM.findall(text):
        digits = re.sub(r"[.,\s ]", "", m)
        if digits:
            out.append(digits)
    return sorted(out)


def run_qa(segments, translations: Dict[str, str], tb_terms: List[Tuple[str, str]] = None) -> List[QAError]:
    """Return a list of QAError for the translated segments."""
    tb_terms = tb_terms or []
    errors: List[QAError] = []

    for seg in segments:
        target = translations.get(seg.id)
        if target is None:
            continue
        source = seg.source or ""

        # 1. Tag/placeholder integrity — no {{n}} dropped or invented.
        if _tags(source) != _tags(target):
            errors.append(QAError(code=4001, segment_id=seg.id, severity="error",
                                  description="Tag/placeholder mismatch (a {{n}} tag is missing or extra)",
                                  original_target=target))

        # 2. Numbers — format-agnostic; flags only genuine digit drops/additions.
        if _numbers(source) != _numbers(target):
            errors.append(QAError(code=4002, segment_id=seg.id, severity="warning",
                                  description="Number mismatch (a numeric value differs, ignoring format)",
                                  original_target=target))

        # 3. Untranslated / copied source (skip trivial, numeric-only, tag-only).
        s_stripped, t_stripped = source.strip(), target.strip()
        if (s_stripped and s_stripped == t_stripped
                and _HAS_LETTER.search(s_stripped)
                and len(s_stripped.split()) >= 2):
            errors.append(QAError(code=4003, segment_id=seg.id, severity="warning",
                                  description="Target is identical to source (possibly untranslated)",
                                  original_target=target))

        # 4. Required termbase term present in the target (backstop; placeholder
        #    injection already enforces this for matched segments).
        if tb_terms:
            low = target.lower()
            for src_term, tgt_term, _pos in find_term_matches(source, tb_terms):
                if (tgt_term or "").lower() not in low:
                    errors.append(QAError(code=4004, segment_id=seg.id, severity="warning",
                                          description=f'Required term "{src_term}" -> "{tgt_term}" not found in target',
                                          original_target=target))

    return errors
