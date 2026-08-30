"""
services/tb_placeholder.py
───────────────────────────
Termbase placeholder injection and restoration.

Replaces matched source terms with opaque [TB:N] placeholders before the LLM
sees them, then restores the correct target terms after translation. This
guarantees terminology compliance because the model never sees the source term
and therefore cannot mistranslate it.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


def inject_placeholders(
    source_text: str,
    term_matches: list[tuple[str, str, int]],
) -> tuple[str, dict[str, str]]:
    """
    Replace matched source terms with [TB:N] placeholders.

    Args:
        source_text:  Original segment source text.
        term_matches: Output of ``term_injector.find_term_matches`` —
                      ``[(source_term, target_term, first_position), …]``.

    Returns:
        (modified_source, placeholder_map)
        - modified_source: source_text with source terms replaced by placeholders
        - placeholder_map: ``{placeholder_string: target_term}``
    """
    if not term_matches:
        return source_text, {}

    # Sort longest-first to avoid partial overlaps during replacement
    sorted_matches = sorted(term_matches, key=lambda m: len(m[0]), reverse=True)

    placeholder_map: dict[str, str] = {}
    modified = source_text

    for idx, (src_term, tgt_term, _pos) in enumerate(sorted_matches):
        placeholder = f"[TB:{idx}]"
        # Case-insensitive, word-boundary-safe replacement (first occurrence only)
        pattern = re.compile(
            r"(?<!\w)" + re.escape(src_term) + r"(?!\w)",
            re.IGNORECASE,
        )
        new_text = pattern.sub(placeholder, modified, count=1)
        if new_text != modified:
            placeholder_map[placeholder] = tgt_term
            modified = new_text

    return modified, placeholder_map


def restore_placeholders(
    translation: str,
    placeholder_map: dict[str, str],
) -> str:
    """
    Replace [TB:N] placeholders in the translation with the correct target terms.

    Handles two Turkish-specific issues:
    1. Apostrophe + suffix after placeholder (e.g. ``[TB:0]'ı``) — the suffix is
       recalculated using vowel harmony against the real target term.
    2. Sentence-start capitalisation — if the restored term lands at position 0 or
       after sentence-ending punctuation, the first letter is capitalised
       (Turkish-aware: ``i`` → ``İ``).
    """
    result = translation
    for placeholder, target_term in placeholder_map.items():
        # Pattern: [TB:N]'suffix  (apostrophe + Turkish suffix)
        suffix_pattern = re.compile(
            re.escape(placeholder) + r"'([a-züçşğıioöu]+)",
            re.IGNORECASE,
        )
        match = suffix_pattern.search(result)
        if match:
            raw_suffix = match.group(1)
            correct_suffix = _recalculate_turkish_suffix(target_term, raw_suffix)
            restored = target_term + correct_suffix
            start_pos = match.start()
            if _is_sentence_start(result, start_pos):
                restored = _tr_capitalize_first(restored)
            result = result[:match.start()] + restored + result[match.end():]
        elif placeholder in result:
            pos = result.index(placeholder)
            restored = target_term
            if _is_sentence_start(result, pos):
                restored = _tr_capitalize_first(restored)
            result = result.replace(placeholder, restored, 1)
        else:
            logger.warning(
                "Placeholder %s not found in LLM output; "
                "expected target term: %s",
                placeholder,
                target_term,
            )
    return result


# ── Turkish suffix helpers ───────────────────────────────────────────────────

_VOWELS = set("aeıioöuü")

_VOWEL_HARMONY_4: dict[str, str] = {
    "a": "ı", "ı": "ı", "o": "u", "u": "u",
    "e": "i", "i": "i", "ö": "ü", "ü": "ü",
}

_VOWEL_HARMONY_2: dict[str, str] = {
    "a": "a", "ı": "a", "o": "a", "u": "a",
    "e": "e", "i": "e", "ö": "e", "ü": "e",
}

# Raw suffix → grammatical case mapping
_CASE_MAP: dict[str, str] = {}
for _s in ("ı", "i", "u", "ü", "yı", "yi", "yu", "yü",
           "nı", "ni", "nu", "nü", "ını", "ini", "unu", "ünü"):
    _CASE_MAP[_s] = "accusative"
for _s in ("a", "e", "ya", "ye", "na", "ne", "ına", "ine"):
    _CASE_MAP[_s] = "dative"
for _s in ("ın", "in", "un", "ün", "nın", "nin", "nun", "nün"):
    _CASE_MAP[_s] = "genitive"
for _s in ("da", "de", "nda", "nde"):
    _CASE_MAP[_s] = "locative"
for _s in ("dan", "den", "ndan", "nden"):
    _CASE_MAP[_s] = "ablative"
for _s in ("dır", "dir", "dur", "dür", "tır", "tir", "tur", "tür"):
    _CASE_MAP[_s] = "copula"
for _s in ("yla", "yle", "la", "le"):
    _CASE_MAP[_s] = "instrumental"


def _find_last_vowel(term: str) -> str:
    """Return the last vowel in *term* (lowercase), or 'e' as fallback."""
    for ch in reversed(term.lower()):
        if ch in _VOWELS:
            return ch
    return "e"


def _identify_case(raw_suffix: str) -> str | None:
    """Map a raw LLM suffix to a grammatical case name, or None."""
    return _CASE_MAP.get(raw_suffix.lower())


def _recalculate_turkish_suffix(term: str, raw_suffix: str) -> str:
    """Compute the correct Turkish suffix for *term* given the LLM's *raw_suffix*."""
    if not term:
        return raw_suffix

    last_char = term[-1].lower()
    last_vowel = _find_last_vowel(term)
    case = _identify_case(raw_suffix)

    is_vowel_ending = last_char in _VOWELS
    is_possessive = last_char in ("ı", "i", "u", "ü")

    if case == "accusative":
        v = _VOWEL_HARMONY_4[last_vowel]
        if is_possessive:
            return "n" + v
        elif is_vowel_ending:
            return "y" + v
        else:
            return v

    elif case == "dative":
        v = _VOWEL_HARMONY_2[last_vowel]
        if is_possessive:
            return "n" + v
        elif is_vowel_ending:
            return "y" + v
        else:
            return v

    elif case == "genitive":
        v = _VOWEL_HARMONY_4[last_vowel]
        if is_vowel_ending:
            return "n" + v + "n"
        else:
            return v + "n"

    elif case == "locative":
        v = _VOWEL_HARMONY_2[last_vowel]
        if is_possessive:
            return "nd" + v
        else:
            return "d" + v

    elif case == "ablative":
        v = _VOWEL_HARMONY_2[last_vowel]
        if is_possessive:
            return "nd" + v + "n"
        else:
            return "d" + v + "n"

    elif case == "copula":
        v = _VOWEL_HARMONY_4[last_vowel]
        return "d" + v + "r"

    elif case == "instrumental":
        v = _VOWEL_HARMONY_2[last_vowel]
        if is_vowel_ending:
            return "yl" + v
        else:
            return "l" + v

    # Fallback — return raw suffix without apostrophe
    return raw_suffix


# ── Sentence-start helpers ───────────────────────────────────────────────────

def _is_sentence_start(text: str, pos: int) -> bool:
    """Return True if *pos* is at the start of a sentence in *text*."""
    if pos == 0:
        return True
    before = text[:pos].rstrip()
    if not before:
        return True
    return before[-1] in ".!?"


def _tr_capitalize_first(s: str) -> str:
    """Capitalize the first letter of *s* with Turkish-aware rules (i → İ)."""
    if not s:
        return s
    first = s[0]
    if first == "i":
        return "İ" + s[1:]
    return first.upper() + s[1:]


def check_tb_compliance(
    translation: str,
    placeholder_map: dict[str, str],
    segment_id: str,
    log,
) -> None:
    """
    Verify that every expected target term appears in the final translation.

    Logs one line per term at INFO (present) or WARNING (missing) level.

    Args:
        translation:    The restored translation string.
        placeholder_map: ``{placeholder: target_term}`` from inject_placeholders.
        segment_id:     Segment ID for log context.
        log:            Logger instance (standard Python logger or compatible).
    """
    terms = list(placeholder_map.items())
    log.info(f"TB Compliance \u2014 seg={segment_id}: {len(terms)} terms checked")

    translation_lower = translation.lower()
    for placeholder, target_term in terms:
        if target_term.lower() in translation_lower:
            log.info(
                f'  TB \u2713  seg={segment_id}  target="{target_term}"'
            )
        else:
            log.warning(
                f'  TB \u2717  seg={segment_id}  target="{target_term}"'
                f"  [placeholder {placeholder} not found in LLM output]"
            )
