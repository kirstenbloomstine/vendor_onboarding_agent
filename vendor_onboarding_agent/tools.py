"""
tools.py — Deterministic tool implementations for the Vendor Onboarding Agent.

Each function is registered as an Anthropic tool and called when the LLM
requests it.  All logic is rule-based (no LLM calls here).
"""

import csv
from difflib import SequenceMatcher
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths (relative to this file, which lives next to Candidate_package/)
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).parent.parent / "Candidate_package" / "tools"
VENDOR_REGISTER_PATH = _ROOT / "vendor_register.csv"
BUDGET_LOOKUP_PATH = _ROOT / "budget_lookup.csv"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_vendor_register() -> list[dict]:
    with open(VENDOR_REGISTER_PATH, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _load_budget() -> dict[str, dict]:
    with open(BUDGET_LOOKUP_PATH, newline="", encoding="utf-8") as f:
        return {row["cost_center"]: row for row in csv.DictReader(f)}


def _parse_net_days(payment_terms: str) -> int | None:
    """Parse 'Net 30' → 30, returns None if unparseable."""
    try:
        return int(payment_terms.lower().replace("net", "").strip())
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Tool 1 — Vendor Register Lookup
# ---------------------------------------------------------------------------

def lookup_vendor_register(vendor_name: str) -> dict:
    """
    Check the vendor register for an exact or fuzzy match.
    Returns match details and a flag if a likely duplicate is found.
    """
    vendors = _load_vendor_register()
    name_lower = vendor_name.lower()

    exact = next(
        (v for v in vendors if v["vendor_name"].lower() == name_lower), None
    )
    if exact:
        return {
            "found": True,
            "match_type": "exact",
            "vendor": exact,
            "flag": f"Vendor '{vendor_name}' already exists (status: {exact['status']}). "
                    "For renewals confirm status; for new requests flag as potential duplicate.",
        }

    fuzzy = [
        {**v, "similarity": round(SequenceMatcher(None, v["vendor_name"].lower(), name_lower).ratio(), 2)}
        for v in vendors
        if SequenceMatcher(None, v["vendor_name"].lower(), name_lower).ratio() > 0.6
    ]
    if fuzzy:
        return {
            "found": True,
            "match_type": "fuzzy",
            "possible_matches": fuzzy,
            "flag": "Possible duplicate vendor(s) found. Human review required before creating a new record.",
        }

    return {
        "found": False,
        "match_type": "none",
        "flag": f"No existing vendor found for '{vendor_name}'. Safe to proceed as a new vendor.",
    }


# ---------------------------------------------------------------------------
# Tool 2 — Budget Lookup
# ---------------------------------------------------------------------------

def lookup_budget(cost_center: str) -> dict:
    """Return available budget and owner for a cost center."""
    budgets = _load_budget()
    if cost_center not in budgets:
        return {
            "found": False,
            "cost_center": cost_center,
            "flag": "Cost center not found. Finance/FP&A review required before approval.",
        }
    row = budgets[cost_center]
    return {
        "found": True,
        "cost_center": cost_center,
        "department": row["department"],
        "annual_budget_remaining": float(row["annual_budget_remaining"]),
        "budget_owner": row["budget_owner"],
    }


# ---------------------------------------------------------------------------
# Tool 3 — Total Contract Value
# ---------------------------------------------------------------------------

def calculate_total_contract_value(
    annual_contract_value: float,
    contract_term_months: int,
    one_time_fees: float = 0.0,
) -> dict:
    """
    TCV = ACV × term_months / 12.
    One-time fees are reported separately.
    """
    tcv = annual_contract_value * contract_term_months / 12
    return {
        "annual_contract_value": annual_contract_value,
        "contract_term_months": contract_term_months,
        "total_contract_value": tcv,
        "one_time_fees": one_time_fees,
        "total_spend_including_one_time": tcv + one_time_fees,
    }


# ---------------------------------------------------------------------------
# Tool 4 — Finance Approval Requirements
# ---------------------------------------------------------------------------

def check_finance_approval_requirements(
    annual_contract_value: float,
    total_contract_value: float,
    budget_remaining: float,
    payment_terms: str,
    contract_term_months: int,
) -> dict:
    """
    Apply the Finance Approval Matrix to determine required approvers and flags.

    Rules (from finance_approval_matrix.md):
      ACV $0–25K        → business_owner
      ACV >25K–50K      → + procurement_manager
      ACV >50K–100K     → + vp_finance
      ACV >100K–250K    → + cfo
      ACV >250K or TCV >250K → + executive_sponsor
      Budget < ACV      → finance approval required regardless
      Net 45            → procurement_manager review
      Net 60            → vp_finance review
      Net >60           → vp_finance + legal
      Term >24 months   → Finance review (multi-year commitment)
    """
    approvers: list[str] = ["business_owner"]
    flags: list[str] = []

    # ACV thresholds
    if annual_contract_value > 25_000:
        approvers.append("procurement_manager")
    if annual_contract_value > 50_000:
        approvers.append("vp_finance")
    if annual_contract_value > 100_000:
        approvers.append("cfo")
    if annual_contract_value > 250_000 or total_contract_value > 250_000:
        approvers.append("executive_sponsor")

    # Budget shortfall
    if budget_remaining < annual_contract_value:
        flags.append(
            f"BUDGET SHORTFALL: remaining ${budget_remaining:,.0f} < ACV ${annual_contract_value:,.0f}. "
            "Finance approval required regardless of ACV threshold."
        )
        if "vp_finance" not in approvers:
            approvers.append("vp_finance")

    # Payment terms
    net_days = _parse_net_days(payment_terms)
    if net_days is None:
        flags.append(f"Non-standard payment terms '{payment_terms}'. Finance and Legal review required.")
        for r in ("vp_finance", "legal"):
            if r not in approvers:
                approvers.append(r)
    elif net_days == 45:
        flags.append("Net 45 requires Procurement manager review.")
        if "procurement_manager" not in approvers:
            approvers.append("procurement_manager")
    elif net_days == 60:
        flags.append("Net 60 requires VP Finance review.")
        if "vp_finance" not in approvers:
            approvers.append("vp_finance")
    elif net_days > 60:
        flags.append(f"{payment_terms} (>Net 60) requires VP Finance and Legal review.")
        for r in ("vp_finance", "legal"):
            if r not in approvers:
                approvers.append(r)

    # Multi-year contract
    if contract_term_months > 24:
        flags.append(
            f"Contract term {contract_term_months} months (>24) creates a multi-year commitment. Finance review required."
        )
        if "vp_finance" not in approvers:
            approvers.append("vp_finance")

    return {"required_approvers": approvers, "flags": flags}


# ---------------------------------------------------------------------------
# Tool 5 — Legal Review Requirements
# ---------------------------------------------------------------------------

def check_legal_review_requirements(
    annual_contract_value: float,
    total_contract_value: float,
    contract_term_months: int,
    payment_terms: str,
    processes_personal_data: bool,
    processes_confidential_data: bool,
    has_non_us_subprocessors: bool,
    non_standard_clauses: list[str],
    ai_training_data_use: bool,
) -> dict:
    """
    Apply the Legal Review Policy to determine whether Legal review is required.

    Triggers (from legal_review_policy.md):
      ACV > $50K | TCV > $100K | term > 12 months | payment > Net 60
      Processes personal or confidential data
      Non-US subprocessors
      Non-standard contract clauses
      Vendor can use company data for AI training / product improvement
    """
    reasons: list[str] = []

    if annual_contract_value > 50_000:
        reasons.append(f"ACV ${annual_contract_value:,.0f} exceeds $50,000 Legal threshold")
    if total_contract_value > 100_000:
        reasons.append(f"TCV ${total_contract_value:,.0f} exceeds $100,000 Legal threshold")
    if contract_term_months > 12:
        reasons.append(f"Contract term {contract_term_months} months exceeds 12-month threshold")

    net_days = _parse_net_days(payment_terms)
    if net_days is None:
        reasons.append(f"Non-standard payment terms: {payment_terms}")
    elif net_days > 60:
        reasons.append(f"Payment terms {payment_terms} exceed Net 60")

    if processes_personal_data:
        reasons.append(
            "Vendor processes personal data — DPA, subprocessor list, breach notice, and retention terms required"
        )
    if processes_confidential_data:
        reasons.append("Vendor processes confidential business data")
    if has_non_us_subprocessors:
        reasons.append(
            "Vendor uses subprocessors outside the United States — cross-border data processing review required"
        )
    for clause in non_standard_clauses or []:
        reasons.append(f"Non-standard clause: {clause}")
    if ai_training_data_use:
        reasons.append(
            "Vendor contract permits use of company/customer/employee data for model training or "
            "service improvement — Legal and executive approval required before proceeding"
        )

    return {
        "legal_review_required": bool(reasons),
        "reasons": reasons,
    }


# ---------------------------------------------------------------------------
# Tool 6 — Security Review Requirements
# ---------------------------------------------------------------------------

def check_security_review_requirements(
    vendor_category: str,
    data_types: list[str],
    system_integrations: list[str],
    annual_contract_value: float,
    soc2_type2_available: bool,
    ai_functionality: bool,
    has_non_us_subprocessors: bool,
) -> dict:
    """
    Apply the Security Review Policy to determine risk tier and blocking issues.

    High-risk triggers (from security_review_policy.md):
      - Processes customer PII or employee sensitive data
      - Integrates with HRIS, finance, data warehouse, identity, or production systems
      - Uses AI/ML on company/customer/employee data
      - Non-US subprocessors without prior review
      - Cannot provide SOC 2 Type II (when medium/high risk)
      - Incomplete or inconsistent security questionnaire answers

    Blocking issues (agent cannot recommend ready for approval):
      - Security questionnaire missing or incomplete
      - Restricted data processing with no retention/deletion explanation
      - AI training on company data without explicit approval
    """
    HIGH_RISK_DATA = {
        "customer personal information", "employee personal information",
        "employee names", "employee emails", "employee email addresses",
        "engagement survey responses", "performance rating", "performance ratings",
        "salary band", "salary bands", "attrition risk", "attrition risk score",
        "authentication credentials", "financial account data", "production data",
    }
    HIGH_RISK_SYSTEMS = {"hris", "finance", "data warehouse", "identity", "production"}
    MEDIUM_RISK_SYSTEMS = {"crm", "salesforce", "snowflake", "slack", "collaboration"}

    data_lower = {d.lower() for d in (data_types or [])}
    systems_lower = {s.lower() for s in (system_integrations or [])}

    high_risk_conditions: list[str] = []
    medium_risk_conditions: list[str] = []
    blocking_issues: list[str] = []

    # Data sensitivity
    sensitive_data = bool(
        data_lower & HIGH_RISK_DATA
        or any(h in d for d in data_lower for h in HIGH_RISK_DATA)
    )
    if sensitive_data:
        high_risk_conditions.append(
            "Processes customer PII or employee sensitive data "
            f"({', '.join(d for d in data_lower if any(h in d for h in HIGH_RISK_DATA))})"
        )

    # System integrations
    matched_high = systems_lower & HIGH_RISK_SYSTEMS
    matched_medium = systems_lower & MEDIUM_RISK_SYSTEMS
    if matched_high:
        high_risk_conditions.append(
            f"Integrates with high-risk system(s): {', '.join(matched_high)}"
        )
    if matched_medium:
        medium_risk_conditions.append(
            f"Integrates with medium-risk system(s): {', '.join(matched_medium)}"
        )

    # AI / ML
    if ai_functionality and sensitive_data:
        high_risk_conditions.append(
            "Uses AI/ML on company, customer, or employee data — elevated risk"
        )

    # Non-US subprocessors
    if has_non_us_subprocessors:
        high_risk_conditions.append(
            "Uses subprocessors outside the United States without prior review"
        )

    # SOC 2
    review_required = bool(high_risk_conditions or medium_risk_conditions)
    if not soc2_type2_available and review_required:
        high_risk_conditions.append(
            "Cannot provide SOC 2 Type II report (required for medium/high risk vendors)"
        )
        blocking_issues.append(
            "SOC 2 Type II not provided - vendor cannot be recommended ready for approval until resolved"
        )

    # AI training blocking
    if ai_functionality and sensitive_data:
        blocking_issues.append(
            "Vendor may use employee/customer data for AI model training — "
            "explicit executive and Legal approval required; not included in current quote"
        )

    # Risk tier
    if high_risk_conditions:
        risk_tier = "high"
    elif medium_risk_conditions:
        risk_tier = "medium"
    else:
        risk_tier = "low"

    # Low-risk office/facilities short-circuit
    low_risk_categories = {"office supplies", "facilities", "event"}
    if (
        vendor_category.lower() in low_risk_categories
        and not data_lower
        and not systems_lower
        and annual_contract_value < 25_000
    ):
        return {
            "security_review_required": False,
            "risk_tier": "low",
            "high_risk_conditions": [],
            "medium_risk_conditions": [],
            "blocking_issues": [],
            "note": "Low-risk operational vendor with no system access or sensitive data — security review not required",
        }

    return {
        "security_review_required": review_required or risk_tier != "low",
        "risk_tier": risk_tier,
        "high_risk_conditions": high_risk_conditions,
        "medium_risk_conditions": medium_risk_conditions,
        "blocking_issues": blocking_issues,
    }


# ---------------------------------------------------------------------------
# Tool dispatch (used by the agent loop)
# ---------------------------------------------------------------------------

TOOL_FUNCTIONS = {
    "lookup_vendor_register": lookup_vendor_register,
    "lookup_budget": lookup_budget,
    "calculate_total_contract_value": calculate_total_contract_value,
    "check_finance_approval_requirements": check_finance_approval_requirements,
    "check_legal_review_requirements": check_legal_review_requirements,
    "check_security_review_requirements": check_security_review_requirements,
}


def dispatch_tool(name: str, inputs: dict) -> dict:
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        return {"error": f"Unknown tool: {name}"}
    return fn(**inputs)
