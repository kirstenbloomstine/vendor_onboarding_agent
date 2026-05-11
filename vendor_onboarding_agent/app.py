"""
app.py — Streamlit UI for the Vendor Onboarding Agent.

Run with:  streamlit run app.py
"""

import json
import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from agent import run_agent
from data_loader import AVAILABLE_CASES, format_case_for_prompt, load_case

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
load_dotenv()

st.set_page_config(
    page_title="Vendor Onboarding Agent",
    page_icon="📋",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Custom CSS — subtle professional look
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
    .stTabs [data-baseweb="tab-list"] { gap: 8px; }
    .block-container { padding-top: 1.5rem; }
    .status-blocked  { background:#fee2e2; border-left:4px solid #dc2626;
                       padding:12px 16px; border-radius:6px; font-weight:600;
                       color:#111827; }
    .status-needs_info { background:#fef9c3; border-left:4px solid #ca8a04;
                         padding:12px 16px; border-radius:6px; font-weight:600;
                         color:#111827; }
    .status-ready    { background:#dcfce7; border-left:4px solid #16a34a;
                       padding:12px 16px; border-radius:6px; font-weight:600;
                       color:#111827; }
    .risk-high   { color:#dc2626; font-weight:700; }
    .risk-medium { color:#d97706; font-weight:700; }
    .risk-low    { color:#16a34a; font-weight:700; }
    .hitl-box    { background:#eff6ff; border:2px solid #3b82f6;
                   border-radius:8px; padding:16px; margin-top:12px; }
    .flag-blocking { color:#dc2626; }
    .flag-warning  { color:#d97706; }
    .flag-info     { color:#2563eb; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.title("Vendor Onboarding Agent")
    st.caption("Internal procurement triage tool · Prototype")
    st.divider()

    # API key
    api_key_input = st.text_input(
        "Groq API Key",
        value=os.environ.get("GROQ_API_KEY", ""),
        type="password",
        help="Free key from https://console.groq.com — set GROQ_API_KEY in .env or enter here.",
    )
    if api_key_input:
        os.environ["GROQ_API_KEY"] = api_key_input

    st.divider()

    # Case selector
    case_options = {f"{k} — {v}": k for k, v in AVAILABLE_CASES.items()}
    selected_label = st.selectbox("Select case", list(case_options.keys()))
    case_id = case_options[selected_label]

    st.divider()
    run_button = st.button("Run Agent Evaluation", type="primary", use_container_width=True)

    st.divider()
    st.caption(
        "⚠️ This agent may NOT approve vendors, commit spend, or send external "
        "communications without human sign-off."
    )


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
if "results" not in st.session_state:
    st.session_state.results = {}
if "hitl_decision" not in st.session_state:
    st.session_state.hitl_decision = {}


# ---------------------------------------------------------------------------
# Helper renderers
# ---------------------------------------------------------------------------

def _status_badge(status: str) -> str:
    labels = {
        "blocked": "🔴 BLOCKED — Cannot route for approval",
        "needs_info": "🟡 NEEDS INFO — Missing required information",
        "ready_for_routing": "🟢 READY — Route for approval",
        "error": "⚠️ AGENT ERROR",
    }
    css_class = "status-blocked" if status == "blocked" else (
        "status-needs_info" if status == "needs_info" else "status-ready"
    )
    return f'<div class="{css_class}">{labels.get(status, status.upper())}</div>'


def _risk_badge(tier: str) -> str:
    icons = {"high": "🔴", "medium": "🟡", "low": "🟢"}
    return f'<span class="risk-{tier}">{icons.get(tier, "")} {tier.upper()} RISK</span>'


def _severity_icon(sev: str) -> str:
    return {"blocking": "🔴", "warning": "🟡", "info": "🔵"}.get(sev, "")


def _render_overview(r: dict):
    st.subheader("Evaluation Summary")

    col1, col2 = st.columns([2, 1])
    with col1:
        st.markdown(_status_badge(r.get("evaluation_status", "error")), unsafe_allow_html=True)
        st.markdown("")
        st.markdown(r.get("summary", ""))
    with col2:
        st.markdown(f"**Risk Tier:** {_risk_badge(r.get('risk_tier', ''))}", unsafe_allow_html=True)
        ct = r.get("commercial_terms", {})
        st.metric("ACV", f"${ct.get('annual_contract_value', 0):,.0f}")
        st.metric("TCV", f"${ct.get('total_contract_value', 0):,.0f}")
        st.metric("Term", f"{ct.get('contract_term_months', '?')} months")

    # Blocking issues banner
    blocking = r.get("blocking_issues", [])
    if blocking:
        st.error("**Blocking Issues — resolve before routing for approval:**")
        for b in blocking:
            st.markdown(f"- {b}")


def _render_commercial(r: dict):
    ct = r.get("commercial_terms", {})
    bc = r.get("budget_check", {})
    vc = r.get("duplicate_vendor_check", {})

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Commercial Terms**")
        st.table(
            {
                "Field": ["ACV", "TCV", "Term", "Payment Terms", "One-time Fees", "Start Date"],
                "Value": [
                    f"${ct.get('annual_contract_value', 0):,.0f}",
                    f"${ct.get('total_contract_value', 0):,.0f}",
                    f"{ct.get('contract_term_months', '?')} months",
                    ct.get("payment_terms", "—"),
                    f"${ct.get('one_time_fees', 0):,.0f}",
                    ct.get("requested_start_date", "—"),
                ],
            }
        )
    with col2:
        st.markdown("**Budget Check**")
        remaining = bc.get("budget_remaining", 0)
        acv = bc.get("acv", 0)
        sufficient = bc.get("budget_sufficient", True)
        color = "normal" if sufficient else "inverse"
        st.metric(
            "Budget Remaining",
            f"${remaining:,.0f}",
            delta=f"{'✓ sufficient' if sufficient else '✗ shortfall'}",
            delta_color=color,
        )
        st.markdown(f"**Cost Center:** {bc.get('cost_center', '—')}")

        st.markdown("**Vendor Register Check**")
        vc_status = vc.get("status", "—")
        icons = {"clear": "✅", "renewal": "🔄", "potential_duplicate": "⚠️"}
        st.markdown(f"{icons.get(vc_status, '•')} {vc_status.replace('_', ' ').title()}")
        st.caption(vc.get("detail", ""))


def _render_documents(r: dict):
    ic = r.get("intake_completeness", {})
    missing_docs = r.get("missing_documents", [])

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Intake Completeness**")
        if ic.get("complete", True):
            st.success("All required intake fields present")
        else:
            st.warning(f"{len(ic.get('missing_fields', []))} missing fields")
            for f in ic.get("missing_fields", []):
                st.markdown(f"- `{f}`")

    with col2:
        st.markdown("**Missing Documents**")
        if not missing_docs:
            st.success("No missing documents identified")
        else:
            for doc in missing_docs:
                st.markdown(f"- 🔴 {doc}")


def _render_policy_flags(r: dict):
    flags = r.get("policy_flags", [])
    if not flags:
        st.success("No policy flags raised.")
        return

    blocking = [f for f in flags if f.get("severity") == "blocking"]
    warning  = [f for f in flags if f.get("severity") == "warning"]
    info     = [f for f in flags if f.get("severity") == "info"]

    for section, items in [("Blocking", blocking), ("Warning", warning), ("Info", info)]:
        if not items:
            continue
        st.markdown(f"**{section}**")
        for flag in items:
            icon = _severity_icon(flag.get("severity", "info"))
            with st.expander(f"{icon} [{flag.get('policy', '')}] {flag.get('finding', '')[:80]}"):
                st.write(flag.get("finding", ""))
        st.markdown("")


def _render_approval_routing(r: dict):
    routing = r.get("approval_routing", {})
    if not routing:
        st.info("No approval routing determined.")
        return

    required = {k: v for k, v in routing.items() if v.get("required")}
    not_required = {k: v for k, v in routing.items() if not v.get("required")}

    st.markdown("**Required Approvals**")
    if not required:
        st.info("No additional approvals required beyond business owner.")
    else:
        for role, info in required.items():
            name = info.get("name", "")
            reason = info.get("reason", "")
            label = role.replace("_", " ").title()
            name_str = f" — {name}" if name else ""
            st.markdown(f"✅ **{label}**{name_str}")
            if reason:
                st.caption(f"  Reason: {reason}")

    if not_required:
        with st.expander("Approvals not required"):
            for role, info in not_required.items():
                label = role.replace("_", " ").title()
                st.markdown(f"~~{label}~~ — {info.get('reason', 'not triggered')}")


def _render_hitl(r: dict, case_id: str):
    """Human-in-the-loop approval gate for the draft vendor follow-up."""
    st.subheader("Human-in-the-Loop Controls")
    draft = r.get("draft_vendor_follow_up", "")
    ticket = r.get("draft_internal_ticket", "")

    hitl_key = case_id

    if hitl_key not in st.session_state.hitl_decision:
        st.session_state.hitl_decision[hitl_key] = None

    decision = st.session_state.hitl_decision[hitl_key]

    # Internal ticket
    if ticket:
        st.markdown("**Draft Internal Ticket**")
        st.code(ticket, language=None)

    # Draft vendor follow-up
    if draft:
        st.markdown("**Draft External Vendor Follow-up**")
        st.markdown(
            '<div class="hitl-box">',
            unsafe_allow_html=True,
        )
        st.warning(
            "This is a DRAFT message. It requires Procurement Owner approval before sending."
        )
        edited = st.text_area(
            "Review and edit draft (optional)",
            value=draft,
            height=200,
            key=f"draft_edit_{case_id}",
        )
        st.markdown("</div>", unsafe_allow_html=True)

        col1, col2, col3 = st.columns(3)
        with col1:
            if st.button(
                "✅ Approve Draft",
                key=f"approve_{case_id}",
                type="primary",
                disabled=(decision is not None),
            ):
                st.session_state.hitl_decision[hitl_key] = "approved"
                st.rerun()
        with col2:
            if st.button(
                "✏️ Request Revision",
                key=f"revise_{case_id}",
                disabled=(decision is not None),
            ):
                st.session_state.hitl_decision[hitl_key] = "revision_requested"
                st.rerun()
        with col3:
            if st.button(
                "❌ Reject",
                key=f"reject_{case_id}",
                disabled=(decision is not None),
            ):
                st.session_state.hitl_decision[hitl_key] = "rejected"
                st.rerun()

        if decision == "approved":
            st.success(
                "Draft approved by Procurement Owner. Ready to send — copy the text above "
                "and send via your email client. (This prototype does not send email.)"
            )
        elif decision == "revision_requested":
            st.warning("Revision requested. Edit the draft above and re-submit.")
            if st.button("Re-submit for approval", key=f"resubmit_{case_id}"):
                st.session_state.hitl_decision[hitl_key] = None
                st.rerun()
        elif decision == "rejected":
            st.error("Draft rejected. No communication will be sent.")
    else:
        st.info("No external vendor communication drafted for this case.")

    # Notes
    notes = r.get("agent_notes", "")
    if notes:
        st.markdown("**Agent Notes & Escalations**")
        st.info(notes)


def _render_raw(result: dict):
    with st.expander("Tool Call Log"):
        st.json(result.get("tool_calls", []))
    with st.expander("Full Agent Response"):
        st.text(result.get("raw_response", ""))
    with st.expander("Raw JSON Report"):
        st.json(result.get("report", {}))


# ---------------------------------------------------------------------------
# Progress callback for sidebar
# ---------------------------------------------------------------------------

def make_progress_callback(placeholder):
    steps = []

    def callback(event_type, data):
        if event_type == "tool_call":
            steps.append(f"🔧 **{data['name']}**")
            placeholder.markdown("\n".join(steps))
        elif event_type == "done":
            steps.append("✅ Evaluation complete")
            placeholder.markdown("\n".join(steps))

    return callback


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

st.title("Vendor Onboarding Agent")
st.caption(
    "Reviews vendor packages against internal policies and produces a structured "
    "procurement recommendation. All approvals require human sign-off."
)

if run_button:
    if not api_key_input:
        st.error("Please enter your Groq API key in the sidebar.")
        st.stop()

    with st.spinner(f"Loading case {case_id}…"):
        case = load_case(case_id)
        case_docs = format_case_for_prompt(case)

    st.sidebar.divider()
    st.sidebar.markdown("**Agent steps:**")
    progress_placeholder = st.sidebar.empty()
    callback = make_progress_callback(progress_placeholder)

    with st.spinner("Running agent evaluation…"):
        result = run_agent(case_docs, api_key=api_key_input, stream_callback=callback)

    st.session_state.results[case_id] = result
    # Reset HITL decision when re-running
    st.session_state.hitl_decision[case_id] = None

# Show results
if case_id in st.session_state.results:
    result = st.session_state.results[case_id]
    r = result.get("report", {})

    if r.get("evaluation_status") == "error":
        st.error("Agent returned an error or could not parse a structured report.")
        st.text(r.get("raw", ""))
    else:
        tabs = st.tabs([
            "Overview",
            "Commercial & Budget",
            "Documents",
            "Policy Flags",
            "Approval Routing",
            "Human Approval",
            "Debug",
        ])
        with tabs[0]:
            _render_overview(r)
        with tabs[1]:
            _render_commercial(r)
        with tabs[2]:
            _render_documents(r)
        with tabs[3]:
            _render_policy_flags(r)
        with tabs[4]:
            _render_approval_routing(r)
        with tabs[5]:
            _render_hitl(r, case_id)
        with tabs[6]:
            _render_raw(result)
else:
    st.info("Select a case in the sidebar and click **Run Agent Evaluation** to begin.")
    st.markdown(
        """
        **Available cases:**
        | Case | Vendor | Type | Complexity |
        |------|--------|------|-----------|
        | case_001 | Northstar Analytics | SaaS · AI-powered | High — EU subprocessor, SOC 2 gap, AI training clause |
        | case_002 | Workspace Depot | Office Supplies Renewal | Low — missing tax/setup forms |
        | case_003 | TalentPulse AI | HR SaaS · AI-powered | Very High — employee PII, HRIS integration, budget shortfall |
        """
    )
