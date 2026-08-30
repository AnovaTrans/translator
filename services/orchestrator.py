"""Streamlit-free translation engine.

`translate()` is the portal-callable core: it takes plain bytes/params and
returns a result dict — no Streamlit, no globals. It reproduces the proven
three-tier TM flow (bypass / context-draft / fresh) with opaque-placeholder
terminology enforcement, adds the TB-overrides-TM demotion graft, runs an
optional deterministic QA pass, and reports progress through a callback.
"""
from __future__ import annotations

import csv as _csv
import io as _io
import re
import time
from typing import Callable, Dict, List, Optional, Tuple

import config
from models.entities import TranslationSegment
from utils.xml_parser import XMLParser
from utils.logger import TransactionLogger
from services.tm_matcher import TMatcher
from services.prompt_builder import PromptBuilder
from services.term_injector import find_term_matches
from services import tb_placeholder
from services.providers import TranslationProvider
from services import qa as qa_module


# ---- small helpers (were module-level in the old Streamlit app) ----

def load_terms_from_csv(csv_bytes: bytes) -> List[Tuple[str, str]]:
    """Parse a termbase CSV into (source, target) pairs. Multi-encoding, with
    delimiter auto-detection (Sniffer, then a best-of ,;\\t| fallback)."""
    if not csv_bytes:
        return []
    text = None
    for enc in ("utf-8", "utf-8-sig", "utf-16", "latin-1", "cp1252"):
        try:
            text = csv_bytes.decode(enc)
            break
        except Exception:
            continue
    if not text:
        return []
    delimiter = ","
    try:
        delimiter = _csv.Sniffer().sniff(text[:8192]).delimiter
    except _csv.Error:
        best = 0
        for cand in (",", ";", "\t", "|"):
            r = _csv.reader(_io.StringIO(text), delimiter=cand)
            next(r, None)
            n = sum(1 for row in r if len(row) >= 2)
            if n > best:
                best, delimiter = n, cand
    terms: List[Tuple[str, str]] = []
    reader = _csv.reader(_io.StringIO(text), delimiter=delimiter)
    next(reader, None)  # header
    for row in reader:
        if len(row) >= 2 and row[0].strip() and row[1].strip():
            terms.append((row[0].strip(), row[1].strip()))
    return terms


def apply_tm_to_segment(source_with_tags: str, tm_translation: str) -> str:
    """Use a TM target directly, re-adding leading/trailing {{n}} tags the TM
    hit may have dropped."""
    src_tags = re.findall(r"\{\{\d+\}\}", source_with_tags)
    if not src_tags or set(src_tags) == set(re.findall(r"\{\{\d+\}\}", tm_translation)):
        return tm_translation
    result = tm_translation
    lead = re.match(r"^(\{\{\d+\}\})+", source_with_tags)
    if lead and not result.startswith(lead.group()):
        result = lead.group() + result
    trail = re.search(r"(\{\{\d+\}\})+$", source_with_tags)
    if trail and not result.endswith(trail.group()):
        result = result + trail.group()
    return result


def _noop(*_a, **_k):
    pass


def translate(
    xliff_bytes: bytes,
    *,
    provider: str,
    api_key: str,
    model: str,
    src_code: str,
    tgt_code: str,
    tmx_bytes: Optional[bytes] = None,
    csv_bytes: Optional[bytes] = None,
    dnt_terms: Optional[List[str]] = None,
    custom_template: Optional[str] = None,
    embedding_matcher=None,
    run_qa: bool = False,
    acceptance_threshold: int = config.DEFAULT_ACCEPTANCE_THRESHOLD,
    match_threshold: int = config.DEFAULT_MATCH_THRESHOLD,
    batch_size: int = config.BATCH_SIZE,
    chat_history_length: int = config.DEFAULT_CHAT_HISTORY,
    max_tokens: int = 8192,
    progress_cb: Callable = _noop,
) -> Dict:
    """Translate an XLIFF's segments. Returns a result dict with translations,
    per-tier stats, the transaction log, and (optional) QA errors."""
    job_start = time.time()
    logger = TransactionLogger()

    # 1. Parse
    segments = XMLParser.parse_xliff(xliff_bytes)
    seg_by_id = {s.id: s for s in segments}
    logger.info(f"Started job: {len(segments)} segments | {src_code}->{tgt_code} | {provider}/{model}")
    progress_cb("parse", len(segments), len(segments), f"{len(segments)} segments")

    # 2. TM + 3. TB
    tm_matcher = TMatcher(tmx_bytes, src_code, tgt_code, acceptance_threshold=acceptance_threshold) if tmx_bytes else None
    tb_terms = load_terms_from_csv(csv_bytes) if csv_bytes else []
    if tm_matcher:
        logger.info(f"TM: {tm_matcher.tu_count} TUs")
    if tb_terms:
        logger.info(f"TB: {len(tb_terms)} terms")

    # 4. Prompt builder + provider
    prompt_builder = (PromptBuilder(custom_template=custom_template) if custom_template
                      else PromptBuilder(template_path=config.PROMPT_TEMPLATE_PATH))
    translator = TranslationProvider(provider, api_key, model, max_tokens=max_tokens)

    # 5. Three-tier classification
    bypass_segs: List[TranslationSegment] = []
    context_segs: List[TranslationSegment] = []
    fresh_segs: List[TranslationSegment] = []
    final: Dict[str, str] = {}
    tm_context: Dict[str, list] = {}
    tm_drafts: Dict[str, Tuple[str, float]] = {}

    for i, seg in enumerate(segments):
        if tm_matcher:
            should_bypass, tm_text, score = tm_matcher.should_bypass_llm(seg.source, match_threshold=match_threshold)
            matches, _ = tm_matcher.extract_matches(seg.source, threshold=match_threshold)
            if should_bypass and tm_text:
                bypass_segs.append(seg)
                final[seg.id] = apply_tm_to_segment(seg.source, tm_text)
                logger.log_segment_decision(seg.id, matches, "BYPASS", score)
            elif matches and score >= match_threshold:
                context_segs.append(seg)
                tm_context[seg.id] = matches
                tm_drafts[seg.id] = (matches[0].target_text, score)
                logger.log_segment_decision(seg.id, matches, "CONTEXT", score)
            else:
                fresh_segs.append(seg)
                logger.log_segment_decision(seg.id, [], "LLM_ONLY", 0)
        else:
            fresh_segs.append(seg)
        progress_cb("analyze", i + 1, len(segments), "TM analysis")

    # 5b. TB-overrides-TM demotion — a TM-accepted segment that dropped a
    # mandatory term is sent to the LLM to be corrected (with the TM as draft).
    demoted = 0
    if tb_terms:
        for seg in list(bypass_segs):
            required = find_term_matches(seg.source, tb_terms)
            if not required:
                continue
            tgt = (final.get(seg.id) or "").lower()
            if any((tgt_term or "").lower() not in tgt for _s, tgt_term, _p in required):
                bypass_segs.remove(seg)
                context_segs.append(seg)
                tm_context.setdefault(seg.id, [])
                tm_drafts[seg.id] = (final.pop(seg.id), 100.0)
                demoted += 1
                logger.info(f"Demoted {seg.id}: TM missing a required term")
    if demoted:
        logger.info(f"TB-demotion: {demoted} segment(s) re-routed to LLM")

    llm_segments = context_segs + fresh_segs
    logger.info(f"bypass={len(bypass_segs)} context={len(context_segs)} fresh={len(fresh_segs)}")

    # 6. Batch translate
    history: List[Dict[str, str]] = []
    total_batches = (len(llm_segments) + batch_size - 1) // batch_size if llm_segments else 0
    src_name = config.SUPPORTED_LANGUAGES.get(src_code, src_code)
    tgt_name = config.SUPPORTED_LANGUAGES.get(tgt_code, tgt_code)

    for bi in range(0, len(llm_segments), batch_size):
        batch = llm_segments[bi:bi + batch_size]
        batch_num = bi // batch_size + 1
        history_context = history[-(chat_history_length * batch_size):] if chat_history_length > 0 else []
        batch_tm = {s.id: tm_context.get(s.id, []) for s in batch}
        batch_drafts = {s.id: tm_drafts[s.id] for s in batch if s.id in tm_drafts}

        # optional per-segment semantic reference
        per_seg_ref = {}
        if embedding_matcher is not None:
            try:
                md = embedding_matcher.find_similar_batch([s.source for s in batch], top_k=3, min_similarity=0.35)
                per_seg_ref = embedding_matcher.format_per_segment_references(md, [s.id for s in batch], max_chars_total=2000)
            except Exception as e:
                logger.info(f"Semantic ref error: {e}")

        # opaque [TB:n] placeholder injection
        placeholder_maps: Dict[str, dict] = {}
        batch_for_prompt = list(batch)
        if tb_terms:
            rebuilt = []
            for s in batch:
                tmatches = find_term_matches(s.source, tb_terms)
                if tmatches:
                    modified_src, pmap = tb_placeholder.inject_placeholders(s.source, tmatches)
                    placeholder_maps[s.id] = pmap
                    rebuilt.append(TranslationSegment(id=s.id, source=modified_src, target=s.target))
                else:
                    rebuilt.append(s)
            batch_for_prompt = rebuilt
        all_placeholders = {}
        for pm in placeholder_maps.values():
            all_placeholders.update(pm)

        prompt = prompt_builder.build_prompt(
            src_name, tgt_name, batch_for_prompt, batch_tm, {},
            chat_history=history_context,
            per_segment_reference=per_seg_ref or None,
            dnt_terms=dnt_terms or None,
            tm_drafts=batch_drafts or None,
            tb_terms=tb_terms or None,
            placeholder_map=all_placeholders or None,
        )

        try:
            text, _tokens = translator.translate_batch(prompt)
            for line in text.strip().split("\n"):
                if line.startswith("[") and "]" in line:
                    seg_id = line[line.find("[") + 1:line.find("]")]
                    trans = line[line.find("]") + 1:].strip()
                    if seg_id in placeholder_maps:
                        trans = tb_placeholder.restore_placeholders(trans, placeholder_maps[seg_id])
                        tb_placeholder.check_tb_compliance(trans, placeholder_maps[seg_id], seg_id, logger)
                    if seg_id in seg_by_id:
                        final[seg_id] = trans
                        history.append({"source": seg_by_id[seg_id].source, "target": trans})
        except Exception as e:
            logger.info(f"ERROR batch {batch_num}: {e}")
        progress_cb("translate", min(bi + batch_size, len(llm_segments)), len(llm_segments), f"batch {batch_num}/{total_batches}")

    # 7. Optional QA
    qa_errors = []
    if run_qa:
        qa_errors = qa_module.run_qa(segments, final, tb_terms)
        logger.info(f"QA: {len(qa_errors)} issue(s)")

    elapsed = time.time() - job_start
    logger.info(f"Done in {elapsed:.0f}s | {len(final)} translations")

    return {
        "translations": final,
        "segments": segments,
        "stats": {
            "total": len(segments),
            "bypass": len(bypass_segs),
            "context": len(context_segs),
            "fresh": len(fresh_segs),
            "demoted": demoted,
            "translated": len(final),
            "elapsed_sec": elapsed,
            "batches": total_batches,
        },
        "qa_errors": qa_errors,
        "log": logger.get_content(),
    }
