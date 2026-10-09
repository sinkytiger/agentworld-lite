"""Finance-domain tool definitions (Claude tool-use format) and system prompt."""

from __future__ import annotations

from .env import TOOL_NAMES


def _schema(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


_STR = {"type": "string"}
_INT = {"type": "integer"}
_PERIOD = {"type": "string", "enum": ["Q1", "H1", "Q3", "FY"]}
_BASIS = {"type": "string", "enum": ["CFS", "OFS"]}

TOOL_DEFS: dict[str, dict] = {
    "fetch_financials": {
        "description": ("Fetch key figures from a DART periodic report into your notebook: revenue, operating_income, "
                        "net_income (incl. non-controlling interests), net_income_owners (attributable to owners of the parent), "
                        "equity_owners, equity_total. period: Q1 / H1 / Q3 / FY. Income-statement figures are CUMULATIVE "
                        "from the start of the fiscal year (H1 = Jan-Jun). basis: CFS = consolidated, OFS = separate. "
                        "Reports filed after the as-of date are not available. company = name or 6-digit stock code."),
        "input_schema": _schema({"company": _STR, "year": _INT, "period": _PERIOD, "basis": _BASIS}),
    },
    "fetch_shares": {
        "description": ("Fetch the number of issued shares (common, preferred) and treasury shares from a DART periodic report "
                        "into your notebook."),
        "input_schema": _schema({"company": _STR, "year": _INT, "period": _PERIOD}),
    },
    "calculate": {
        "description": ("Compute a new fact from facts in YOUR notebook. inputs binds variable names to fact ids, e.g. "
                        "[{\"var\": \"a\", \"fact_id\": \"...\"}]; formula uses only those variables, numbers, + - * / and "
                        "parentheses (e.g. \"a - b + c\"). The result is saved as calc.<name> in your notebook. "
                        "unit: KRW, shares, %, x (multiple) or another short label."),
        "input_schema": _schema({
            "name": _STR, "formula": _STR, "unit": _STR,
            "inputs": {"type": "array", "items": _schema({"var": _STR, "fact_id": _STR})},
        }),
    },
    "share_facts": {
        "description": "Copy facts from your notebook into a teammate's notebook (they can then use them).",
        "input_schema": _schema({"to_agent": _STR, "fact_ids": {"type": "array", "items": _STR}}),
    },
    "send_chat": {
        "description": "Send a chat message to the team ('all') or to one teammate username.",
        "input_schema": _schema({"to": _STR, "message": _STR}),
    },
    "submit_answer": {
        "description": ("Submit the team's answer. Each answer cites a fact id from YOUR notebook for a required field; "
                        "you cannot type numbers directly. Submitting a field again replaces it."),
        "input_schema": _schema({"answers": {"type": "array", "items": _schema({"field": _STR, "fact_id": _STR})}}),
    },
    "wait": {
        "description": "Do nothing this turn (e.g. waiting for a teammate).",
        "input_schema": _schema({"reason": _STR}),
    },
}

assert tuple(TOOL_DEFS) == TOOL_NAMES


def tool_specs(allowed: list[str] | tuple[str, ...] | None = None, strict: bool = True) -> list[dict]:
    names = [n for n in TOOL_NAMES if allowed is None or n in allowed]
    out = []
    for n in names:
        spec = {"name": n, "description": TOOL_DEFS[n]["description"], "input_schema": TOOL_DEFS[n]["input_schema"]}
        if strict:
            spec["strict"] = True
        out.append(spec)
    return out


SYSTEM_PROMPT = """You are an AI analyst on a small equity-research desk. Your team answers a question about a listed Korean company using public DART disclosures.

CRITICAL RESPONSE RULE: You MUST call exactly ONE tool function in every response. Never respond without calling a tool function.

How the desk works:
- The desk is turn-based. Each round every teammate acts once, in a fixed order. One tool call is your whole turn.
- Every number lives in a notebook as a fact with an id. You can only use facts in YOUR notebook; teammates must share_facts to you.
- Roles are limited: check which tools your role cannot use, and ask the teammate who can.
- You only see your own notebook and the team chat. Coordinate through send_chat.
- Watch for traps: consolidated (CFS) vs separate (OFS), net income vs net income attributable to owners, cumulative vs single-quarter figures, and the as-of date.
- Answers must cite fact ids; you cannot type numbers in. The round budget is limited, so act efficiently and do not send redundant messages.
- Keep any text before your tool call to at most two short sentences."""
