# Anova Translator — standalone TMX + TB(CSV) AI translator

**Date:** 2026-08-30 · **Status:** Approved (brainstorming)

## Goal

Replace the memoQ-coupled `ai-with-memoq.streamlit.app` with a **standalone**
Streamlit AI translator whose only reference inputs are a **TMX** (translation
memory) and a **termbase exported as CSV** — no live memoQ server. Built
**portal-ready**: the engine is a Streamlit-free service so the Anova Oltrans
platform can call it, with hooks for portal-managed API keys/credits, preloaded
TMs, and TM write-back.

## Platform context (from "Anova Oltrans AI Translation Platform.docx")

The Oltrans platform unifies: Document Prep (OCR → XLIFF) → Glossary/Style Guide
(portal) → **AI Translation + TM (this module)** → Verifika QA → human post-edit
→ finalize to DOCX. **XLIFF is the platform's canonical interchange format.**
Known pain points this build must respect: formatting (bold/italic/underline)
lost to plain-text tags; untranslated/copied-source segments; number
inconsistency **and** false number-format errors; "must paste the API key every
time" (portal should inject it).

## Decisions (locked)

- **I/O:** all formats. Non-XLIFF (DOCX/PPTX/XLSX/TXT/HTML) are converted **to
  XLIFF internally**, translated, then **optionally converted back** to the
  original format. XLIFF is the internal canonical format.
- **Providers:** Claude **and** OpenAI (user picks), live model dropdown per
  provider; `_response_text` block-safe Claude parsing; current model IDs only.
- **Terminology enforcement:** BOTH — opaque-placeholder (`[TB:n]`, term never
  shown to the model, Turkish-morphology suffixing on restore) **and**
  TB-overrides-TM demotion (re-translate a TM-bypassed segment that dropped a
  mandatory term).
- **Semantic style reference:** keep (OpenAI `text-embedding-3-small`; optional —
  skipped when no embedding key).
- **QA pass:** optional toggle — deterministic checks (tag/placeholder integrity,
  number consistency **format-aware**, untranslated/copied-source, required-TB
  present). Verifika = v2.

## Base + grafts

- **Base:** the `Translator` repo (already standalone, Claude wired, unique
  opaque-placeholder + Turkish morphology). Its `services/`, `models/`, `utils/`
  tree is kept to avoid import churn.
- **Graft from `AI-with-memoQ`:** the richer `prompt_builder` (per-segment inline
  TM+TB injection, mandatory-terminology block with precise morphological rules)
  and the TB-demotion loop.
- **Drop:** all memoQ code, plaintext `api_keys.json`, monolithic `app.py`
  (rewritten thin).

## Structure

```
app.py                 # thin Streamlit UI
model_utils.py         # live dual-provider model dropdown
services/
  providers.py         # NEW dual Claude+OpenAI (was ai_translator); _response_text; current IDs
  orchestrator.py      # NEW Streamlit-free translate() — the callable engine core
  tm_matcher.py        # local TMX fuzzy (rapidfuzz), 3-tier bypass/context/fresh, multi-encoding (UTF-16)
  tb_matcher.py        # local CSV termbase, memoQ-export column auto-detect
  term_injector.py     # term detection (word-boundary, longest-match)
  tb_placeholder.py    # opaque [TB:n] + Turkish morphology suffixing  ← crown jewel
  prompt_builder.py    # grafted richer builder
  embedding_matcher.py # optional OpenAI style reference
  qa.py                # NEW optional deterministic QA
  caching.py, language_codes.py, doc_analyzer.py
models/entities.py     # TMMatch, TermMatch, TranslationSegment, QAError, TranslationResult
utils/xml_parser.py    # XLIFF + TMX parse, {{n}} inline-tag round-trip
io/documents.py        # NEW any-format <-> XLIFF (DOCX/PPTX/XLSX/TXT/HTML)
```

## Pipeline (Streamlit-free `orchestrator.translate`)

parse source → XLIFF segments → per-segment triage (tag-only bypass / TM≥95
bypass / TM 70–94 context-draft / fresh) → TB match + opaque-placeholder inject →
batch translate (Claude|OpenAI) with per-segment TM+TB + chat-history +
optional style ref → restore placeholders (inflected) → TB-demotion re-translate
→ optional QA → reassemble XLIFF → optional convert back to original format +
transaction log.

## Portal-readiness hooks

- API key resolved from `env`/portal first, sidebar fallback (never forced).
- `orchestrator.translate(...)` takes plain args (no Streamlit), returns a
  `TranslationResult` — callable by the portal.
- `on_approved_segments` write-back hook (no-op in standalone) for future
  TM-update-on-approval.
- Preloaded-TM selection anticipated (v1 = upload; interface allows a provided
  TM path/bytes).

## Deferred (v2)

Verifika QA integration; LLM self-review second pass; preloaded/managed TM store;
automatic TM write-back UI; PPTX/XLSX high-fidelity formatting round-trip
(v1 does best-effort).

## Verification strategy

Offline unit tests per module (TMX/CSV parse incl. UTF-16, term placeholder +
Turkish morphology, QA checks, documents↔XLIFF round-trip) + Streamlit AppTest
render. End-to-end translation needs an API key (user-run).
