"""FleetOps — fleet-maintenance agent ops dashboard (Streamlit).

Design system: "Data-Dense Dashboard" (ui-ux-pro-max) — blue primary #1E40AF,
amber accent #D97706, semantic status colors always paired with SVG icons
(never color alone), Fira Code / Fira Sans, 8px spacing rhythm.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

from src.tools import DEFAULT_DB, run_sql

ROOT = Path(__file__).resolve().parent
RESULTS_DIR = ROOT / "evals" / "results"

st.set_page_config(page_title="FleetOps — Maintenance Agent", page_icon="🚚",
                   layout="wide", initial_sidebar_state="expanded")

# ---------------------------------------------------------------------------
# Design tokens + component CSS
# ---------------------------------------------------------------------------
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;500;600;700&family=Fira+Sans:wght@300;400;500;600;700&display=swap');

:root {
  --primary:#1E40AF; --secondary:#3B82F6; --accent:#D97706;
  --bg:#F8FAFC; --fg:#0F172A; --muted:#E9EEF6; --border:#DBEAFE;
  --ok:#15803D; --warn:#B45309; --err:#DC2626;
}
html, body, [class*="css"], .stMarkdown, p, li { font-family:'Fira Sans',sans-serif; }
h1,h2,h3, [data-testid="stMetricValue"], code, .kpi-value { font-family:'Fira Code',monospace; }

.block-container { padding-top: 2.5rem; max-width: 1200px; }

/* KPI cards */
.kpi { background:#fff; border:1px solid var(--border); border-radius:10px;
       padding:16px 20px; box-shadow:0 1px 2px rgba(15,23,42,.06); height:100%; }
.kpi .kpi-label { font-size:13px; font-weight:500; color:#475569;
                  display:flex; align-items:center; gap:6px; }
.kpi .kpi-value { font-size:28px; font-weight:700; color:var(--fg); margin-top:4px; }
.kpi .kpi-sub { font-size:12px; color:#64748B; margin-top:2px; }
.kpi.kpi-ok    { border-left:4px solid var(--ok); }
.kpi.kpi-warn  { border-left:4px solid var(--warn); }
.kpi.kpi-err   { border-left:4px solid var(--err); }
.kpi.kpi-info  { border-left:4px solid var(--primary); }

/* status badges: color + icon + text (never color alone) */
.badge { display:inline-flex; align-items:center; gap:5px; border-radius:999px;
         padding:3px 10px; font-size:12.5px; font-weight:600; line-height:1.4; }
.badge svg { flex:none; }
.badge-ok   { background:#DCFCE7; color:var(--ok); }
.badge-warn { background:#FEF3C7; color:var(--warn); }
.badge-err  { background:#FEE2E2; color:var(--err); }
.badge-info { background:#DBEAFE; color:var(--primary); }

.stButton>button, .stChatInput { cursor:pointer; }
.stButton>button { transition: all .18s ease-out; border-radius:8px; }
.stButton>button:hover { border-color:var(--primary); color:var(--primary); }
.stTabs [data-baseweb="tab"] { font-family:'Fira Sans'; font-weight:600; }

@media (prefers-reduced-motion: reduce) {
  * { animation:none !important; transition:none !important; }
}
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# SVG icons (Lucide, inline, 16px, stroke 2)
# ---------------------------------------------------------------------------
def icon(name: str, color: str = "currentColor", size: int = 16) -> str:
    paths = {
        "check": '<path d="M20 6 9 17l-5-5"/>',
        "x": '<path d="M18 6 6 18M6 6l12 12"/>',
        "alert": '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><path d="M12 9v4M12 17h.01"/>',
        "truck": '<path d="M14 18V6a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v11a1 1 0 0 0 1 1h2"/><path d="M15 18H9"/><path d="M19 18h2a1 1 0 0 0 1-1v-3.65a1 1 0 0 0-.22-.62l-3.48-4.35a1 1 0 0 0-.78-.38H14"/><circle cx="17" cy="18" r="2"/><circle cx="7" cy="18" r="2"/>',
        "wrench": '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
        "gauge": '<path d="m12 14 4-4"/><path d="M3.34 19a10 10 0 1 1 17.32 0"/>',
        "shield": '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1 1 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/>',
        "database": '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5V19A9 3 0 0 0 21 19V5"/><path d="M3 12A9 3 0 0 0 21 12"/>',
        "bot": '<path d="M12 8V4H8"/><rect width="16" height="12" x="4" y="8" rx="2"/><path d="M2 14h2M20 14h2M15 13v2M9 13v2"/>',
        "chart": '<path d="M3 3v16a2 2 0 0 0 2 2h16"/><path d="M18 17V9M13 17V5M8 17v-3"/>',
    }
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
            f'stroke="{color}" stroke-width="2" stroke-linecap="round" '
            f'stroke-linejoin="round" role="img" aria-hidden="true">{paths[name]}</svg>')


def badge(kind: str, label: str) -> str:
    icons = {"ok": "check", "warn": "alert", "err": "x", "info": "bot"}
    return f'<span class="badge badge-{kind}">{icon(icons[kind])}{label}</span>'


def kpi(label: str, value: str, sub: str, kind: str, icon_name: str) -> str:
    colors = {"ok": "#15803D", "warn": "#B45309", "err": "#DC2626", "info": "#1E40AF"}
    return (f'<div class="kpi kpi-{kind}"><div class="kpi-label">'
            f'{icon(icon_name, colors[kind])}{label}</div>'
            f'<div class="kpi-value">{value}</div><div class="kpi-sub">{sub}</div></div>')


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300)
def fleet_snapshot() -> dict:
    con = sqlite3.connect(f"file:{DEFAULT_DB}?mode=ro", uri=True)
    snap = {
        "total": con.execute("SELECT COUNT(*) FROM trucks").fetchone()[0],
        "active": con.execute("SELECT COUNT(*) FROM trucks WHERE status='active'").fetchone()[0],
        "in_shop": con.execute("SELECT COUNT(*) FROM trucks WHERE status='in_shop'").fetchone()[0],
        "overdue": con.execute(
            "SELECT COUNT(*) FROM trucks WHERE status='active' AND"
            " (next_service_due_date<'2026-07-08' OR next_service_due_miles<mileage)"
        ).fetchone()[0],
        "low_stock": con.execute("SELECT COUNT(*) FROM parts WHERE stock_qty<min_stock").fetchone()[0],
        "cost_30d": con.execute(
            "SELECT COALESCE(ROUND(SUM(cost),0),0) FROM maintenance_log WHERE event_date>='2026-06-08'"
        ).fetchone()[0],
    }
    con.close()
    return snap


def latest_eval_results() -> dict | None:
    for name in ("results_live.json", "results_live_regression.json",
                 "results_mock.json", "results_mock_regression.json"):
        p = RESULTS_DIR / name
        if p.exists():
            return json.loads(p.read_text())
    return None


HAS_KEY = bool(os.environ.get("ANTHROPIC_API_KEY"))

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown(f'<h2 style="display:flex;align-items:center;gap:8px;margin-bottom:0">'
                f'{icon("truck", "#1E40AF", 26)} FleetOps</h2>', unsafe_allow_html=True)
    st.caption("AI maintenance agent over a synthetic fleet database · data anchor 2026-07-08")

    snap = fleet_snapshot()
    st.markdown("#### Fleet snapshot")
    st.markdown(
        badge("info", f"{snap['active']} active") + " " +
        badge("warn", f"{snap['overdue']} overdue") + " " +
        badge("err", f"{snap['low_stock']} parts low"),
        unsafe_allow_html=True)
    st.markdown("")
    if HAS_KEY:
        st.markdown(badge("ok", "Agent online · claude-opus-4-8"), unsafe_allow_html=True)
    else:
        st.markdown(badge("warn", "No API key — quick queries only"), unsafe_allow_html=True)
        st.caption("Set `ANTHROPIC_API_KEY` to enable the conversational agent.")

    st.divider()
    st.markdown("#### Try asking")
    SAMPLES = ["Which trucks are overdue for service?",
               "What does SPN-3226-FMI-4 mean and is the part in stock?",
               "Did battery faults spike last winter?",
               "Generate a 30-day fleet maintenance report"]
    for s in SAMPLES:
        if st.button(s, use_container_width=True, key=f"sample-{s[:20]}"):
            st.session_state["queued_question"] = s

# ---------------------------------------------------------------------------
# Header + KPI row
# ---------------------------------------------------------------------------
st.markdown(f'<h1 style="display:flex;align-items:center;gap:10px">'
            f'{icon("gauge", "#1E40AF", 30)} Fleet Operations Console</h1>',
            unsafe_allow_html=True)

c1, c2, c3, c4 = st.columns(4, gap="small")
c1.markdown(kpi("Active trucks", str(snap["active"]), f"of {snap['total']} total", "info", "truck"),
            unsafe_allow_html=True)
c2.markdown(kpi("Overdue service", str(snap["overdue"]), "past due date or mileage",
                "warn" if snap["overdue"] else "ok", "wrench"), unsafe_allow_html=True)
c3.markdown(kpi("Parts below min stock", str(snap["low_stock"]), "reorder recommended",
                "err" if snap["low_stock"] else "ok", "alert"), unsafe_allow_html=True)
c4.markdown(kpi("30-day maintenance spend", f"${snap['cost_30d']:,.0f}", "all event types",
                "info", "chart"), unsafe_allow_html=True)
st.markdown("")

tab_agent, tab_evals = st.tabs(["Ask the agent", "Evals"])

# ---------------------------------------------------------------------------
# Tab 1 — Agent console
# ---------------------------------------------------------------------------
with tab_agent:
    if "chat" not in st.session_state:
        st.session_state.chat = []

    for msg in st.session_state.chat:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            for tc in msg.get("tool_calls", []):
                with st.expander(f"Tool call: `{tc['name']}`", expanded=False):
                    st.code(json.dumps(tc["input"], indent=2), language="json")
                    r = tc["result"]
                    if isinstance(r, dict) and "rows" in r:
                        st.dataframe(pd.DataFrame(r["rows"], columns=r["columns"]),
                                     use_container_width=True, hide_index=True)
                    else:
                        st.code(json.dumps(r, indent=2, default=str), language="json")

    question = st.chat_input("Ask about trucks, fault codes, parts, or costs…",
                             disabled=not HAS_KEY)
    if not question and "queued_question" in st.session_state and HAS_KEY:
        question = st.session_state.pop("queued_question")

    if question:
        st.session_state.chat.append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"):
            with st.spinner("Agent is querying the fleet database…"):
                try:
                    from src.agent import run_agent
                    result = run_agent(question)
                    entry = {"role": "assistant", "content": result.answer,
                             "tool_calls": result.tool_calls}
                except Exception as exc:  # surface API errors in the UI
                    entry = {"role": "assistant",
                             "content": f"Agent error: `{exc}`", "tool_calls": []}
            st.session_state.chat.append(entry)
            st.rerun()

    if not HAS_KEY:
        st.info("Conversational agent is offline (no API key). "
                "Use the quick queries below — they run the same read-only SQL tool.")
        q1, q2, q3 = st.columns(3)
        QUICK = {
            "Overdue trucks": "SELECT truck_id, make, model, mileage, next_service_due_date FROM trucks WHERE status='active' AND (next_service_due_date<'2026-07-08' OR next_service_due_miles<mileage) ORDER BY next_service_due_date",
            "Low-stock parts": "SELECT part_name, part_number, stock_qty, min_stock, supplier, lead_time_days FROM parts WHERE stock_qty<min_stock",
            "Top fault codes": "SELECT ml.fault_code, fc.description, COUNT(*) AS occurrences FROM maintenance_log ml JOIN fault_codes fc ON fc.code=ml.fault_code GROUP BY ml.fault_code ORDER BY occurrences DESC LIMIT 10",
        }
        for col, (label, sql) in zip((q1, q2, q3), QUICK.items()):
            if col.button(label, use_container_width=True):
                st.session_state["quick_sql"] = (label, sql)
        if "quick_sql" in st.session_state:
            label, sql = st.session_state["quick_sql"]
            r = run_sql(sql)
            st.markdown(f"**{label}**")
            st.dataframe(pd.DataFrame(r["rows"], columns=r["columns"]),
                         use_container_width=True, hide_index=True)

# ---------------------------------------------------------------------------
# Tab 2 — Evals
# ---------------------------------------------------------------------------
with tab_evals:
    data = latest_eval_results()
    if not data:
        st.warning("No eval results found. Run `python evals/run_evals.py --mock` "
                   "or `--live` to populate this view.")
    else:
        s, rows = data["summary"], data["rows"]
        is_live = s["mode"] == "live"
        n_pass = sum(1 for r in rows if r["tool_pass"])

        e1, e2, e3, e4 = st.columns(4, gap="small")
        e1.markdown(kpi("Tool-selection accuracy", s["tool_selection_accuracy"].split(" ")[0],
                        f"{n_pass}/{len(rows)} cases", "ok" if n_pass == len(rows) else "warn",
                        "check"), unsafe_allow_html=True)
        judge_val = (f'{s["mean_judge_score"]}/5' if isinstance(s["mean_judge_score"], (int, float))
                     else "n/a")
        e2.markdown(kpi("Mean judge score", judge_val,
                        f'judge: {s["judge_model"]}' if is_live else "not measured (mock mode)",
                        "ok" if is_live else "warn", "bot"), unsafe_allow_html=True)
        adv = [r for r in rows if r["category"] == "adversarial"]
        adv_ok = all(r["tool_pass"] for r in adv) if adv else True
        e3.markdown(kpi("Write-guardrail", "HELD" if adv_ok else "BREACHED",
                        f"{len(adv)} adversarial cases", "ok" if adv_ok else "err", "shield"),
                    unsafe_allow_html=True)
        e4.markdown(kpi("Golden dataset", str(len(rows)),
                        f"mode: {'live' if is_live else 'mock'}", "info", "database"),
                    unsafe_allow_html=True)

        if not is_live:
            st.markdown("")
            st.info("These are **mock-mode harness results** (deterministic tools, no LLM) — "
                    "they validate the pipeline and guardrails, **not** model quality. "
                    "Run `--live` with an API key for measured agent scores.")

        st.markdown("#### Pass rate by category")
        df = pd.DataFrame([{"category": r["category"],
                            "result": "pass" if r["tool_pass"] else "fail"} for r in rows])
        chart_df = (df.groupby(["category", "result"]).size().reset_index(name="count"))
        import altair as alt
        chart = (alt.Chart(chart_df).mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
                 .encode(
                     x=alt.X("category:N", title=None, axis=alt.Axis(labelAngle=0)),
                     y=alt.Y("count:Q", title="cases"),
                     color=alt.Color("result:N",
                                     scale=alt.Scale(domain=["pass", "fail"],
                                                     range=["#15803D", "#DC2626"]),
                                     legend=alt.Legend(title="Tool selection")),
                     tooltip=["category", "result", "count"])
                 .properties(height=220))
        st.altair_chart(chart, use_container_width=True)

        st.markdown("#### Per-question breakdown")
        table = pd.DataFrame([{
            "id": r["id"], "category": r["category"],
            "question": r["question"],
            "tools called": ", ".join(c["name"] for c in r["tool_calls"]) or "(none)",
            "tool selection": "✓ pass" if r["tool_pass"] else "✗ fail",
            "judge": r.get("judge_score", "—"),
            "note": r["tool_note"],
        } for r in rows])
        st.dataframe(table, use_container_width=True, hide_index=True, height=420)

        xlsx = next(iter(sorted(RESULTS_DIR.glob("results_*.xlsx"))), None)
        if xlsx:
            st.download_button("Download results (xlsx)", xlsx.read_bytes(),
                               file_name=xlsx.name,
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

st.markdown("---")
st.caption("All data is synthetic (seeded generator). SQL tool is strictly read-only — "
           "non-SELECT statements are refused at three layers.")
