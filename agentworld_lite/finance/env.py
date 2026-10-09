"""Finance research-desk environment: notebooks of facts, seven tools, observation, verifier.

Agents hold facts in a notebook (an inventory of fact ids). Only facts in your own notebook can be used.
  fetch_financials / fetch_shares  create facts from filings (as-of date enforced: no look-ahead)
  calculate                        creates a derived fact from facts in your notebook
  share_facts                      copies facts into a teammate's notebook
  submit_answer                    records the team's answer as facts (cited, never typed-in numbers)
  send_chat / wait
Role limits use the same `restricted_tools` mechanism as the RPG domain.
"""

from __future__ import annotations

import ast
import inspect
import json
import operator
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ..engine import ActionResult
from .dataset import (BASES, PERIODS, Dataset, Fact, fmt_value, price_fact, share_facts as _share_facts,
                      statement_facts)

TOOL_NAMES = ("fetch_financials", "fetch_shares", "calculate", "share_facts", "send_chat", "submit_answer", "wait")
REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class DeskAgent:
    name: str
    role: str
    inventory: Counter = field(default_factory=Counter)  # fact id -> 1 (named inventory so ScriptedAgent conditions work)
    restricted_tools: frozenset = frozenset()
    equipment: dict = field(default_factory=dict)
    knocked_out: bool = False
    deaths: int = 0


# ---------------------------------------------------------------------- safe arithmetic
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.USub: operator.neg, ast.UAdd: operator.pos}


def safe_eval(formula: str, env: dict[str, float]) -> float:
    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in env:
                raise ValueError(f"unknown variable '{node.id}'")
            return env[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.operand))
        raise ValueError("only numbers, variables, + - * / and parentheses are allowed")
    return ev(ast.parse(formula, mode="eval"))


def as_list(value) -> list:
    """Small models often send nested arrays as JSON text ('["a", "b"]') or a comma-separated string."""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if text.startswith(("[", "{")):
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                try:  # Python-style literal: [{'var': 'a', ...}]
                    value = ast.literal_eval(text)
                except (ValueError, SyntaxError):
                    return [x.strip(" '\"") for x in text.strip("[]").split(",") if x.strip(" '\"")]
            if not isinstance(value, (list, dict)):
                return [value]
        else:
            return [x.strip() for x in text.split(",") if x.strip()]
    if isinstance(value, dict):
        return [value]
    return list(value)


def slug(name: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z가-힣_]+", "_", str(name).strip()).strip("_")
    return s[:60] or "value"


class FinanceEnv:
    def __init__(self, dataset: Dataset, as_of: str) -> None:
        self.dataset = dataset
        self.as_of = as_of
        self.round = 0
        self.completed_rounds = 0
        self.agents: dict[str, DeskAgent] = {}
        self.facts: dict[str, Fact] = {}
        self.submission: dict[str, str] = {}  # field -> fact id
        self.env_events: list[dict] = []

    # ------------------------------------------------------------------ setup
    def add_agent(self, spec: dict) -> DeskAgent:
        a = DeskAgent(name=spec["username"], role=spec.get("role", ""),
                      restricted_tools=frozenset(spec.get("restricted_tools") or []))
        self.agents[a.name] = a
        for f in spec.get("initial_facts") or []:
            fact = self._initial_fact(f)
            self.facts[fact.id] = fact
            a.inventory[fact.id] = 1
        return a

    def _initial_fact(self, f: dict) -> Fact:
        if f.get("price_of"):
            company = self.dataset.find(f["price_of"])
            fact = price_fact(company, f["date"]) if company else None
            if fact is None:
                raise ValueError(f"no price for {f['price_of']} on {f['date']}")
            return fact
        return Fact(f["id"], f["label"], float(f["value"]), f.get("unit", ""), {"source": f.get("source", "task")})

    # ------------------------------------------------------------------ dispatch
    def execute(self, agent_name: str, tool: str, args: dict | None) -> ActionResult:
        agent = self.agents[agent_name]
        args = dict(args or {})
        if tool not in TOOL_NAMES:
            return ActionResult(False, f"Unknown tool '{tool}'.")
        if tool in agent.restricted_tools:
            return ActionResult(False, f"Your role cannot use {tool}. Ask a teammate who can.")
        handler = getattr(self, f"_t_{tool}")
        params = inspect.signature(handler).parameters
        call_args = {k: v for k, v in args.items() if k in params}
        missing = [p for p, v in params.items() if p != "agent" and v.default is inspect._empty and p not in call_args]
        if missing:
            return ActionResult(False, f"Missing argument(s) for {tool}: {', '.join(missing)}.")
        before = {n: Counter(a.inventory) for n, a in self.agents.items()}
        try:
            ok, msg, extra = handler(agent, **call_args)
        except (TypeError, ValueError) as exc:
            return ActionResult(False, f"Invalid arguments for {tool}: {exc}")
        inv = {}
        for n, a in self.agents.items():
            delta = {k: a.inventory[k] - before[n][k] for k in set(a.inventory) | set(before[n]) if a.inventory[k] != before[n][k]}
            if delta:
                inv[n] = delta
        effects = {"inv": inv} if inv else {}
        effects.update(extra)
        return ActionResult(ok, msg, effects)

    def _company(self, company: str):
        c = self.dataset.find(company)
        if c is None:
            names = ", ".join(f"{x.name}({x.stock_code})" for x in self.dataset.companies.values())
            raise ValueError(f"unknown company '{company}'. Available: {names}")
        return c

    def _filed_ok(self, filed: str | None) -> bool:
        return filed is None or filed <= self.as_of

    def _filed_list(self, company, key: str) -> str:
        """Reports of `company` filed by the as-of date, as on DART's filing search (key: statements | shares)."""
        found: dict[tuple, set] = {}
        for k, v in company.data.get(key, {}).items():
            if self._filed_ok(v.get("filed")):
                year, period, *basis = k.split(":")
                found.setdefault((v.get("filed") or "", year, period), set()).update(basis)
        items = [f"{y} {p}" + (f" ({'/'.join(sorted(b))})" if b else "") for (_, y, p), b in sorted(found.items())]
        return ", ".join(items) or "none"

    def _give(self, agent: DeskAgent, facts: list[Fact]) -> list[Fact]:
        for f in facts:
            self.facts[f.id] = f
            agent.inventory[f.id] = 1
        return facts

    # ------------------------------------------------------------------ tools
    def _t_fetch_financials(self, agent, company, year, period, basis):
        c = self._company(company)
        year, period, basis = int(year), str(period).upper(), str(basis).upper()
        if period not in PERIODS:
            return False, ("period must be Q1 (Jan-Mar), H1 (Jan-Jun), Q3 (Jan-Sep) or FY (full year); "
                           "income-statement figures are cumulative."), {}
        if basis not in BASES:
            return False, "basis must be CFS (consolidated) or OFS (separate).", {}
        facts = statement_facts(c, year, period, basis)
        if not facts:
            return False, (f"No {year} {period} {basis} report for {c.name}. "
                           f"Reports filed by {self.as_of}: {self._filed_list(c, 'statements')}."), {}
        filed = facts[0].meta.get("filed")
        if not self._filed_ok(filed):
            return False, f"The {year} {period} report of {c.name} was filed on {filed}, after the as-of date {self.as_of}.", {}
        self._give(agent, facts)
        lines = [f"{f.id} = {fmt_value(f.value, f.unit)}" for f in facts]
        return True, f"Fetched {len(facts)} facts from DART (filed {filed}):\n" + "\n".join(lines), {}

    def _t_fetch_shares(self, agent, company, year, period):
        c = self._company(company)
        year, period = int(year), str(period).upper()
        facts = _share_facts(c, year, period)
        if not facts:
            return False, (f"No share-count data for {c.name} {year} {period}. "
                           f"Available as of {self.as_of}: {self._filed_list(c, 'shares')}."), {}
        filed = facts[0].meta.get("filed")
        if not self._filed_ok(filed):
            return False, f"That report was filed on {filed}, after the as-of date {self.as_of}.", {}
        self._give(agent, facts)
        return True, "Fetched share counts:\n" + "\n".join(f"{f.id} = {fmt_value(f.value, f.unit)}" for f in facts), {}

    def _t_calculate(self, agent, name, formula, inputs, unit):
        values, used = {}, []
        for item in as_list(inputs):
            if not isinstance(item, dict):
                return False, "inputs must be a list of {var, fact_id} objects.", {}
            var, fid = str(item.get("var", "")).strip(), str(item.get("fact_id", "")).strip()
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", var):
                return False, f"Invalid variable name '{var}' (use letters, digits, underscore).", {}
            if agent.inventory[fid] <= 0:
                return False, f"Fact '{fid}' is not in your notebook. Ask a teammate to share it.", {}
            values[var] = self.facts[fid].value
            used.append(fid)
        try:
            result = safe_eval(str(formula), values)
        except ZeroDivisionError:
            return False, "Division by zero.", {}
        # A fact id never changes value: a different result under the same name gets a new id (calc.x_2, ...).
        base = fid = f"calc.{slug(name)}"
        k = 1
        while fid in self.facts and not (self.facts[fid].value == result and agent.inventory[fid] > 0):
            k += 1
            fid = f"{base}_{k}"
        fact = Fact(fid, str(name), result, str(unit),
                    {"source": "calculation", "formula": str(formula), "inputs": {v: f for v, f in zip(values, used)},
                     "by": agent.name})
        self._give(agent, [fact])
        return True, f"Computed {fid} = {fmt_value(result, fact.unit)} ({formula}).", {"uses": {agent.name: used}}

    def _t_share_facts(self, agent, to_agent, fact_ids):
        other = self.agents.get(str(to_agent).strip())
        if other is None or other is agent:
            return False, f"'{to_agent}' is not a teammate. Teammates: {', '.join(n for n in self.agents if n != agent.name)}.", {}
        ids = [str(x).strip() for x in as_list(fact_ids) if str(x).strip()]
        missing = [x for x in ids if agent.inventory[x] <= 0]
        if not ids or missing:
            return False, f"Not in your notebook: {', '.join(missing) or '(no ids given)'}.", {}
        for x in ids:
            other.inventory[x] = 1
        return True, f"Shared {len(ids)} fact(s) with {other.name}.", {"uses": {agent.name: ids}}

    def _t_send_chat(self, agent, to, message):
        to = str(to).strip() or "all"
        message = str(message).strip()[:600]
        if not message:
            return False, "Empty message.", {}
        if to != "all" and to not in self.agents:
            return False, f"Unknown recipient '{to}'. Use 'all' or a teammate username.", {}
        return True, f"Message sent to {to}.", {"chat": {"to": to, "text": message}}

    def _t_submit_answer(self, agent, answers):
        recorded, used = [], []
        for item in as_list(answers):
            if not isinstance(item, dict):
                return False, "answers must be a list of {field, fact_id} objects.", {}
            fld, fid = str(item.get("field", "")).strip(), str(item.get("fact_id", "")).strip()
            if not fld:
                continue
            if agent.inventory[fid] <= 0:
                return False, f"Fact '{fid}' is not in your notebook; answers must cite facts you hold.", {}
            self.submission[fld] = fid
            recorded.append(f"{fld} = {fid} ({fmt_value(self.facts[fid].value, self.facts[fid].unit)})")
            used.append(fid)
        if not recorded:
            return False, "No answers given. Use a list of {field, fact_id}.", {}
        return True, "Recorded answers:\n" + "\n".join(recorded), {"uses": {agent.name: used}}

    def _t_wait(self, agent, reason=""):
        return True, "Waited." + (f" ({reason})" if reason else ""), {}

    # ------------------------------------------------------------------ protocol hooks
    def end_round(self) -> None:
        self.completed_rounds = self.round

    def snapshot(self) -> dict:
        return {
            "round": self.round,
            # same shape as the RPG snapshot so the CCE rule judge can seed fact provenance from it
            "agents": {n: {"inventory": {k: v for k, v in sorted(a.inventory.items()) if v}} for n, a in self.agents.items()},
            "submission": dict(self.submission),
        }


def build_env(task, seed: int = 7) -> FinanceEnv:
    data = task.meta.get("data") or {}
    directory = Path(data.get("dir", "data/finance"))
    if not directory.is_absolute():
        directory = REPO_ROOT / directory
    env = FinanceEnv(Dataset(directory), as_of=str((task.meta.get("inputs") or {}).get("as_of", "9999-12-31")))
    for spec in task.agents:
        env.add_agent(spec)
    return env


# ---------------------------------------------------------------------- observation
def render_observation(env: FinanceEnv, agent_name: str, *, max_rounds: int, history: list[dict],
                       chat_log: list[dict], seen_chat: int, whitebox: bool = False,
                       last_actions: dict | None = None) -> str:
    a = env.agents[agent_name]
    lines = [f"=== Round {env.round} / {max_rounds} ===  (as-of date: {env.as_of})",
             f"You: {a.name} (role: {a.role})"]
    if a.restricted_tools:
        lines.append(f"Your role CANNOT use: {', '.join(sorted(a.restricted_tools))}")
    lines.append("")
    held = [env.facts[k] for k, v in sorted(a.inventory.items()) if v]
    if held:
        lines.append(f"Your notebook ({len(held)} facts; cite them by id):")
        for f in held:
            lines.append(f"  - {f.id} = {fmt_value(f.value, f.unit)} | {f.label}")
    else:
        lines.append("Your notebook: (empty)")
    if env.submission:
        lines.append("Team answers submitted so far: " + ", ".join(f"{k} -> {v}" for k, v in env.submission.items()))
    if whitebox:
        lines.append("")
        lines.append("Team notebooks (oracle view):")
        for o in env.agents.values():
            if o is not a:
                ids = sorted(k for k, v in o.inventory.items() if v)
                lines.append(f"  {o.name} ({o.role}): {', '.join(ids) or '(empty)'} | last: {(last_actions or {}).get(o.name, '-')}")
    lines.append("")
    if chat_log:
        window = chat_log[-20:]
        start = len(chat_log) - len(window)
        lines.append(f"Chat log (latest {len(window)} of {len(chat_log)}; * = new since your last turn):")
        for i, m in enumerate(window, start=start):
            mark = "*" if i >= seen_chat and m["from"] != agent_name else " "
            lines.append(f" {mark}[R{m['round']}] {m['from']} -> {m['to']}: {m['text']}")
    else:
        lines.append("Chat log: (no messages yet)")
    lines.append("")
    if history:
        lines.append("Your recent actions:")
        for h in history[-8:]:
            lines.append(f"  [R{h['round']}] {h['call']} -> {'OK' if h['ok'] else 'FAILED'}: {h['message'][:300]}")
    else:
        lines.append("Your recent actions: (none yet)")
    lines.append("")
    lines.append("Choose exactly ONE tool call for this turn.")
    return "\n".join(lines)


# ---------------------------------------------------------------------- verifier
def evaluate(env: FinanceEnv, spec: dict) -> dict:
    """Checkpoint type `answer`: the submitted fact for `field` must be within rel_tol of `expected`."""
    cps = []
    for cp in spec["checkpoints"]:
        if cp["type"] != "answer":
            raise ValueError(f"unknown finance checkpoint type {cp['type']!r}")
        fid = env.submission.get(cp["field"])
        got = env.facts[fid].value if fid in env.facts else None
        tol = float(cp.get("rel_tol", 0.005))
        expected = float(cp["expected"])
        done = got is not None and abs(got - expected) <= tol * max(abs(expected), 1e-9)
        cps.append({"name": cp.get("name") or cp["field"], "have": int(done), "need": 1, "done": done,
                    "bool": True, "frac": 1.0 if done else 0.0})
    success = all(c["done"] for c in cps)
    psr = sum(c["frac"] for c in cps) / len(cps) if cps else float(success)
    message = ", ".join(f"{c['name']}: {c['done']}" for c in cps)
    return {"success": success, "psr": psr, "checkpoints": cps, "message": message}
