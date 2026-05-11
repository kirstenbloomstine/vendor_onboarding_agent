"""
agent.py — Vendor Onboarding Agent using Groq (OpenAI-compatible tool-use API).

Free tier: get a key at https://console.groq.com
Default model: llama-3.3-70b-versatile
  — Groq's recommended replacement for the decommissioned tool-use-preview
    models; superior tool use per Groq deprecation docs.
    The system prompt is kept short (~660 tokens) so the model stays in
    structured JSON tool-call mode rather than reverting to Llama's native
    XML format.
"""

import json
import re
from pathlib import Path

from groq import Groq

from tools import dispatch_tool

# ---------------------------------------------------------------------------
# Load policy documents
# Kept SEPARATE from the system prompt so the system prompt stays short
# (prevents the model from reverting to Llama's native XML function-call format).
# ---------------------------------------------------------------------------

_DOCS_ROOT = Path(__file__).parent.parent / "Candidate_package" / "docs"


def _load_policies() -> str:
    sections = []
    for md_file in sorted(_DOCS_ROOT.glob("*.md")):
        content = md_file.read_text(encoding="utf-8")
        sections.append(f"### {md_file.stem.replace('_', ' ').title()}\n\n{content}")
    return "\n\n---\n\n".join(sections)


POLICIES_BLOCK = _load_policies()


# ---------------------------------------------------------------------------
# System prompt  (instructions + output schema ONLY — no policy docs here)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are the Vendor Onboarding Agent for Example Company, Inc.
Review a vendor onboarding package and produce a structured procurement recommendation.

HARD CONSTRAINTS:
- Do NOT approve a vendor, commit spend, or accept contract terms.
- Label all draft communications [DRAFT - REQUIRES HUMAN APPROVAL].
- Do NOT bypass Legal, Security, Finance, Procurement, or Executive approvals.
- Do NOT make final security or privacy decisions.
- If a duplicate vendor is found, flag for human review only.
- If required intake fields are missing, flag them; do not mark as ready for routing.

YOU MAY:
- Summarize the intake and identify missing information.
- Call the provided tools to look up vendor register, budget, TCV, and policy requirements.
- Recommend approval routing and draft internal/vendor messages (drafts only).

TOOL CALL ORDER:
1. lookup_vendor_register
2. lookup_budget
3. calculate_total_contract_value
4. check_finance_approval_requirements
5. check_legal_review_requirements
6. check_security_review_requirements
7. Output the final JSON report.

OUTPUT FORMAT — after all tool calls, return exactly this JSON inside ```json ... ```:

```json
{
  "case_id": "...",
  "vendor_name": "...",
  "vendor_category": "...",
  "evaluation_status": "blocked | needs_info | ready_for_routing",
  "risk_tier": "low | medium | high",
  "summary": "2-4 sentence plain-English summary",
  "commercial_terms": {
    "annual_contract_value": 0,
    "total_contract_value": 0,
    "contract_term_months": 0,
    "payment_terms": "...",
    "one_time_fees": 0,
    "requested_start_date": "..."
  },
  "duplicate_vendor_check": {"status": "clear | renewal | potential_duplicate", "detail": "..."},
  "budget_check": {"cost_center": "...", "budget_remaining": 0, "acv": 0, "budget_sufficient": true},
  "intake_completeness": {"complete": true, "missing_fields": []},
  "missing_documents": [],
  "policy_flags": [{"policy": "...", "finding": "...", "severity": "info | warning | blocking"}],
  "approval_routing": {
    "business_owner":    {"required": true,  "name": "...", "reason": "..."},
    "procurement_manager":{"required": false, "reason": "..."},
    "vp_finance":        {"required": false, "reason": "..."},
    "cfo":               {"required": false, "reason": "..."},
    "executive_sponsor": {"required": false, "reason": "..."},
    "legal":             {"required": false, "reason": "..."},
    "security":          {"required": false, "reason": "..."}
  },
  "blocking_issues": [],
  "draft_vendor_follow_up": "[DRAFT - REQUIRES HUMAN APPROVAL]\\n\\n...",
  "draft_internal_ticket": "...",
  "agent_notes": "..."
}
```"""

# ---------------------------------------------------------------------------
# Tool definitions (OpenAI-compatible function-calling schema)
# ---------------------------------------------------------------------------

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "lookup_vendor_register",
            "description": (
                "Check the company vendor register for an exact or fuzzy match. "
                "Use first to detect duplicates before treating as a new vendor."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "vendor_name": {"type": "string", "description": "Vendor name from intake"}
                },
                "required": ["vendor_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_budget",
            "description": "Retrieve available budget and owner for a cost center.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cost_center": {"type": "string", "description": "Cost center code, e.g. REVOPS-042"}
                },
                "required": ["cost_center"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_total_contract_value",
            "description": "Calculate TCV = ACV x term_months / 12. Run before finance and legal checks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "annual_contract_value": {"type": "number"},
                    "contract_term_months": {"type": "integer"},
                    "one_time_fees": {"type": "number", "description": "Default 0"},
                },
                "required": ["annual_contract_value", "contract_term_months"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_finance_approval_requirements",
            "description": (
                "Apply Finance Approval Matrix: required approvers + flags for "
                "budget shortfall, payment terms, multi-year commitment."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "annual_contract_value": {"type": "number"},
                    "total_contract_value": {"type": "number"},
                    "budget_remaining": {"type": "number"},
                    "payment_terms": {"type": "string"},
                    "contract_term_months": {"type": "integer"},
                },
                "required": [
                    "annual_contract_value", "total_contract_value",
                    "budget_remaining", "payment_terms", "contract_term_months",
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_legal_review_requirements",
            "description": (
                "Apply Legal Review Policy: returns whether legal review is required and why."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "annual_contract_value": {"type": "number"},
                    "total_contract_value": {"type": "number"},
                    "contract_term_months": {"type": "integer"},
                    "payment_terms": {"type": "string"},
                    "processes_personal_data": {"type": "boolean"},
                    "processes_confidential_data": {"type": "boolean"},
                    "has_non_us_subprocessors": {"type": "boolean"},
                    "non_standard_clauses": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "ai_training_data_use": {"type": "boolean"},
                },
                "required": [
                    "annual_contract_value", "total_contract_value", "contract_term_months",
                    "payment_terms", "processes_personal_data", "processes_confidential_data",
                    "has_non_us_subprocessors", "ai_training_data_use",
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_security_review_requirements",
            "description": (
                "Apply Security Review Policy: returns risk tier and any blocking issues."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "vendor_category": {"type": "string"},
                    "data_types": {"type": "array", "items": {"type": "string"}},
                    "system_integrations": {"type": "array", "items": {"type": "string"}},
                    "annual_contract_value": {"type": "number"},
                    "soc2_type2_available": {"type": "boolean"},
                    "ai_functionality": {"type": "boolean"},
                    "has_non_us_subprocessors": {"type": "boolean"},
                },
                "required": [
                    "vendor_category", "data_types", "system_integrations",
                    "annual_contract_value", "soc2_type2_available",
                    "ai_functionality", "has_non_us_subprocessors",
                ],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------

def run_agent(
    case_documents: str,
    *,
    api_key: str,
    stream_callback=None,
    model: str = "llama-3.3-70b-versatile",
) -> dict:
    """
    Run the vendor onboarding agent for a single case.

    Parameters
    ----------
    case_documents : str
        All case documents formatted as a single text block.
    api_key : str
        Groq API key (passed explicitly from the UI).
    stream_callback : callable | None
        Optional (event_type, data) callback for UI progress updates.
        event_type: 'tool_call' | 'tool_result' | 'text' | 'done'
    model : str
        Groq model ID. Default is the tool-use fine-tuned model.

    Returns
    -------
    dict  {"report": dict, "raw_response": str, "tool_calls": list}
    """
    client = Groq(api_key=api_key)

    # Policies are injected into the first USER message (not the system prompt)
    # to keep the system prompt short and prevent the model from reverting to
    # Llama's native XML function-call format.
    first_user_content = (
        "## Internal Policies (use as authoritative reference)\n\n"
        + POLICIES_BLOCK
        + "\n\n---\n\n"
        "## Vendor Onboarding Package to Evaluate\n\n"
        + case_documents
        + "\n\n---\n\n"
        "Please evaluate this package using your tools and produce the JSON report."
    )

    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": first_user_content},
    ]

    tool_call_log: list[dict] = []
    final_text = ""

    # Agentic loop
    while True:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            max_tokens=4096,
        )

        message = response.choices[0].message
        messages.append(message)

        if message.content and stream_callback:
            stream_callback("text", message.content)

        # No tool calls — model is done
        if not message.tool_calls:
            final_text = message.content or ""
            break

        # Execute each tool call
        for tc in message.tool_calls:
            tool_name = tc.function.name
            tool_input = json.loads(tc.function.arguments)

            if stream_callback:
                stream_callback("tool_call", {"name": tool_name, "input": tool_input})

            result = dispatch_tool(tool_name, tool_input)
            tool_call_log.append({"tool": tool_name, "input": tool_input, "result": result})

            if stream_callback:
                stream_callback("tool_result", {"name": tool_name, "result": result})

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result, default=str),
            })

    if stream_callback:
        stream_callback("done", {})

    report = _parse_json_report(final_text)
    return {"report": report, "raw_response": final_text, "tool_calls": tool_call_log}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_json_report(text: str) -> dict:
    """Extract the JSON block from the agent's final text response."""
    match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    return {
        "evaluation_status": "error",
        "error": "Could not parse structured JSON from agent response.",
        "raw": text,
    }
