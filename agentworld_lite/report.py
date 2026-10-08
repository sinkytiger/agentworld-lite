"""Aggregate trajectories (+ evaluations) into the paper's result tables, as Markdown."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from statistics import mean

from .cce import to_mermaid
from .failures import CATEGORIES
from .task import CATEGORIES as TASK_CATEGORIES


def load_run(run_dir: str | Path) -> list[dict]:
    run_dir = Path(run_dir)
    out = []
    for p in sorted((run_dir / "trajectories").glob("*.json")):
        traj = json.loads(p.read_text(encoding="utf-8"))
        ev = run_dir / "evals" / p.name
        traj["eval"] = json.loads(ev.read_text(encoding="utf-8")) if ev.exists() else {}
        traj["_path"] = str(p)
        out.append(traj)
    return out


def repeated_chats(traj: dict) -> tuple[int, int]:
    """(repeated, total) chat messages; a message is repeated if the same normalized text was already sent
    in the episode by anyone. A judge-free proxy for the paper's top failure mode (stale & redundant)."""
    seen: set[str] = set()
    repeated = total = 0
    for a in traj["actions"]:
        if a["tool"] != "send_chat" or not a["ok"]:
            continue
        text = " ".join(str(a["args"].get("message", "")).lower().split())
        total += 1
        repeated += text in seen
        seen.add(text)
    return repeated, total


def system_label(traj: dict) -> str:
    m = traj.get("meta") or {}
    label = m.get("model") or m.get("agent") or "?"
    return f"{label} ({m['effort']})" if m.get("effort") else label


def _cce(traj: dict, judge: str | None) -> dict | None:
    cce = (traj.get("eval") or {}).get("cce") or {}
    if judge:
        return cce.get(judge)
    return cce.get("llm") or cce.get("rule")


def _fmt(x, pct=False, nd=2):
    if x is None:
        return "-"
    return f"{100 * x:.1f}" if pct else f"{x:.{nd}f}"


def aggregate(trajs: list[dict], judge: str | None = None) -> dict:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for t in trajs:
        groups[(system_label(t), t["setting"])].append(t)
    rows = {}
    for key, ts in sorted(groups.items()):
        succ = [t for t in ts if t["result"]["success"]]
        cces = [c for c in (_cce(t, judge) for t in ts) if c]
        cces_s = [c for c in (_cce(t, judge) for t in succ) if c]
        pac_min = [min(c["pac"].values()) for c in cces_s if c["pac"]]
        n_act = sum(t["result"]["n_actions"] for t in ts)
        rep = [repeated_chats(t) for t in ts]
        n_chat = sum(c for _, c in rep)
        rows[key] = {
            "n": len(ts),
            "sr": mean(t["result"]["success"] for t in ts),
            "psr": mean(t["result"]["psr"] for t in ts),
            "cce": mean(c["cce"] for c in cces) if cces else None,
            "cce_succ": mean(c["cce"] for c in cces_s) if cces_s else None,
            "pac_min": mean(pac_min) if pac_min else None,
            "rounds": mean(t["result"]["rounds_used"] for t in ts),
            "rounds_succ": mean(t["result"]["rounds_used"] for t in succ) if succ else None,
            "chats": mean(t["result"]["n_chats"] for t in ts),
            "repeat_rate": sum(r for r, _ in rep) / n_chat if n_chat else None,
            "fail_rate": sum(t["result"]["n_failed_actions"] for t in ts) / n_act if n_act else 0.0,
            "deaths": mean(t["result"]["deaths"] for t in ts),
            "cost": sum((t.get("usage") or {}).get("cost_usd", 0.0) for t in ts),
            "judge": (cces[0]["judge"] if cces else None),
        }
    return rows


def render_markdown(trajs: list[dict], judge: str | None = None, graphs: int = 0) -> str:
    rows = aggregate(trajs, judge)
    md = ["# AgentWorld-Lite results", ""]
    md.append(f"Episodes: {len(trajs)} | systems x settings: {len(rows)}")
    md.append("")
    md.append("## Main results")
    md.append("")
    md.append("| System | Setting | N | SR% | PSR% | CCE | CCE \\| success | min PAC \\| success | Avg rounds | Avg chats | Repeated chats % | Failed-action % | Deaths/ep | Cost $ | CCE judge |")
    md.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for (sysname, setting), r in rows.items():
        md.append(f"| {sysname} | {setting} | {r['n']} | {_fmt(r['sr'], True)} | {_fmt(r['psr'], True)} | {_fmt(r['cce'], nd=3)} | "
                  f"{_fmt(r['cce_succ'], nd=3)} | {_fmt(r['pac_min'], nd=2)} | {_fmt(r['rounds'], nd=1)} | {_fmt(r['chats'], nd=1)} | "
                  f"{_fmt(r['repeat_rate'], True)} | {_fmt(r['fail_rate'], True)} | {_fmt(r['deaths'], nd=2)} | {_fmt(r['cost'], nd=2)} | {r['judge'] or '-'} |")
    md.append("")
    md.append("CCE counts failed episodes as 0 (no success action, so the contributing set is empty); "
              "'CCE | success' averages successful episodes only. min PAC = least-contributing agent's share of useful actions. "
              "Repeated chats = messages whose text was already sent earlier in the episode "
              "(judge-free proxy for stale/redundant messages).")

    # per-category SR
    keys = list(rows)
    md += ["", "## Success rate by category (%)", ""]
    md.append("| Category | " + " | ".join(f"{s} / {st}" for s, st in keys) + " |")
    md.append("|---|" + "---|" * len(keys))
    for cat in TASK_CATEGORIES:
        cells = []
        for s, st in keys:
            ts = [t for t in trajs if t["category"] == cat and system_label(t) == s and t["setting"] == st]
            cells.append(_fmt(mean(t["result"]["success"] for t in ts), True) if ts else "-")
        if any(c != "-" for c in cells):
            md.append(f"| {cat} | " + " | ".join(cells) + " |")

    # per-task table
    md += ["", "## Per-task outcomes", ""]
    md.append("| Task | System / Setting | Success | PSR | Rounds | Chats | CCE | Verifier |")
    md.append("|---|---|---|---|---|---|---|---|")
    for t in sorted(trajs, key=lambda t: (t["task_id"], system_label(t), t["setting"], t["seed"])):
        c = _cce(t, judge)
        md.append(f"| {t['task_id']} | {system_label(t)} / {t['setting']} (s{t['seed']}) | {'✓' if t['result']['success'] else '✗'} | "
                  f"{t['result']['psr']:.2f} | {t['result']['rounds_used']}/{t['max_rounds']} | {t['result']['n_chats']} | "
                  f"{_fmt(c['cce'] if c else None, nd=2)} | {t['result']['message']} |")

    # failure taxonomy
    labelled = [t for t in trajs if (t.get("eval") or {}).get("failures")]
    if labelled:
        md += ["", "## Communication failure taxonomy", ""]
        cats = [c for c in CATEGORIES if c != "none"]
        md.append("| System / Setting | Messages | Flagged % | " + " | ".join(cats) + " |")
        md.append("|---|---|---|" + "---|" * len(cats))
        for s, st in keys:
            counts = defaultdict(int)
            for t in labelled:
                if system_label(t) == s and t["setting"] == st:
                    for k, v in t["eval"]["failures"]["counts"].items():
                        counts[k] += v
            total = sum(counts.values())
            flagged = total - counts["none"]
            if not total:
                continue
            shares = [_fmt(counts[c] / flagged, True) if flagged else "-" for c in cats]
            md.append(f"| {s} / {st} | {total} | {_fmt(flagged / total, True)} | " + " | ".join(shares) + " |")
        md.append("")
        md.append("Category shares are percentages of flagged (non-'none') messages, as in the paper.")

    # judge agreement
    agree = [t["eval"]["agreement"] for t in trajs if (t.get("eval") or {}).get("agreement")]
    if agree:
        md += ["", "## CCE judge agreement (rule vs LLM)", ""]
        md.append(f"Episodes: {len(agree)} | mean raw agreement: {mean(a['raw'] for a in agree):.3f} | "
                  f"mean Cohen's kappa: {mean(a['kappa'] for a in agree):.3f}")

    # causal graphs
    if graphs:
        shown = [t for t in trajs if t["result"]["success"] and _cce(t, judge)][:graphs]
        if shown:
            md += ["", "## Causal action graphs (contributing actions only)", ""]
            for t in shown:
                c = _cce(t, judge)
                md.append(f"### {t['task_id']} — {system_label(t)} / {t['setting']} (CCE {c['cce']:.2f}, judge={c['judge']})")
                md.append("")
                md += ["```mermaid", to_mermaid(t, c), "```", ""]
    return "\n".join(md) + "\n"
