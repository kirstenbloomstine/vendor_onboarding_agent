# Vendor Onboarding Agent — Prototype

A lightweight AI agent that reviews vendor onboarding packages and produces
structured procurement recommendations for a human Procurement Owner.

---

## Quick Start

### 1. Install dependencies

```bash
cd vendor_onboarding_agent
pip install -r requirements.txt
```

Requires Python 3.10+.

### 2. Set your Anthropic API key

Copy the example env file and fill in your key:

```bash
cp .env.example .env
# then edit .env and set ANTHROPIC_API_KEY=sk-ant-...
```

Or enter the key directly in the Streamlit sidebar at runtime.

### 3. Run the app

```bash
streamlit run app.py
```

The app will open at `http://localhost:8501`.

### 4. Evaluate a case

1. Select a case from the sidebar (`case_001`, `case_002`, or `case_003`).
2. Click **Run Agent Evaluation**.
3. Review findings across the tabs: Overview → Commercial → Documents →
   Policy Flags → Approval Routing → Human Approval.
4. In the **Human Approval** tab, review the draft vendor follow-up message
   and click **Approve Draft**, **Request Revision**, or **Reject**.

---

## Project Structure

```
vendor_onboarding_agent/
├── app.py            # Streamlit UI — case selector, tabs, HITL approval
├── agent.py          # Anthropic tool-use agent loop + system prompt
├── tools.py          # Deterministic policy tools (finance, legal, security checks)
├── data_loader.py    # Reads .xlsx, .csv, .pdf, .txt, .md case files
├── requirements.txt
├── .env.example
└── README.md

../Candidate_package/
├── cases/            # Three synthetic vendor onboarding cases
├── docs/             # Six internal policy documents (loaded into system prompt)
└── tools/            # vendor_register.csv, budget_lookup.csv
```

---

## Architecture Note

```
┌──────────────────────────────────────────────────────────────┐
│                    Streamlit UI (app.py)                     │
│  Case selector → Run button → Tabbed report → HITL gate      │
└────────────────────────┬─────────────────────────────────────┘
                         │ case documents (text)
                         ▼
┌──────────────────────────────────────────────────────────────┐
│                  Agent Loop (agent.py)                       │
│                                                              │
│  System prompt embeds 6 policy docs as authoritative text    │
│                                                              │
│  Claude (claude-sonnet-4-6) ◄──────────────────────────────┐ │
│        │ tool_use request                                   │ │
│        ▼                                                    │ │
│  dispatch_tool() ──► tools.py (deterministic)               │ │
│    • lookup_vendor_register   (vendor_register.csv)         │ │
│    • lookup_budget            (budget_lookup.csv)           │ │
│    • calculate_total_contract_value                         │ │
│    • check_finance_approval_requirements                    │ │
│    • check_legal_review_requirements                        │ │
│    • check_security_review_requirements                     │ │
│        │ tool_result JSON                                   │ │
│        └────────────────────────────────────────────────────┘ │
│                                                              │
│  Claude synthesises all results → outputs JSON report        │
└──────────────────────────────────────────────────────────────┘
                         │ structured JSON
                         ▼
┌──────────────────────────────────────────────────────────────┐
│                Human-in-the-Loop Gate (app.py)               │
│                                                              │
│  Draft communications displayed with Approve / Revise /      │
│  Reject buttons.  No email is sent without a human click.    │
│  Agent cannot approve vendors, commit spend, or accept terms.│
└──────────────────────────────────────────────────────────────┘
```

### Key design decisions

| Decision | Choice | Rationale |
|---|---|---|
| LLM framework | Raw Anthropic SDK | Cleaner tool-use loop; no hidden abstraction |
| Policy enforcement | Deterministic Python tools | Rules are unambiguous; LLM handles synthesis and language |
| UI / deployment | Streamlit | Zero-infrastructure web app; runs locally or on Streamlit Cloud |
| HITL | Approval buttons in UI | All external actions gated; cannot be bypassed programmatically |
| Output format | Structured JSON | Parseable by downstream systems; consistent schema |

---

## How to Productionize

### 1. Data ingestion
Replace file-based case loading with an API intake form (e.g. Typeform → webhook
→ normalized JSON) or a CRM/ERP integration. Parse attached documents
server-side with a document intelligence service (e.g. AWS Textract, Azure
Document Intelligence) for robust PDF/image extraction.

### 2. Persistence
Store evaluations and tool-call traces in a database (Postgres + JSONB or
a document store).  Add an audit log table for HITL decisions (who approved,
when, what was changed).

### 3. Authentication & RBAC
Gate the UI behind SSO (SAML/OIDC).  Implement role-based access: Procurement
owners can approve; Finance can only view finance sections; Legal can comment
but not close.

### 4. Async processing
Move the agent invocation off the request thread into a job queue (Celery,
AWS SQS) so large cases don't block the UI.  Push status updates to the
frontend via WebSocket or polling.

### 5. Tool expansion
- Connect `lookup_vendor_register` to the live ERP/P2P system (Coupa, SAP Ariba).
- Connect `lookup_budget` to the live FP&A system.
- Add a `create_jira_ticket` tool for auto-routing to Legal/Security queues.
- Add a `send_email_draft` tool that fires only after human approval (HITL flag
  checked server-side, not just client-side).

### 6. LLM governance
- Pin model version; test new versions before promoting.
- Log all prompts and responses for compliance review.
- Add an evaluation harness with golden-answer test cases (the three provided
  cases make a good starting set) to catch regressions.

### 7. Monitoring & alerting
Track per-case latency, tool error rates, and approval outcomes.
Alert on blocking-issue rate spikes (may indicate a policy change is needed)
and on high agent-error rates (may indicate a model or data-quality issue).
