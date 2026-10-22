"""3분기 실적 자동 갱신: DART 공시 목록(list.json)에서 4대 금융지주의 새 정기보고서와 정정 보고서를 찾고, 원하면 새 보고서를
데이터셋에 넣은 뒤 숫자마다 접수번호가 붙은 한국어 3분기 실적 노트를 쓴다. 언어 모델은 쓰지 않는다.

    python -m agentworld_lite.finance watch-filings                 # 확인만 (DART_API_KEY 필요, 파일을 바꾸지 않음)
    python -m agentworld_lite.finance watch-filings --apply         # 새 보고서를 데이터셋에 넣고 과제 검증, 노트 작성
    python -m agentworld_lite.finance watch-filings --note-only     # 지금 데이터셋으로 3분기 노트만 (오프라인)

One run and done: this module registers no scheduled task, starts no service and never runs git. Run it again whenever
you want to check (each run compares DART's list with the dataset, so a pending report keeps showing until applied).

For each group the periodic-report list (pblntf_ty A) from --since to --until is read and every report is placed by its
name ("분기보고서 (2026.09)" is 2026 Q3, "[기재정정]반기보고서 (2026.06)" an amendment of 2026 H1):

  new        a period the dataset does not have yet. --apply fetches it (CFS and OFS statements, shares, the original
             filing of an amended report) with the same code as `fetch --profile fhc`.
  amended    a newer filing of a period the dataset has. Reported only: the served figures would change and the filing
             date would move past the as-of date of the tasks (2026-10-15), so updating is a decision for a person
             (`fetch --source dart --profile fhc --company CODE --periods YEAR:PERIOD`).

A small state file (default data/finance/cache/watch_state.json, outside the repository) records when each receipt
number was first seen, so a run can say what is new since the last one.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .catalog import FHC
from .dart import fetch_company, filed_date
from .dataset import Dataset, Fact, statement_facts
from .env import REPO_ROOT
from .note_ko import cited_ko, dart_link, uncited_figures_ko

DATA_DIR = REPO_ROOT / "data" / "finance" / "dart_fhc"
STATE_FILE = REPO_ROOT / "data" / "finance" / "cache" / "watch_state.json"
TASK_AS_OF = "2026-10-15"
CODES = tuple(FHC.values())
NOTICE = "교육용 예시·투자 권유 아님"
_REPORT = re.compile(r"^(?P<marks>(?:\[[^\]]*\]\s*)*)(?P<kind>분기보고서|반기보고서|사업보고서)\s*\((?P<y>\d{4})\.(?P<m>\d{2})\)")
_PERIOD = {("분기보고서", "03"): "Q1", ("반기보고서", "06"): "H1", ("분기보고서", "09"): "Q3", ("사업보고서", "12"): "FY"}


class WatchError(Exception):
    pass


@dataclass
class Event:
    code: str
    name: str
    year: int
    period: str
    kind: str              # new | amended
    rcept_no: str
    report: str
    filed: str
    dataset_rcept_no: str | None = None
    first_seen: str | None = None
    applied: bool = False

    def line(self) -> str:
        what = {"new": "새 보고서", "amended": "정정 보고서"}[self.kind]
        seen = f", 처음 발견 {self.first_seen}" if self.first_seen else ""
        tail = f" (데이터셋은 {self.dataset_rcept_no})" if self.dataset_rcept_no else ""
        done = " → 데이터셋에 반영함" if self.applied else ""
        return f"{self.name}: {what} {self.report} 접수번호 {self.rcept_no} 접수일 {self.filed}{tail}{seen}{done}"


def parse_report(report_nm: str) -> tuple[int, str, bool] | None:
    """'[기재정정]반기보고서 (2026.06)' -> (2026, 'H1', True); None for anything that is not a periodic report."""
    m = _REPORT.match(str(report_nm or "").strip())
    if not m or (m["kind"], m["m"]) not in _PERIOD:
        return None
    return int(m["y"]), _PERIOD[(m["kind"], m["m"])], bool(m["marks"])


def _known(data: dict) -> dict[tuple[int, str], set[str]]:
    """Receipt numbers the dataset already holds per period (served version and original filing)."""
    out: dict[tuple[int, str], set[str]] = {}
    for key, st in (data.get("statements") or {}).items():
        year, period, _ = key.split(":")
        out.setdefault((int(year), period), set()).update(x for x in (st.get("rcept_no"), st.get("first_rcept_no")) if x)
    return out


def latest_filed(data: dict) -> str:
    """The group's latest filing date in the dataset (YYYYMMDD). Each group is scanned from its own, so a pending
    amendment of one group is not skipped once another group's newer report has been applied."""
    dates = [st.get("filed") for st in (data.get("statements") or {}).values() if st.get("filed")]
    return max(dates).replace("-", "") if dates else "20260101"


def scan(client, data_dir: str | Path = DATA_DIR, since: str | None = None, until: str | None = None) -> list[Event]:
    """Periodic reports filed between since (default: per group, see latest_filed) and until (YYYYMMDD) that the
    dataset does not hold yet."""
    data_dir = Path(data_dir)
    until = until or dt.date.today().strftime("%Y%m%d")
    events = []
    for code in CODES:
        data = json.loads((data_dir / f"{code}.json").read_text(encoding="utf-8"))
        known = _known(data)
        rows = client.filing_list(data["corp_code"], since or latest_filed(data), until, kind="A")
        by_period: dict[tuple[int, str], list[dict]] = {}
        for r in rows:
            parsed = parse_report(r.get("report_nm"))
            if parsed:
                by_period.setdefault(parsed[:2], []).append(r)
        for (year, period), filings in sorted(by_period.items()):
            latest = max(filings, key=lambda r: r["rcept_no"])
            have = known.get((year, period))
            if have and (latest["rcept_no"] in have or latest["rcept_no"] < max(have)):
                continue
            served = data["statements"].get(f"{year}:{period}:CFS", {}).get("rcept_no") if have else None
            events.append(Event(code, data.get("name", code), year, period, "amended" if have else "new",
                                latest["rcept_no"], re.sub(r"\s+", " ", str(latest["report_nm"])).strip(),
                                filed_date(latest["rcept_no"]), served))
    return events


def remember(events: list[Event], state_path: str | Path = STATE_FILE, today: str | None = None) -> None:
    """Mark each event with the date its receipt number was first seen, and record new ones."""
    p = Path(state_path)
    state = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"seen": {}}
    today = today or dt.date.today().isoformat()
    for e in events:
        rec = state["seen"].setdefault(e.rcept_no, {"code": e.code, "report": e.report, "kind": e.kind,
                                                    "first_seen": today})
        e.first_seen = rec["first_seen"]
    state["last_run"] = today
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")


def apply(client, events: list[Event], data_dir: str | Path = DATA_DIR, log=print) -> list[Path]:
    """Fetch the new periods into the dataset. Amendments are left alone (see the module docstring)."""
    written = []
    for e in events:
        if e.kind != "new":
            continue
        written.append(fetch_company(client, e.code, [f"{e.year}:{e.period}"], out_dir=data_dir, log=log,
                                     profile="fhc", first_filings=True))
        e.applied = True
    return written


# ---------------------------------------------------------------------- the Q3 note (Korean Markdown)
def _facts(c, year: int, period: str) -> dict[str, Fact]:
    return {f.meta["account"]: f for f in statement_facts(c, year, period, "CFS")}


def render_q3_note(data_dir: str | Path = DATA_DIR, year: int = 2026) -> str:
    ds = Dataset(Path(data_dir))
    out = [f"# 4대 금융지주 {year}년 3분기 실적 (공시 자동 갱신)", "",
           f"> **{NOTICE}.** 데이터셋에 들어온 3분기보고서만으로 규칙에 따라 쓴 노트이며, 언어 모델이 쓰지 않았습니다. "
           "모든 수치 뒤에 사실 ID와 DART 접수번호를 붙였습니다. 금액은 억원 미만에서 반올림했습니다.", "",
           "- 3분기 단독 = 3분기 누적 − 상반기 누적. 증감률은 전년 같은 분기(같은 방식) 대비.",
           "- CIR(누적) = 일반관리비 ÷ (영업이익 + 일반관리비 + 신용손실충당금 전입액) × 100. "
           "보통주자본 = 지배주주지분 − 신종자본증권(9월 말).", ""]
    rows, missing, sources = [], [], []
    for code in CODES:
        c = ds.companies[code]
        q3 = _facts(c, year, "Q3")
        if not q3:
            missing.append(c.name)
            continue
        h1, q3p, h1p = _facts(c, year, "H1"), _facts(c, year - 1, "Q3"), _facts(c, year - 1, "H1")
        ni = "net_income_owners"
        now = q3[ni].value - h1[ni].value
        cells = [c.name, cited_ko(now, "KRW", [q3[ni], h1[ni]])]
        if q3p and h1p:
            before = q3p[ni].value - h1p[ni].value
            cells += [cited_ko(before, "KRW", [q3p[ni], h1p[ni]]),
                      cited_ko((now / before - 1) * 100, "%", [q3[ni], h1[ni], q3p[ni], h1p[ni]])]
        else:
            cells += ["-", "-"]
        g, o, p = (q3[k] for k in ("general_admin_expense", "operating_income", "credit_loss_provision"))
        cells += [cited_ko(q3[ni].value, "KRW", [q3[ni]]),
                  cited_ko(g.value / (o.value + g.value + p.value) * 100, "%", [g, o, p]),
                  cited_ko(q3["equity_owners"].value - q3["hybrid_capital"].value, "KRW",
                           [q3["equity_owners"], q3["hybrid_capital"]])]
        rows.append("| " + " | ".join(cells) + " |")
        st = c.statement(year, "Q3", "CFS")
        sources.append(f"| {c.name} | {st.get('form', '분기보고서')} | {dart_link(st['rcept_no'])} | {st.get('filed')} | "
                       f"{st.get('first_rcept_no') or '-'} |")
    if rows:
        out += ["## 그룹별 3분기", "",
                "| 금융지주 | 3분기 지배주주순이익 | 전년 3분기 | 증감률 | 누적 지배주주순이익 | 누적 CIR | 9월 말 보통주자본 |",
                "|---|---|---|---|---|---|---|", *rows, ""]
    else:
        out += ["아직 데이터셋에 들어온 3분기보고서가 없습니다. `watch-filings`로 DART 공시 목록을 확인하세요.", ""]
    if missing:
        out += [f"아직 3분기보고서가 데이터셋에 없는 그룹: {', '.join(missing)}.", ""]
    if sources:
        out += ["## 출처", "", "| 금융지주 | 보고서 | 접수번호 | 접수일 | 최초 접수 |", "|---|---|---|---|---|", *sources, ""]
    md = "\n".join(out).rstrip() + "\n"
    bad = uncited_figures_ko(md)
    if bad:
        raise WatchError(f"uncited figures in the Q3 note: {', '.join(bad[:5])}")
    return md


def run(client, data_dir: str | Path = DATA_DIR, since: str | None = None, until: str | None = None,
        do_apply: bool = False, state_path: str | Path | None = STATE_FILE, log=print) -> list[Event]:
    events = scan(client, data_dir, since, until)
    if state_path:
        remember(events, state_path)
    if do_apply:
        apply(client, events, data_dir, log=log)
    for e in events:
        log(e.line())
    if not events:
        log("새 보고서나 정정 보고서가 없습니다.")
    if any(e.kind == "amended" for e in events):
        log(f"정정 보고서는 자동 반영하지 않습니다. 반영하면 접수일이 과제 기준일({TASK_AS_OF}) 이후로 바뀝니다.")
    return events

