from typing import List, Dict, Optional, Tuple
from models.entities import TranslationSegment, TMMatch, TermMatch
from services.term_injector import find_term_matches
import os


class PromptBuilder:
    """
    Builds prompts for translation with support for:
    - Custom prompt templates
    - Chat history (previous translations as context)
    - TM and TB context
    - Draft correction mode: segments with sufficient TM match get a DRAFT to
      correct instead of being translated from scratch.
    """

    _CONTEXT_NOTE = (
        "DOCUMENT CONTEXT NOTE: The segments below are consecutive excerpts from a single "
        "source document. Translate them as a coherent unit \u2014 not as independent sentences. "
        "Preserve consistency of terminology, tone, and register across all segments. "
        "References to entities, pronouns, or concepts introduced in one segment carry over "
        "to subsequent segments in the same batch."
    )

    def __init__(self, template_path: Optional[str] = None, custom_template: Optional[str] = None):
        self.template = ""
        if custom_template:
            self.template = custom_template
        elif template_path and os.path.exists(template_path):
            with open(template_path, 'r', encoding='utf-8') as f:
                self.template = f.read()
        else:
            self.template = self._get_default_template()

    def set_custom_template(self, template_content: str):
        """Set a custom template from string content"""
        self.template = template_content

    def build_prompt(self,
                     source_lang: str,
                     target_lang: str,
                     segments: List[TranslationSegment],
                     tm_context: Dict[str, List[TMMatch]],
                     tb_context: Dict[str, List[TermMatch]],
                     chat_history: Optional[List[Dict[str, str]]] = None,
                     reference_context: Optional[str] = None,
                     per_segment_reference: Optional[Dict[str, str]] = None,
                     dnt_terms: Optional[List[str]] = None,
                     tm_drafts: Optional[Dict[str, Tuple[str, float]]] = None,
                     tb_terms: Optional[List[Tuple[str, str]]] = None,
                     placeholder_map: Optional[Dict[str, str]] = None) -> str:
        """
        Build the translation prompt.

        Args:
            source_lang: Source language name
            target_lang: Target language name
            segments: List of segments to translate
            tm_context: TM matches for each segment (reference examples)
            tb_context: TB matches for each segment
            chat_history: [{source, target}, ...] previous translations
            reference_context: Target language style reference text
            dnt_terms: Terms that must NOT be translated
            tm_drafts: {seg_id: (draft_translation, score_pct)} for segments
                       that have a sufficient TM match. LLM must CORRECT the
                       draft rather than translate from scratch.
            tb_terms: Pre-loaded (source, target) term pairs from TBMatcher.
                      When provided, inline [TERM: "X" → "Y"] annotations are
                      prepended to each segment that contains a match, forcing
                      the model to apply the approved term in that segment.
            per_segment_reference: {seg_id: formatted_reference_string} — when
                      provided, each segment gets its own [REFERENCE] block in
                      the prompt. Takes precedence over reference_context.
        """
        tm_drafts = tm_drafts or {}

        # ── Pre-compute per-segment [TERM] prefix lines ───────────────────────
        # Run once over all segments so the block-building loop below can look
        # up the prefix string in O(1).  Empty string means "no annotation".
        _term_prefix: Dict[str, str] = {}
        if tb_terms:
            for seg in segments:
                matches = find_term_matches(seg.source, tb_terms)
                if matches:
                    _term_prefix[seg.id] = "\n".join(
                        f'[TERM: "{src}" \u2192 "{tgt}"]'
                        for src, tgt, _pos in matches
                    )

        # Split segments into two groups
        draft_segs = [s for s in segments if s.id in tm_drafts]
        fresh_segs = [s for s in segments if s.id not in tm_drafts]

        # ── 0. Style reference ────────────────────────────────────────────
        # per_segment_reference takes precedence; skip batch-level block when present
        reference_text = ""
        if reference_context and not per_segment_reference:
            reference_text = (
                f"STYLE REFERENCE (use these {target_lang} samples as style/tone guide):\n"
                + reference_context + "\n\n"
            )

        # ── 0.5 DNT list ─────────────────────────────────────────────────
        dnt_text = ""
        if dnt_terms:
            dnt_text = "DO NOT TRANSLATE (keep these terms in original form):\n"
            for term in dnt_terms[:50]:
                dnt_text += f"- {term}\n"
            dnt_text += "\n"

        # ── 0.6 TB placeholder instruction ──────────────────────────────
        tb_placeholder_text = ""
        if placeholder_map:
            tb_placeholder_text = (
                "TB PLACEHOLDER INSTRUCTION: Tokens of the form [TB:N] are "
                "pre-translated terminology placeholders. They must be copied "
                "exactly as-is into the output at the appropriate position. "
                "They must never be translated, omitted, split, or altered "
                "in any way.\n\n"
            )

        # ── 1. Chat history ───────────────────────────────────────────────
        history_text = ""
        if chat_history:
            history_text = "PREVIOUS TRANSLATIONS (for consistency):\n"
            for item in chat_history:
                history_text += f"{item['source']} -> {item['target']}\n"
            history_text += "\n"

        # ── 2. TM reference examples ──────────────────────────────────────
        examples_text = ""
        unique_tm: Dict[str, TMMatch] = {}
        for seg in segments:
            for match in tm_context.get(seg.id, []):
                key = f"{match.source_text}->{match.target_text}"
                unique_tm[key] = match

        if unique_tm:
            examples_text = "TRANSLATION MEMORY CONTEXT:\n"
            for m in sorted(unique_tm.values(), key=lambda x: x.similarity, reverse=True)[:15]:
                examples_text += f"{m.source_text} -> {m.target_text} [{m.match_type} {m.similarity:.0f}%]\n"
        else:
            examples_text = "No Translation Memory matches available."

        # ── 3. TB terminology ─────────────────────────────────────────────
        terms_text = ""
        unique_tb: Dict[str, str] = {}
        for seg in segments:
            for match in tb_context.get(seg.id, []):
                unique_tb[match.source] = match.target

        if unique_tb:
            terms_text = "REQUIRED TERMINOLOGY:\n"
            for src, tgt in unique_tb.items():
                terms_text += f"- {src} = {tgt}\n"
        else:
            terms_text = "No specific terminology constraints."

        # ── 3.5 Context note (prepend to seg_text for fallback paths) ────────
        # Will be injected via %CONTEXT_NOTE% if the template has that placeholder;
        # otherwise prepended to seg_text so it lands before the segment list.
        context_note_text = self._CONTEXT_NOTE

        # ── 4. Build segment blocks ───────────────────────────────────────
        draft_block = ""
        if draft_segs:
            draft_block = (
                "DRAFT CORRECTIONS "
                "(improve these existing translations — do NOT retranslate from scratch):\n"
            )
            for seg in draft_segs:
                draft_text, score = tm_drafts[seg.id]
                prefix = _term_prefix.get(seg.id, "")
                if prefix:
                    draft_block += prefix + "\n"
                if per_segment_reference and seg.id in per_segment_reference:
                    draft_block += f"[REFERENCE]\n{per_segment_reference[seg.id]}\n[/REFERENCE]\n"
                draft_block += f"[{seg.id}] SOURCE: {seg.source}\n"
                draft_block += f"    TM DRAFT ({score:.0f}%): {draft_text}\n"
            draft_block += "\n"

        fresh_block = ""
        if fresh_segs:
            if draft_segs:
                fresh_block = "FRESH TRANSLATIONS (translate from scratch):\n"
            for seg in fresh_segs:
                prefix = _term_prefix.get(seg.id, "")
                if prefix:
                    fresh_block += prefix + "\n"
                if per_segment_reference and seg.id in per_segment_reference:
                    fresh_block += f"[REFERENCE]\n{per_segment_reference[seg.id]}\n[/REFERENCE]\n"
                fresh_block += f"[{seg.id}] {seg.source}\n"

        seg_text = draft_block + fresh_block

        # ── 5. Assemble from template ─────────────────────────────────────
        prompt = self.template
        prompt = prompt.replace("%SOURCELANG%", source_lang)
        prompt = prompt.replace("%TARGETLANG%", target_lang)
        prompt = prompt.replace("%EXAMPLES%", examples_text)
        prompt = prompt.replace("%TERMS%", terms_text)

        if "%FORBIDDENTERMS%" in prompt:
            prompt = prompt.replace("%FORBIDDENTERMS%", dnt_text if dnt_text else "")
        elif dnt_text:
            prompt = prompt.replace(terms_text, terms_text + "\n" + dnt_text)

        if "%TB_PLACEHOLDER_NOTE%" in prompt:
            prompt = prompt.replace("%TB_PLACEHOLDER_NOTE%", tb_placeholder_text if tb_placeholder_text else "")
        elif tb_placeholder_text:
            # Insert after DNT block or after terms block
            if dnt_text and dnt_text in prompt:
                prompt = prompt.replace(dnt_text, dnt_text + tb_placeholder_text)
            elif terms_text in prompt:
                prompt = prompt.replace(terms_text, terms_text + "\n" + tb_placeholder_text)

        if reference_text:
            if "%EXAMPLES%" in self.template:
                prompt = prompt.replace(examples_text, reference_text + examples_text)
            else:
                first_nl = prompt.find('\n\n')
                if first_nl > 0:
                    prompt = prompt[:first_nl] + "\n\n" + reference_text + prompt[first_nl:]

        if history_text:
            current_examples = (reference_text + examples_text) if reference_text else examples_text
            prompt = prompt.replace(current_examples, history_text + current_examples)

        # ── Context note injection ────────────────────────────────────────
        if "%CONTEXT_NOTE%" in prompt:
            prompt = prompt.replace("%CONTEXT_NOTE%", context_note_text)
        elif "SEGMENTS TO TRANSLATE:" in prompt:
            prompt = prompt.replace(
                "SEGMENTS TO TRANSLATE:",
                context_note_text + "\nSEGMENTS TO TRANSLATE:"
            )
        else:
            # Will be appended with seg_text below; prepend to seg_text
            seg_text = context_note_text + "\n" + seg_text

        if "%SEGMENTS%" in prompt:
            if "SEGMENTS TO TRANSLATE:" in self.template:
                prompt = prompt.replace("%SEGMENTS%", seg_text)
            else:
                prompt = prompt.replace("%SEGMENTS%", "SEGMENTS TO TRANSLATE:\n" + seg_text)
        else:
            prompt = prompt.rstrip() + "\n\nSEGMENTS TO TRANSLATE:\n" + seg_text

        if "OUTPUT FORMAT:" not in prompt and "[ID]" not in prompt:
            prompt += "\nOUTPUT FORMAT:\n[ID] Translated text\n"

        return prompt

    def _get_default_template(self):
        return """You are a professional translator translating from %SOURCELANG% to %TARGETLANG%.
Your task is to translate the segments provided ensuring high accuracy and natural flow.

%EXAMPLES%

Apply terminology consistently. Use the approved terms listed below (if any).
Some segments begin with one or more [TERM: "X" \u2192 "Y"] annotations. When present, you MUST use "Y" as the translation of "X" in that segment \u2014 this overrides any other consideration.
%TERMS%

%TB_PLACEHOLDER_NOTE%
INSTRUCTIONS:
1. Preserve all tags (e.g., {{1}}, {{2}}) exactly as they appear in source.
2. Use the provided Terminology and TM context where applicable.
3. For DRAFT CORRECTIONS: correct the provided draft to match the source exactly. Do not retranslate from scratch.
4. Output format must be strictly: [ID] Translated Text

%CONTEXT_NOTE%
%SEGMENTS%
"""
