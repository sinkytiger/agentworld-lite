"""모델 비교 리더보드: 한국 금융지주 데스크(tasks/finance_kr) 실행 기록을 모델별로 모아 성공률, 비용, 라운드, 규칙 CCE를 비교하고,
틀리거나 빠진 답을 함정 종류와 협업 단계로 나눈다. 모델을 호출하지 않는다.

    python -m agentworld_lite.finance leaderboard --out docs/finance_kr_leaderboard.md

Runs are found under runs/finance_kr_* (each with run_config.json; directories of one model are merged). Every
trajectory is replayed on a fresh desk (note.replay), which recovers each fact's value and every agent's notebook, and
each answer is classified:

  정답                 submitted, within tolerance
  함정: <실수>          submitted, equal to another answer's right value (다른 답(…)의 값, e.g. two groups swapped)
                       or to the value a listed mistake gives (traps_kr)
  기타 오답             submitted, wrong, no listed mistake
  제출 누락             not submitted, although an agent who may submit held a correct value
  공유 누락             not submitted; a correct value was computed but never reached an agent who may submit
  함정 값만 계산: <실수> not submitted; only a listed mistake's value was computed
  입력 부족             not submitted; the agents who calculate never held every filing figure the reference needs
  계산 안 함            not submitted; the inputs were there but nothing was computed

Intermediate calculations are matched against the same trap list, so a mistake that was made and then corrected shows
up even in a solved episode.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from ..task import Task, load_tasks
from .env import REPO_ROOT
from .note import NoteError, replay
from .traps_kr import match_trap

RUNS_ROOT = REPO_ROOT / "runs"
TASK_DIR = REPO_ROOT / "tasks" / "finance_kr"
PAIRS = ("k01", "k02", "k03", "k04", "k05", "k06")
PAIR_KO = {"k01": "KB BPS·ROE", "k02": "신한·우리 2분기 CIR", "k03": "4대 2분기 순이익·보통주자본",
           "k04": "2분기 순이익 증가율", "k05": "합산 CIR", "k06": "밸류업 ROE 격차"}
CELLS = (("shared", "full"), ("shared", "no_comm"), ("private", "full"), ("private", "no_comm"))
MISSING = ("제출 누락", "공유 누락", "함정 값만 계산", "입력 부족", "계산 안 함")


@dataclass
class Episode:
    model: str
    provider: str
    task_id: str
    pair: str
    placement: str
    setting: str
    seed: int
    success: bool
    answers_ok: int
    n_answers: int
    rounds: int
    max_rounds: int
    cost: float
    failed_calls: int
    cce: float | None
    turns: int = 0
    idle_turns: int = 0                                          # turns that produced no tool call (or a refusal)
    outcomes: dict[str, str] = field(default_factory=dict)       # field -> category (see the module docstring)
    detail: dict[str, str] = field(default_factory=dict)         # field -> what was submitted / missing
    trap_hits: list[tuple[str, str, bool]] = field(default_factory=list)  # (field, mistake, final answer right)


# ---------------------------------------------------------------------- reference inputs
def _steps(task: Task):
    for agent, steps in (task.reference_solution or {}).items():
        for s in steps:
            yield agent, s[0], s[1] if len(s) > 1 else {}


def reference_leaves(task: Task, answer_field: str) -> list[str]:
    """Filing figures (and task inputs) the reference solution combines into one answer."""
    calcs = {a["name"]: a for _, tool, a in _steps(task) if tool == "calculate"}
    target = next((x["fact_id"] for _, tool, a in _steps(task) if tool == "submit_answer"
                   for x in a.get("answers", []) if x["field"] == answer_field), None)
    out: list[str] = []

    def walk(fid: str):
        name = fid[5:] if fid.startswith("calc.") else None
        if name in calcs:
            for i in calcs[name].get("inputs", []):
                walk(i["fact_id"])
        elif fid not in out:
            out.append(fid)
    if target:
        walk(target)
    return out


# ---------------------------------------------------------------------- one episode
def _num(v: float) -> str:
    return f"{v:,.0f}" if abs(v) >= 1e5 else f"{v:,.4f}".rstrip("0").rstrip(".")


def _close(value: float, expected: float, rel_tol: float) -> bool:
    return abs(value - expected) <= rel_tol * abs(expected)


def classify(traj: dict, task: Task) -> tuple[dict[str, str], dict[str, str], list[tuple[str, str, bool]]]:
    """(outcome per answer, detail per answer, trap hits among all calculations)."""
    env = replay(traj, task)
    pair = task.id[:3]
    can = lambda tool: [a["username"] for a in task.agents if tool not in (a.get("restricted_tools") or [])]
    submitters, calculators = can("submit_answer"), can("calculate")
    held = lambda names, fid: any(env.agents[n].inventory[fid] > 0 for n in names)
    calcs = [f for f in env.facts.values() if f.meta.get("source") == "calculation"]
    cps = task.verifier["checkpoints"]
    answer_of = lambda v: next((c["field"] for c in cps if _close(v, c["expected"], c["rel_tol"])), None)
    stray = [f for f in calcs if answer_of(f.value) is None]  # another answer's right value is not a mistake
    outcomes, detail, hits = {}, {}, []
    for cp in cps:
        fld, exp, tol = cp["field"], cp["expected"], cp["rel_tol"]
        fid = env.submission.get(fld)
        if fid is not None:
            v = env.facts[fid].value
            if _close(v, exp, tol):
                outcomes[fld] = "정답"
            else:
                other = answer_of(v)
                trap = f"다른 답({other})의 값" if other else match_trap(pair, fld, v)
                outcomes[fld] = f"함정: {trap}" if trap else "기타 오답"
                detail[fld] = f"제출 {_num(v)} (정답 {_num(exp)})"
            continue
        right = [f for f in calcs if _close(f.value, exp, tol)]
        if right:
            outcomes[fld] = "제출 누락" if any(held(submitters, f.id) for f in right) else "공유 누락"
            detail[fld] = f"{right[0].id} = {_num(right[0].value)}"
            continue
        trap = next((t for f in stray if (t := match_trap(pair, fld, f.value))), None)
        if trap:
            outcomes[fld], detail[fld] = f"함정 값만 계산: {trap}", ""
            continue
        leaves = reference_leaves(task, fld)
        missing = [x for x in leaves if not held(calculators, x)]
        outcomes[fld] = "입력 부족" if missing else "계산 안 함"
        detail[fld] = (f"필요한 수치 {len(leaves)}개 중 {len(missing)}개 없음: {', '.join(missing[:2])}"
                       + (" 등" if len(missing) > 2 else "")) if missing else ""
    ok = {cp["field"]: outcomes[cp["field"]] == "정답" for cp in cps}
    for f in stray:
        for fld in ok:
            if (trap := match_trap(pair, fld, f.value)):
                hits.append((fld, trap, ok[fld]))
    return outcomes, detail, hits


# ---------------------------------------------------------------------- runs
def discover(root: str | Path = RUNS_ROOT, prefix: str = "finance_kr_") -> dict[str, list[Path]]:
    """{model: [run dirs]} from run_config.json; directories of one model are merged."""
    out: dict[str, list[Path]] = {}
    for d in sorted(Path(root).glob(f"{prefix}*")):
        cfg = d / "run_config.json"
        if cfg.exists():
            out.setdefault(json.loads(cfg.read_text(encoding="utf-8"))["model"], []).append(d)
    return out


def load_episodes(runs: dict[str, list[Path]], task_dir: str | Path = TASK_DIR
                  ) -> tuple[list[Episode], list[str], dict[str, int]]:
    """(episodes, trajectories that could not be replayed, episodes each model's run configs planned)."""
    tasks = {t.id: t for t in load_tasks(str(task_dir))}
    episodes, skipped, planned = [], [], {}
    for model, dirs in runs.items():
        for d in dirs:
            cfg = json.loads((Path(d) / "run_config.json").read_text(encoding="utf-8"))
            provider = cfg.get("provider", "")
            planned[model] = planned.get(model, 0) + len(cfg["tasks"]) * len(cfg["settings"]) * len(cfg["seeds"])
            for p in sorted((Path(d) / "trajectories").glob("*.json")):
                t = json.loads(p.read_text(encoding="utf-8"))
                task = tasks.get(t["task_id"])
                if task is None:
                    skipped.append(f"{p.name}: unknown task")
                    continue
                try:
                    outcomes, detail, hits = classify(t, task)
                except NoteError as exc:
                    skipped.append(f"{p.name}: {exc}")
                    continue
                ev = Path(d) / "evals" / p.name
                cce = (json.loads(ev.read_text(encoding="utf-8"))["cce"].get("rule") or {}).get("cce") if ev.exists() else None
                r = t["result"]
                episodes.append(Episode(
                    model, provider, t["task_id"], t["task_id"][:3], t["task_id"].rsplit("_", 1)[1], t["setting"],
                    t["seed"], r["success"], sum(v == "정답" for v in outcomes.values()), len(outcomes),
                    r["rounds_used"], t["max_rounds"], (t.get("usage") or {}).get("cost_usd", 0.0),
                    r["n_failed_actions"], cce, len(t["actions"]),
                    sum(a["tool"] == "wait" and str((a.get("args") or {}).get("reason", "")).startswith(
                        ("no tool call", "model refusal")) for a in t["actions"]),
                    outcomes, detail, hits))
    return episodes, skipped, planned


# ---------------------------------------------------------------------- the report (Korean Markdown)
def _pct(a: int, b: int) -> str:
    return f"{a / b * 100:.0f}%" if b else "-"


def _mean(xs) -> float | None:
    xs = [x for x in xs if x is not None]
    return statistics.mean(xs) if xs else None


def render(episodes: list[Episode], skipped: list[str] | None = None, notes: list[str] | None = None,
           planned: dict[str, int] | None = None, title: str = "한국 데스크 모델 비교") -> str:
    by = {m: [e for e in episodes if e.model == m] for m in dict.fromkeys(e.model for e in episodes)}
    rank = lambda m: (-sum(e.success for e in by[m]) / len(by[m]), -sum(e.cost for e in by[m]) / len(by[m]))
    models = sorted(by, key=rank)  # success rate, then the dearer (larger) model first
    notes = list(notes or [])
    for m in models:
        if planned and planned.get(m, 0) > len(by[m]):
            notes.insert(0, f"{m}: 실행 설정상 {planned[m]}판 중 {len(by[m])}판만 기록되어 있습니다(진행 중이거나 중단된 실행).")
    unrun = [p for p in PAIRS if not any(e.pair == p for e in episodes)]
    if unrun:
        notes.insert(0, f"{', '.join(unrun)}은(는) 어느 모델로도 실행하지 않았습니다.")
    out = [f"# {title}", "",
           "이 문서는 `python -m agentworld_lite.finance leaderboard`가 저장된 실행 기록으로 만든 것입니다(모델 호출 없음). "
           "각 실행 기록을 새 데스크에서 다시 실행해 답과 중간 계산을 함정 목록과 대조했습니다.", "",
           "## 요약", "",
           "| 모델 | 에피소드 | 성공 | 답 정답률 | 시드 | 라운드 사용(한도 대비) | 실패한 도구 호출/판 | 도구 호출 없는 턴 | 규칙 CCE | 비용 | 판당 비용 | 성공당 비용 |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for m in models:
        es = by[m]
        won = sum(e.success for e in es)
        cost = sum(e.cost for e in es)
        cce = _mean(e.cce for e in es)
        out.append(
            f"| {m} | {len(es)} | {won} ({_pct(won, len(es))}) | "
            f"{_pct(sum(e.answers_ok for e in es), sum(e.n_answers for e in es))} | "
            f"{', '.join(str(s) for s in sorted({e.seed for e in es}))} | "
            f"{statistics.mean(e.rounds / e.max_rounds for e in es) * 100:.0f}% | "
            f"{statistics.mean(e.failed_calls for e in es):.2f} | "
            f"{_pct(sum(e.idle_turns for e in es), sum(e.turns for e in es))} | {'-' if cce is None else f'{cce:.2f}'} | "
            f"${cost:.2f} | ${cost / len(es):.3f} | {'-' if not won else f'${cost / won:.3f}'} |")
    out += ["", "## 과제별 성공", "", "| 모델 | " + " | ".join(f"{p} {PAIR_KO[p]}" for p in PAIRS) + " |",
            "|---|" + "---|" * len(PAIRS)]
    for m in models:
        cells = []
        for p in PAIRS:
            es = [e for e in by[m] if e.pair == p]
            cells.append(f"{sum(e.success for e in es)}/{len(es)}" if es else "실행 안 함")
        out.append(f"| {m} | " + " | ".join(cells) + " |")
    out += ["", "## 방법론 위치와 채팅", "",
            "공유형(shared)은 방법론 문장이 모두가 보는 문서에, 개인형(private)은 analyst의 개인 브리핑에만 있습니다. "
            "no_comm은 채팅을 막은 설정입니다.", "",
            "| 모델 | 공유형·채팅 | 공유형·채팅 없음 | 개인형·채팅 | 개인형·채팅 없음 |", "|---|---|---|---|---|"]
    for m in models:
        cells = []
        for placement, setting in CELLS:
            es = [e for e in by[m] if e.placement == placement and e.setting == setting]
            cells.append(f"{sum(e.success for e in es)}/{len(es)}" if es else "-")
        out.append(f"| {m} | " + " | ".join(cells) + " |")
    wrong = [(e, f, c) for e in episodes for f, c in e.outcomes.items() if c != "정답"]
    out += ["", "## 실패 분석: 틀리거나 빠진 답", ""]
    if not wrong:
        out.append("틀리거나 빠진 답이 없습니다.")
    else:
        cats = list(dict.fromkeys(c.split(":")[0] for _, _, c in wrong))
        out += ["| 모델 | " + " | ".join(cats) + " | 합계 |", "|---|" + "---|" * (len(cats) + 1)]
        for m in models:
            mine = [c.split(":")[0] for e, _, c in wrong if e.model == m]
            if mine:
                out.append(f"| {m} | " + " | ".join(str(mine.count(c)) for c in cats) + f" | {len(mine)} |")
        out += ["", "| 모델 | 에피소드 | 라운드 | 답 | 분류 | 근거 |", "|---|---|---|---|---|---|"]
        for e, f, c in wrong:
            out.append(f"| {e.model} | {e.task_id} {e.setting} s{e.seed} | {e.rounds}/{e.max_rounds} | {f} | {c} | "
                       f"{e.detail.get(f, '') or '-'} |")
    hits = [(e, f, t, ok) for e in episodes for f, t, ok in e.trap_hits]
    out += ["", "## 중간 계산에서 나온 함정 값", "",
            "제출 여부와 관계없이, 계산 결과가 함정 목록의 값과 같았던 경우입니다(최종 답이 맞았는지 함께 적음).", ""]
    if not hits:
        out.append("중간 계산에서도 함정 값이 나오지 않았습니다.")
    else:
        out += ["| 모델 | 에피소드 | 답 | 함정 | 최종 답 |", "|---|---|---|---|---|"]
        for e, f, t, ok in hits:
            out.append(f"| {e.model} | {e.task_id} {e.setting} s{e.seed} | {f} | {t} | {'정답' if ok else '틀리거나 빠짐'} |")
    if notes:
        out += ["", "## 참고", "", *[f"- {n}" for n in notes]]
    if skipped:
        out += ["", "## 다시 실행하지 못한 기록", "", *[f"- {s}" for s in skipped]]
    return "\n".join(out).rstrip() + "\n"
