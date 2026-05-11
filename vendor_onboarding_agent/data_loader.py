"""
data_loader.py — Reads all documents for a vendor onboarding case.

Supported formats:
  .xlsx  → openpyxl  (intake forms)
  .csv   → stdlib csv (quotes)
  .pdf   → pypdf     (contract excerpts)
  .txt   → plain text (vendor emails)
  .md    → plain text (security questionnaires)
"""

import csv
from pathlib import Path

import openpyxl
from pypdf import PdfReader

_CASES_ROOT = Path(__file__).parent.parent / "Candidate_package" / "cases"

AVAILABLE_CASES = {
    "case_001": "Northstar Analytics — SaaS / Revenue Analytics",
    "case_002": "Workspace Depot — Office Supplies Renewal",
    "case_003": "TalentPulse AI — HR Analytics SaaS",
}


# ---------------------------------------------------------------------------
# Individual file loaders
# ---------------------------------------------------------------------------

def _read_intake(path: Path) -> str:
    """Convert the intake XLSX (key/value table) to readable text."""
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    lines = []
    header_skipped = False
    for row in ws.iter_rows(values_only=True):
        if not any(c is not None for c in row):
            continue
        # Skip the descriptive header rows (first two)
        if not header_skipped and row[1] is None:
            lines.append(str(row[0] or ""))
            continue
        # The actual column-header row
        if row[1] == "Field Key":
            header_skipped = True
            continue
        if row[0] and row[2] and row[3] is not None:
            lines.append(f"  [{row[0]}] {row[2]}: {row[3]}")
    return "\n".join(lines)


def _read_quote(path: Path) -> str:
    """Convert quote CSV to a simple text table."""
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    if not rows:
        return "(no quote data)"
    lines = []
    headers = list(rows[0].keys())
    lines.append("  " + " | ".join(headers))
    lines.append("  " + "-" * 80)
    for row in rows:
        lines.append("  " + " | ".join(str(row[h]) for h in headers))
    return "\n".join(lines)


def _read_pdf(path: Path) -> str:
    """Extract text from a PDF (contract excerpt)."""
    reader = PdfReader(str(path))
    pages = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text)
    return "\n\n".join(pages)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_case(case_id: str) -> dict:
    """
    Return a dict with keys:
      case_id, intake_text, vendor_email_text, quote_text,
      security_questionnaire_text, contract_text
    """
    base = _CASES_ROOT / case_id
    return {
        "case_id": case_id,
        "intake_text": _read_intake(base / f"{case_id}_intake.xlsx"),
        "vendor_email_text": _read_text(base / f"{case_id}_vendor_email.txt"),
        "quote_text": _read_quote(base / f"{case_id}_quote.csv"),
        "security_questionnaire_text": _read_text(base / f"{case_id}_security_questionnaire.md"),
        "contract_text": _read_pdf(base / f"{case_id}_contract.pdf"),
    }


def format_case_for_prompt(case: dict) -> str:
    """Render all case documents as a single labelled text block for the LLM."""
    return f"""
=== INTAKE FORM ===
{case['intake_text']}

=== VENDOR EMAIL ===
{case['vendor_email_text']}

=== QUOTE / ORDER FORM ===
{case['quote_text']}

=== SECURITY QUESTIONNAIRE ===
{case['security_questionnaire_text']}

=== CONTRACT EXCERPT ===
{case['contract_text']}
""".strip()
