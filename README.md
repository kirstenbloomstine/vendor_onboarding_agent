# Vendor Onboarding Agent

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

### 2. Get a Groq API key

Get a free key at https://console.groq.com and paste it into the Streamlit sidebar when the app loads.

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
├── agent.py          # Groq tool-use agent loop + system prompt
├── tools.py          # Deterministic policy tools (finance, legal, security checks)
├── data_loader.py    # Reads .xlsx, .csv, .pdf, .txt, .md case files
├── requirements.txt
└── README.md

../Candidate_package/
├── cases/            # Three synthetic vendor onboarding cases
├── docs/             # Six internal policy documents (injected into first user message)
└── tools/            # vendor_register.csv, budget_lookup.csv
```

---

## Architecture Note

This design could be approached a few ways. In a scenario with complex, evolving, policy, a RAG pipeline where all policy docs are embedded into ChromaDB would be a better approach. However, given the size of this dataset, adding the needed information into the prompt worked better.

In the agent loop, each tool is called sequentially. While it could be justified to have the LLM decide to skip certain tool calls based on findings, for auditability and transparency here, it's called tool-by-tool.

## Production

To productionalize this, I would consider the RAG design element depending on future policy changes.

In terms of data, I would connect the vendor data to the system of record (Procurement system, etc) to ensure current data is being used. Similarly for budget lookup.

I would ensure robust testing is done with cases that had already been evaluated by sourcing, to see if the agent produced the same results.

I'd also need to use a production LLM with higher rate limits and ensure failure handling and retry mechanisms were in place. Similarly, an audit trail is important. In the case of an incorrect approval, it's important to see what tools were called and what was missed.

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
│  Policies injected into first user message (keeps system     │
│  prompt short to prevent Llama XML-format fallback)          │
│                                                              │
│  Groq / llama-3.3-70b-versatile ◄─────────────────────────┐ │
│        │ tool_call request (OpenAI-compatible JSON)         │ │
│        ▼                                                    │ │
│  dispatch_tool() ──► tools.py (deterministic)               │ │
│    • lookup_vendor_register   (vendor_register.csv)         │ │
│    • lookup_budget            (budget_lookup.csv)           │ │
│    • calculate_total_contract_value                         │ │
│    • check_finance_approval_requirements                    │ │
│    • check_legal_review_requirements                        │ │
│    • check_security_review_requirements                     │ │
│        │ tool_result JSON                                   │ │
│        └───────────────────────────────────────────────────┘ │
│                                                              │
│  Groq synthesises all results → outputs JSON report          │
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

Note: This project produced with the help of Claude Code.

