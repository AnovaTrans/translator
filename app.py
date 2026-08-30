"""Anova Translator — standalone TMX + TB(CSV) AI translation (Streamlit UI).

Thin UI over the Streamlit-free engine in services/orchestrator.translate().
API keys resolve from the environment / st.secrets first (portal-managed
credits) and fall back to a sidebar field — never forced.
"""
import os
import re

import streamlit as st

import config
import model_utils
from anova_brand_theme import apply_anova_theme, anova_header, anova_footer, anova_sidebar_logo
from utils.xml_parser import XMLParser
from services.orchestrator import translate
from services import documents

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
    """Portal/env/secrets first, per provider."""
    if provider == "Claude":
        return os.getenv("ANTHROPIC_API_KEY", "") or _secret("anthropic_api_key") or _secret("ANTHROPIC_API_KEY")
    return os.getenv("OPENAI_API_KEY", "") or _secret("openai_api_key") or _secret("OPENAI_API_KEY")


def _detect_langs(xliff_bytes: bytes):
    head = xliff_bytes[:6000].decode("utf-8", errors="ignore")
    s = re.search(r'source-language="([^"]+)"', head)
    t = re.search(r'target-language="([^"]+)"', head)
    norm = lambda c: (c or "").split("-")[0].lower()
    return (norm(s.group(1)) if s else "en", norm(t.group(1)) if t else "tr")


def _read_dnt(f):
    """First column of a DNT txt/csv, minus the header row."""
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
    typed = st.text_input(
        f"{provider} API Key",
        type="password",
        value="",
        help="Left empty, the key is read from the environment / secrets "
             "(managed by the portal).",
    ).strip()
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
    run_qa = st.checkbox("Run QA after translation", value=False,
                         help="Deterministic checks: tags, numbers (format-agnostic), "
                              "untranslated segments, required terms.")


# ---------------- Main ----------------
anova_header("Translator", "AI translation with Translation Memory (TMX) + Termbase (CSV)")

c1, c2 = st.columns(2)
with c1:
    in_file = st.file_uploader(
        "File to translate — XLIFF or a document (required)",
        type=["xliff", "xlf", "mqxliff", "sdlxliff", "xml", "docx", "txt"],
        help="XLIFF/XLF for CAT round-trip, or a DOCX/TXT document (converted to "
             "XLIFF internally and rebuilt to its original format after translation).",
    )
with c2:
    tmx_file = st.file_uploader("Translation Memory — TMX (optional)", type=["tmx"])

c3, c4 = st.columns(2)
with c3:
    csv_file = st.file_uploader("Termbase — CSV export (optional)", type=["csv"])
with c4:
    dnt_file = st.file_uploader("Do-Not-Translate list (optional)", type=["txt", "csv"])

is_doc = bool(in_file) and documents.is_document(in_file.name)

# Languages: auto-detected from an XLIFF header; chosen by the user for documents.
src_code, tgt_code = "en", "tr"
if in_file and not is_doc:
    src_code, tgt_code = _detect_langs(in_file.getvalue())

_lang_codes = list(config.SUPPORTED_LANGUAGES.keys())
_fmt = lambda c: f"{c} — {config.SUPPORTED_LANGUAGES.get(c, c)}"
lc1, lc2 = st.columns(2)
with lc1:
    src_code = st.selectbox("Source language", _lang_codes,
                            index=_lang_codes.index(src_code) if src_code in _lang_codes else 0,
                            format_func=_fmt, disabled=(bool(in_file) and not is_doc))
with lc2:
    tgt_code = st.selectbox("Target language", _lang_codes,
                            index=_lang_codes.index(tgt_code) if tgt_code in _lang_codes else 0,
                            format_func=_fmt, disabled=(bool(in_file) and not is_doc))
if in_file and not is_doc:
    st.caption("Languages auto-detected from the XLIFF header.")
elif is_doc:
    st.caption(f"📄 Document mode: **{in_file.name}** → translated & rebuilt to its original format.")

running = st.session_state.get("tr_running", False)
go = st.button("🚀 Translate", type="primary", use_container_width=True,
               disabled=running or in_file is None)

if go and not running:
    if not api_key:
        st.error("Provide an API key (sidebar) or set it in the environment/secrets.")
    else:
        st.session_state.tr_running = True
        st.session_state.pop("tr_result", None)
        st.session_state._pending = {
            "original": in_file.getvalue(),
            "filename": in_file.name,
            "is_doc": is_doc,
            "tmx": tmx_file.getvalue() if tmx_file else None,
            "csv": csv_file.getvalue() if csv_file else None,
            "dnt": _read_dnt(dnt_file) if dnt_file else None,
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
        # Documents are converted to XLIFF first; XLIFF/XLF are used as-is.
        if p["is_doc"]:
            xliff_in = documents.to_xliff(p["original"], p["filename"], p["src"], p["tgt"])
        else:
            xliff_in = p["original"]

        res = translate(
            xliff_in, provider=p["provider"], api_key=p["api_key"], model=p["model"],
            src_code=p["src"], tgt_code=p["tgt"],
            tmx_bytes=p["tmx"], csv_bytes=p["csv"], dnt_terms=p["dnt"],
            run_qa=p["run_qa"],
            acceptance_threshold=p["acceptance"], match_threshold=p["match"],
            batch_size=p["batch"], chat_history_length=p["hist"],
            progress_cb=_cb,
        )
        seg_map = {s.id: s for s in res["segments"]}
        out_xliff = XMLParser.update_xliff(xliff_in, res["translations"], seg_map)

        doc_bytes, doc_name = (None, None)
        if p["is_doc"]:
            doc_bytes, doc_name = documents.rebuild(res["translations"], p["original"], p["filename"])

        st.session_state.tr_result = {
            "xliff": out_xliff, "doc_bytes": doc_bytes, "doc_name": doc_name,
            "log": res["log"], "stats": res["stats"], "qa": res["qa_errors"],
        }
    except Exception as e:
        st.session_state.tr_error = str(e)
    finally:
        st.session_state.tr_running = False
    st.rerun()


if st.session_state.get("tr_error"):
    st.error(f"Translation error: {st.session_state.pop('tr_error')}")

r = st.session_state.get("tr_result")
if r:
    s = r["stats"]
    st.success(f"✅ Done in {s['elapsed_sec']:.0f}s — {s['translated']}/{s['total']} segments")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("From TM (no cost)", s["bypass"])
    m2.metric("TM-draft corrected", s["context"])
    m3.metric("Fresh LLM", s["fresh"])
    m4.metric("TB-demoted", s["demoted"])

    cols = st.columns(3 if r.get("doc_bytes") else 2)
    ci = 0
    if r.get("doc_bytes"):
        cols[ci].download_button("⬇️ Download translated document", data=r["doc_bytes"],
                                 file_name=r["doc_name"], use_container_width=True)
        ci += 1
    cols[ci].download_button("⬇️ Download translated XLIFF", data=r["xliff"],
                             file_name="translated.xliff", mime="application/xml",
                             use_container_width=True)
    ci += 1
    cols[ci].download_button("📋 Download log", data=r["log"],
                             file_name="translation_log.txt", mime="text/plain",
                             use_container_width=True)

    if r["qa"]:
        st.subheader(f"🔎 QA — {len(r['qa'])} issue(s)")
        st.dataframe(
            [{"Segment": e.segment_id, "Severity": e.severity, "Issue": e.description}
             for e in r["qa"]],
            use_container_width=True, hide_index=True,
        )

anova_footer()
