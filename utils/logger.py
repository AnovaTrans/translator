import datetime
from typing import List, Dict, Optional
from models.entities import TranslationSegment, TMMatch, TermMatch


class TransactionLogger:
    def __init__(self):
        self.entries = []

    def _time(self):
        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _add(self, message: str):
        self.entries.append(f"{self._time()} | {message}")

    def info(self, message: str):
        """Logs a general info message"""
        self._add(message)

    def log_segment_decision(self, seg_id: str, matches: List[TMMatch], decision: str, score: float):
        """
        Logs TM lookup result and routing decision for one segment.
        decision: 'BYPASS' | 'CONTEXT' | 'LLM_ONLY'
        """
        if matches:
            matches_str = ", ".join(
                (
                    f"TMMatch('{m.source_text[:40]}...' \u2192 '{m.target_text[:40]}...' [{m.match_type} {m.similarity:.0f}%])"
                    if len(m.source_text) > 40 else
                    f"TMMatch('{m.source_text}' \u2192 '{m.target_text}' [{m.match_type} {m.similarity:.0f}%])"
                )
                for m in matches[:5]
            )
            self._add(f"TM lookup for {seg_id}: [{matches_str}]")
            self._add(f"[{seg_id}] TM match score: {score:.0f}%")
        else:
            self._add(f"TM lookup for {seg_id}: (no match)")

        if decision == "BYPASS":
            self._add(f"[{seg_id}] BYPASS ({score:.0f}% TM match)")
        elif decision == "CONTEXT":
            self._add(f"[{seg_id}] CONTEXT ({score:.0f}% TM fuzzy match)")
        else:
            self._add(f"[{seg_id}] No TM match - sending to LLM")

    def log_batch_header(self, batch_num: int, seg_count: int, history_count: int = 0):
        """Logs the start of an LLM batch."""
        self._add(f"Batch {batch_num}: {seg_count} segments to LLM")
        if history_count > 0:
            self._add(f"Chat history: {history_count} previous translations included")

    def log_llm_result(self, batch_num: int, prompt_len: int, response_len: int,
                       parsed_results: Dict[str, str]):
        """Logs LLM prompt/response stats and parsed translations."""
        self._add(f"LLM Prompt length: {prompt_len} chars")
        self._add(f"LLM Response length: {response_len} chars")
        if parsed_results:
            self._add(f"Parsed Response (Batch {batch_num}):")
            for seg_id, trans in parsed_results.items():
                preview = trans[:80] + "..." if len(trans) > 80 else trans
                self.entries.append(f"{self._time()} |   [{seg_id}] {preview}")

    def log_summary(self, total: int, bypass: int, context: int, llm_only: int,
                    elapsed_sec: float, batch_size: int, total_batches: int,
                    acceptance_threshold: int, match_threshold: int):
        """Logs the final job summary block."""
        sep = "=" * 80
        self.entries.append(sep)
        self._add("TRANSLATION JOB SUMMARY")
        self._add(sep)
        self._add(f"Total Segments: {total}")
        bypass_pct = (bypass / total * 100) if total else 0
        context_pct = (context / total * 100) if total else 0
        llm_pct = (llm_only / total * 100) if total else 0
        self._add(f"\u2713 Bypass (\u2265{acceptance_threshold}%): {bypass} ({bypass_pct:.1f}%)")
        self._add(f"\u2713 With TM Context ({match_threshold}-{acceptance_threshold - 1}%): {context} ({context_pct:.1f}%)")
        self._add(f"\u2713 LLM Only (<{match_threshold}%): {llm_only} ({llm_pct:.1f}%)")
        self._add(f"Processing Time: {elapsed_sec:.1f} seconds")
        self._add(f"Batch Size: {batch_size} segments")
        self._add(f"Total Batches: {total_batches}")
        self._add(sep)

    # ── Legacy methods kept for backward compatibility ─────────────────────

    def log_batch_start(self, batch_num: int, segments: List[TranslationSegment]):
        """Legacy wrapper – prefer log_batch_header."""
        self.log_batch_header(batch_num, len(segments))

    def log_tm_matches(self, tm_data: Dict[str, List[TMMatch]]):
        """Legacy: aggregate TM match summary after analysis phase."""
        count = sum(len(m) for m in tm_data.values())
        if count == 0:
            self._add("TM Context: No matches found.")
        else:
            self._add(f"TM Context ({count} matches total)")

    def log_tb_matches(self, tb_data: Dict[str, List[TermMatch]]):
        """Legacy: aggregate TB match summary."""
        count = sum(len(m) for m in tb_data.values())
        if count == 0:
            self._add("TB Context: No matches found.")
        else:
            self._add(f"TB Context ({count} term matches total)")

    def log_llm_interaction(self, prompt: str, response: str):
        """Legacy: replaced by log_llm_result (suppressed to keep log clean)."""
        pass

    def get_content(self) -> str:
        """Returns the full log as a string"""
        return "\n".join(self.entries)
