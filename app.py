"""Anova Translator — standalone TMX + TB(CSV) AI translation (Streamlit UI).

Thin UI over services/orchestrator.translate(). API keys resolve from the
environment / st.secrets first (portal-managed credits) and fall back to a
sidebar field. Two tabs: Workspace (translate) and Results (edit + download).
"""
import os
import re

import pandas as pd
import streamlit as st

import config
import model_utils
from anova_brand_theme import apply_anova_theme, anova_header, anova_footer, anova_sidebar_logo
from utils.xml_parser import XMLParser
from services.orchestrator import translate
from services import documents
from services.embedding_matcher import EmbeddingMatcher

st.set_page_config(page_title="Anova Translator", page_icon="🌐",
                   layout="wide", initial_sidebar_state="expanded")
apply_anova_theme()


@st.cache_data(show_spinner=False)
def _cached_models(provider: str, api_key: str):
    return model_utils.list_models(provider, api_key)


def _secret(name: str) -> str:
    try:
        return st.secrets.get(name, "")  # type: ignore[attr-defined]
    except Exception:
        return ""


def _resolve_key(provider: str) -> str:
    if provider == "Claude":
        return os.getenv("ANTHROPIC_API_KEY", "") or _secret("anthropic_api_key") or _secret("ANTHROPIC_API_KEY")
    return os.getenv("OPENAI_API_KEY", "") or _secret("openai_api_key") or _secret("OPENAI_API_KEY")


def _openai_key() -> str:
    return os.getenv("OPENAI_API_KEY", "") or _secret("openai_api_key") or _secret("OPENAI_API_KEY")


def _detect_langs(xliff_bytes: bytes):
    head = xliff_bytes[:6000].decode("utf-8", errors="ignore")
    s = re.search(r'source-language="([^"]+)"', head)
    t = re.search(r'target-language="([^"]+)"', head)
    norm = lambda c: (c or "").split("-")[0].lower()
    return (norm(s.group(1)) if s else "en", norm(t.group(1)) if t else "tr")


def _read_dnt(f):
    try:
        lines = [ln.split(",")[0].strip() for ln in f.getvalue().decode("utf-8", "ignore").splitlines() if ln.strip()]
        return lines[1:] or None
    except Exception:
        return None


# ---------------- Sidebar ----------------
with st.sidebar:
    anova_sidebar_logo()
    st.subheader("⚙️ Provider & Model")
    provider = st.radio("AI Provider", ["Claude", "OpenAI"], horizontal=True)
    env_key = _resolve_key(provider)
    typed = st.text_input(f"{provider} API Key", type="password", value="",
                          help="Left empty, the key is read from the environment / secrets.").strip()
    api_key = typed or env_key
    if env_key and not typed:
        st.caption("🔑 Using the key from environment/secrets.")
    live = _cached_models(provider, api_key)
    default_id = model_utils.default_model(provider, live)
    idx = live.index(default_id) if default_id in live else 0
    model = st.selectbox("Model", live, index=idx, format_func=model_utils.display_name)

    st.subheader("🧠 Translation Memory")
    acceptance = st.slider("TM accept ≥ (%) — use TM directly", 50, 100, config.DEFAULT_ACCEPTANCE_THRESHOLD)
    match = st.slider("TM match ≥ (%) — send as context", 0, 100, config.DEFAULT_MATCH_THRESHOLD)

    st.subheader("🔧 Advanced")
    batch = st.slider("Batch size", 5, 50, config.BATCH_SIZE)
    hist = st.slider("Chat-history batches", 0, 10, config.DEFAULT_CHAT_HISTORY)
    run_qa = st.checkbox("Run QA after translation", value=True,
                         help="Deterministic checks: tags, numbers (format-agnostic), "
                              "untranslated segments, required terms.")


anova_header("Translator", "AI translation with Translation Memory (TMX) + Termbase (CSV)")
tab_ws, tab_res = st.tabs(["📝 Workspace", "📊 Results"])


# ---------------- Workspace ----------------
with tab_ws:
    c1, c2 = st.columns(2)
    with c1:
        in_file = st.file_uploader(
            "File to translate — XLIFF or a document (required)",
            type=["xliff", "xlf", "mqxliff", "sdlxliff", "xml", "docx", "txt"],
            help="XLIFF for CAT round-trip, or a DOCX/TXT document (converted to "
                 "XLIFF internally and rebuilt to its original format).")
    with c2:
        tmx_file = st.file_uploader("Translation Memory — TMX (optional)", type=["tmx"])

    c3, c4 = st.columns(2)
    with c3:
        csv_file = st.file_uploader("Termbase — CSV export (optional)", type=["csv"])
    with c4:
        dnt_file = st.file_uploader("Do-Not-Translate list (optional)", type=["txt", "csv"])

    with st.expander("🎨 Style reference (target-language document — optional)"):
        ref_file = st.file_uploader(
            "Reference document in the TARGET language (DOCX/TXT/PDF)",
            type=["docx", "txt", "pdf"], key="ref_up",
            help="The model matches each segment to the most similar reference "
                 "passages for tone/style. Uses OpenAI embeddings.")
        emb_env = _openai_key()
        emb_typed = st.text_input("OpenAI key for style-embeddings", type="password", value="",
                                  help="Needed for the style reference even in Claude mode. "
                                       "Empty = read from environment/secrets.").strip()
        emb_key = emb_typed or emb_env
        if ref_file and not emb_key:
            st.caption("⚠️ Provide an OpenAI key to use the style reference (else it's skipped).")

    is_doc = bool(in_file) and documents.is_document(in_file.name)
    src_code, tgt_code = "en", "tr"
    if in_file and not is_doc:
        src_code, tgt_code = _detect_langs(in_file.getvalue())

    _codes = list(config.SUPPORTED_LANGUAGES.keys())
    _fmt = lambda c: f"{c} — {config.SUPPORTED_LANGUAGES.get(c, c)}"
    lc1, lc2 = st.columns(2)
    with lc1:
        src_code = st.selectbox("Source language", _codes,
                                index=_codes.index(src_code) if src_code in _codes else 0,
                                format_func=_fmt, disabled=(bool(in_file) and not is_doc))
    with lc2:
        tgt_code = st.selectbox("Target language", _codes,
                                index=_codes.index(tgt_code) if tgt_code in _codes else 0,
                                format_func=_fmt, disabled=(bool(in_file) and not is_doc))
    if in_file and not is_doc:
        st.caption("Languages auto-detected from the XLIFF header.")
    elif is_doc:
        st.caption(f"📄 Document mode: **{in_file.name}** → translated & rebuilt to its original format.")

    running = st.session_state.get("tr_running", False)
    go = st.button("🚀 Translate", type="primary", width="stretch",
                   disabled=running or in_file is None)

    if go and not running:
        if not api_key:
            st.error("Provide an API key (sidebar) or set it in the environment/secrets.")
        else:
            st.session_state.tr_running = True
            st.session_state.pop("tr_result", None)
            st.session_state._pending = {
                "original": in_file.getvalue(), "filename": in_file.name, "is_doc": is_doc,
                "tmx": tmx_file.getvalue() if tmx_file else None,
                "csv": csv_file.getvalue() if csv_file else None,
                "dnt": _read_dnt(dnt_file) if dnt_file else None,
                "ref": (ref_file.getvalue(), ref_file.name) if ref_file else None,
                "emb_key": emb_key,
                "provider": provider, "api_key": api_key, "model": model,
                "src": src_code, "tgt": tgt_code,
                "acceptance": acceptance, "match": match, "batch": batch,
                "hist": hist, "run_qa": run_qa,
            }
            st.rerun()

    if st.session_state.get("tr_running"):
        p = st.session_state.get("_pending", {})
        bar = st.progress(0.0, text="Starting…")

        def _cb(stage, done, total, msg):
            frac = (done / total) if total else 0.0
            bar.progress(min(frac, 1.0), text=f"{stage}: {msg} ({done}/{total})")

        try:
            # optional semantic style matcher
            emb = None
            if p.get("ref") and p.get("emb_key"):
                try:
                    chunks = documents.parse_reference_chunks(p["ref"][0], p["ref"][1])
                    if chunks:
                        emb = EmbeddingMatcher(p["emb_key"])
                        emb.load_reference(chunks)
                except Exception:
                    emb = None

            xliff_in = (documents.to_xliff(p["original"], p["filename"], p["src"], p["tgt"])
                        if p["is_doc"] else p["original"])
            res = translate(
                xliff_in, provider=p["provider"], api_key=p["api_key"], model=p["model"],
                src_code=p["src"], tgt_code=p["tgt"],
                tmx_bytes=p["tmx"], csv_bytes=p["csv"], dnt_terms=p["dnt"],
                embedding_matcher=emb, run_qa=p["run_qa"],
                acceptance_threshold=p["acceptance"], match_threshold=p["match"],
                batch_size=p["batch"], chat_history_length=p["hist"], progress_cb=_cb)

            st.session_state.tr_result = {
                "xliff_in": xliff_in, "original": p["original"], "filename": p["filename"],
                "is_doc": p["is_doc"], "segments": res["segments"],
                "translations": res["translations"], "stats": res["stats"],
                "qa": res["qa_errors"], "log": res["log"],
            }
        except Exception as e:
            st.session_state.tr_error = str(e)
        finally:
            st.session_state.tr_running = False
        st.rerun()

    if st.session_state.get("tr_error"):
        st.error(f"Translation error: {st.session_state.pop('tr_error')}")
    if st.session_state.get("tr_result"):
        st.success("✅ Translation complete — open the **📊 Results** tab to review, edit, and download.")


# ---------------- Results (editable) ----------------
with tab_res:
    r = st.session_state.get("tr_result")
    if not r:
        st.info("Run a translation in the **Workspace** tab first.")
    else:
        s = r["stats"]
        st.success(f"{s['translated']}/{s['total']} segments in {s['elapsed_sec']:.0f}s")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("From TM (no cost)", s["bypass"])
        m2.metric("TM-draft corrected", s["context"])
        m3.metric("Fresh LLM", s["fresh"])
        m4.metric("TB-demoted", s["demoted"])

        st.markdown("#### ✏️ Segments — edit any target, downloads update automatically")
        df = pd.DataFrame([{"ID": seg.id, "Source": seg.source,
                            "Target": r["translations"].get(seg.id, "")} for seg in r["segments"]])
        edited = st.data_editor(
            df, hide_index=True, width="stretch", height=460, key="seg_editor",
            column_config={
                "ID": st.column_config.TextColumn("ID", disabled=True, width="small"),
                "Source": st.column_config.TextColumn("Source", disabled=True),
                "Target": st.column_config.TextColumn("Target (editable)"),
            })
        edited_trans = {str(i): (t or "") for i, t in zip(edited["ID"], edited["Target"])}

        # Rebuild outputs from the CURRENT (possibly edited) targets.
        seg_map = {seg.id: seg for seg in r["segments"]}
        out_xliff = XMLParser.update_xliff(r["xliff_in"], edited_trans, seg_map)
        doc_bytes = doc_name = None
        if r["is_doc"]:
            doc_bytes, doc_name = documents.rebuild(edited_trans, r["original"], r["filename"])

        st.markdown("#### ⬇️ Download")
        cols = st.columns(3 if doc_bytes else 2)
        ci = 0
        if doc_bytes:
            cols[ci].download_button("Translated document", data=doc_bytes, file_name=doc_name,
                                     width="stretch"); ci += 1
        cols[ci].download_button("Translated XLIFF", data=out_xliff, file_name="translated.xliff",
                                 mime="application/xml", width="stretch"); ci += 1
        cols[ci].download_button("Log", data=r["log"], file_name="translation_log.txt",
                                 mime="text/plain", width="stretch")

        if r["qa"]:
            st.markdown(f"#### 🔎 QA — {len(r['qa'])} issue(s)")
            st.dataframe([{"Segment": e.segment_id, "Severity": e.severity, "Issue": e.description}
                          for e in r["qa"]], width="stretch", hide_index=True)

anova_footer()
