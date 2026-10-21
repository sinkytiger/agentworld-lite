"""밸류에이션 스냅샷: 4대 금융지주 한 곳을 나머지 세 곳과 비교한다. 공시 수치만 쓰고 언어 모델은 쓰지 않는다.

    python -m agentworld_lite.finance valuation --market kr --bank 105560 --out kb_valuation.md

For each group, at the end of 2026 H1: the KRX close on 2026-06-30 (not in DART: added with its source by
`fetch --price CODE@DATE=VALUE --price-source ...`), market capitalization on issued common shares, TTM net income to
owners = FY 2025 - H1 2025 + H1 2026, PER, common equity = owners' equity - hybrid capital securities (신종자본증권), PBR,
a simple ROE (TTM net income to owners / period-end common equity) and the cost-to-income ratio of Q2 alone. Korean
banks are compared on P/B against ROE, so the subject's common equity times the peers' lowest, median and highest PBR
gives the market-capitalization range those multiples imply. Every number cites its fact ids and DART receipt numbers.
An educational sample (교육용 예시·투자 권유 아님): no rating, no target price, not investment advice.
"""

from __future__ import annotations

import statistics
from pathlib import Path

from .catalog import FHC
from .dataset import Dataset, Fact, price_fact, share_facts, statement_facts
from .env import REPO_ROOT
from .note_ko import cited_ko, dart_link, uncited_figures_ko
from .valuation import ValuationError

PRICE_DATE = "2026-06-30"
CODES = tuple(FHC.values())
RATING_WORDS_KO = ("매수", "매도", "중립", "비중확대", "비중축소", "목표주가", "목표가", "투자의견", "적정주가")
NOTICE = "교육용 예시·투자 권유 아님"


def group_metrics(ds: Dataset, code: str, as_of: str, year: int = 2026, price_date: str = PRICE_DATE) -> dict:
    """Each metric as (value, unit, facts) for the end of `year` H1."""
    c = ds.companies[code]
    facts = {}
    for y, p in ((year - 1, "FY"), (year - 1, "H1"), (year, "Q1"), (year, "H1")):
        st = c.statement(y, p, "CFS")
        if st is None or (st.get("filed") or "") > as_of:
            raise ValuationError(f"{code}: no {y} {p} report filed by {as_of}")
        facts[(y, p)] = {f.meta["account"]: f for f in statement_facts(c, y, p, "CFS")}
    shares = next((f for f in share_facts(c, year, "H1") if f.id.endswith(".shares_common")), None)
    if shares is None or (shares.meta.get("filed") or "") > as_of:
        raise ValuationError(f"{code}: no {year} H1 share count filed by {as_of}")
    price = price_fact(c, price_date)
    if price is None or not (c.data.get("price_sources") or {}).get(price_date):
        raise ValuationError(f"{code}: no KRX close for {price_date} with its source; add it with "
                             f"fetch --source dart --profile fhc --company {code} --price {code}@{price_date}=<close> "
                             "--price-source \"<where it comes from>\"")
    fy, prev, q1, h1 = facts[(year - 1, "FY")], facts[(year - 1, "H1")], facts[(year, "Q1")], facts[(year, "H1")]
    ni = [fy["net_income_owners"], prev["net_income_owners"], h1["net_income_owners"]]
    ttm = ni[0].value - ni[1].value + ni[2].value
    mcap = price.value * shares.value
    eq = [h1["equity_owners"], h1["hybrid_capital"]]
    ce = eq[0].value - eq[1].value
    acc = ("operating_income", "general_admin_expense", "credit_loss_provision")
    q2 = {a: h1[a].value - q1[a].value for a in acc}
    cost = [h1[a] for a in acc] + [q1[a] for a in acc]
    return {
        "code": code, "name": c.name,
        "price": (price.value, "KRW", [price]),
        "shares": (shares.value, "shares", [shares]),
        "treasury": next(((f.value, "shares", [f]) for f in share_facts(c, year, "H1") if f.id.endswith("treasury_common")),
                         None),
        "mcap": (mcap, "KRW", [price, shares]),
        "ttm_ni": (ttm, "KRW", ni),
        "per": (mcap / ttm, "x", [price, shares, *ni]),
        "common_equity": (ce, "KRW", eq),
        "pbr": (mcap / ce, "x", [price, shares, *eq]),
        "roe": (ttm / ce * 100, "%", [*ni, *eq]),
        "cir": (q2["general_admin_expense"] / sum(q2.values()) * 100, "%", cost),
    }


def _median(rows: list[tuple]) -> tuple:
    return (statistics.median(v for v, _, _ in rows), rows[0][1], [f for _, _, fs in rows for f in fs])


def snapshot_ko(subject: str = "105560", *, as_of: str = "2026-10-15", year: int = 2026,
                data_dir: str | Path | None = None) -> tuple[str, dict]:
    ds = Dataset(Path(data_dir) if data_dir else REPO_ROOT / "data" / "finance" / "dart_fhc")
    found = ds.find(subject)
    code = found.stock_code if found else str(subject)
    if code not in CODES:
        raise ValuationError(f"unknown group {subject!r}; choose one of {', '.join(CODES)} (or KB금융, 신한지주, 하나금융지주, 우리금융지주)")
    m = {c: group_metrics(ds, c, as_of, year) for c in CODES}
    s, peers = m[code], [m[c] for c in CODES if c != code]
    pbrs = sorted(((p["pbr"], p["name"]) for p in peers), key=lambda r: r[0][0])
    low, high, med = pbrs[0], pbrs[-1], _median([r for r, _ in pbrs])
    ce = s["common_equity"]
    implied = {k: (ce[0] * r[0], "KRW", [*ce[2], *r[2]]) for k, r in (("low", low[0]), ("median", med), ("high", high[0]))}
    numbers = {"pbr": {c: m[c]["pbr"][0] for c in CODES}, "per": {c: m[c]["per"][0] for c in CODES},
               "roe": {c: m[c]["roe"][0] for c in CODES}, "peer_pbr": {"low": low[0][0], "median": med[0], "high": high[0][0]},
               "implied_mcap": {k: v[0] for k, v in implied.items()}, "mcap": s["mcap"][0], "price": s["price"][0]}
    md = render(ds, code, m, peers, low, med, high, implied, as_of, year)
    bad = uncited_figures_ko(md)
    if bad:
        raise ValuationError(f"uncited figures in the snapshot: {', '.join(bad)}")
    return md, numbers


def _position(mcap: float, lo: float, hi: float) -> str:
    if mcap < lo:
        return "동종 그룹 PBR이 가리키는 범위보다 아래"
    if mcap > hi:
        return "동종 그룹 PBR이 가리키는 범위보다 위"
    return "동종 그룹 PBR이 가리키는 범위 안"


def render(ds, code, m, peers, low, med, high, implied, as_of, year) -> str:
    s = m[code]
    name = s["name"]
    peer_names = ", ".join(p["name"] for p in peers)
    roe_med, cir_med = _median([p["roe"] for p in peers]), _median([p["cir"] for p in peers])
    out = [
        f"# 밸류에이션 스냅샷: {name}({code}), 4대 금융지주 비교", "",
        f"> **{NOTICE}.** 공시 수치로 고정된 규칙에 따라 만든 예시이며, 언어 모델이 쓰지 않았습니다. 투자의견과 "
        "목표주가는 없습니다.", "",
        f"- **기준일:** {as_of}. 이 날까지 접수된 보고서만 씁니다. 주가는 {PRICE_DATE}({year}년 상반기 말) KRX 종가이고 "
        "출처는 각 숫자 뒤와 맨 아래에 적었습니다.",
        f"- **비교 대상:** 나머지 세 금융지주({peer_names}).",
        "- **인용:** 모든 수치 뒤에 사실 ID와 DART 접수번호를 붙였습니다. 금액은 억원 미만에서 반올림했습니다.", "",
        "## 비교표", "",
        "| 금융지주 | 종가 | 시가총액 | TTM 지배주주순이익 | PER | 보통주자본 | PBR | 단순 ROE | 2분기 CIR |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for c in CODES:
        r = m[c]
        label = f"**{r['name']}({c})**" if c == code else f"{r['name']}({c})"
        cells = [cited_ko(*r[k]) for k in ("price", "mcap", "ttm_ni", "per", "common_equity", "pbr", "roe", "cir")]
        out.append(f"| {label} | " + " | ".join(cells) + " |")
    lo, hi = implied["low"][0], implied["high"][0]
    out += [
        "",
        f"## 동종 그룹 PBR이 가리키는 {name}의 시가총액 범위", "",
        f"- 동종 그룹 PBR: 최저 {cited_ko(*low[0])}({low[1]}), 중간값 {cited_ko(*med)}, "
        f"최고 {cited_ko(*high[0])}({high[1]}).",
        f"- {name}의 보통주자본 {cited_ko(*s['common_equity'])}에 이 배수를 곱하면 시가총액은 최저 "
        f"{cited_ko(*implied['low'])}, 중간값 {cited_ko(*implied['median'])}, 최고 {cited_ko(*implied['high'])}입니다.",
        f"- {name}의 실제 시가총액은 {cited_ko(*s['mcap'])}(PBR {cited_ko(*s['pbr'])})로, "
        f"{_position(s['mcap'][0], lo, hi)}였습니다. 한 날짜의 주가가 어디에 있었는지 적은 것이며, 적정가치나 "
        "주가 전망이 아닙니다.", "",
        "## 배수가 담지 못하는 것", "",
        f"- **수익성.** {name}의 단순 ROE는 {cited_ko(*s['roe'])}, 동종 그룹 중간값은 {cited_ko(*roe_med)}입니다. 은행주의 "
        "PBR은 ROE와 함께 읽어야 합니다. ROE가 높은 그룹은 PBR도 높게 형성되는 경향이 있습니다.",
        f"- **비용.** {year}년 2분기 단독 영업이익경비율(CIR)은 {cited_ko(*s['cir'])}, 동종 그룹 중간값은 "
        f"{cited_ko(*cir_med)}입니다(낮을수록 효율적).",
        "- **신종자본증권.** 지배주주순이익에는 신종자본증권 보유자에게 돌아가는 몫이 들어 있어, 보통주 몫만 쓸 때보다 "
        "PER은 약간 낮게, ROE는 약간 높게 나옵니다. 보통주자본에서는 신종자본증권을 뺐습니다.",
    ]
    if s["treasury"]:
        out.append(f"- **자기주식.** 시가총액은 발행 보통주식 수 {cited_ko(*s['shares'])} 기준이며, 자기주식 "
                   f"{cited_ko(*s['treasury'])}은 빼지 않았습니다. 네 그룹 모두 2025년 말보다 2026년 6월 말의 발행 "
                   "보통주식 수가 적습니다(자기주식 소각).")
    out += [
        "- **일회성 항목.** 공시 원값이며 조정하지 않았습니다. 큰 일회성 손익(희망퇴직 비용, 충당금 환입, 보험 계리가정 "
        "변경 등)이 지난 네 분기에 있으면 PER과 ROE가 그만큼 달라집니다.", "",
        "## 방법", "",
        f"- **TTM 지배주주순이익** = {year - 1}년 연간 − {year - 1}년 상반기 누적 + {year}년 상반기 누적. 연결 재무제표 기준.",
        f"- **시가총액** = {PRICE_DATE} 종가 × {year}년 반기보고서의 발행 보통주식 수. 자기주식을 빼지 않고, 우선주는 "
        "네 그룹 모두 없습니다.",
        "- **PER** = 시가총액 / TTM 지배주주순이익. **PBR** = 시가총액 / 보통주자본.",
        f"- **보통주자본** = 지배주주지분 − 신종자본증권, {PRICE_DATE} 연결 재무상태표 기준.",
        "- **단순 ROE** = TTM 지배주주순이익 / 기말 보통주자본. 회사가 발표하는 ROE(평균 자본, 연환산 기준)와 다릅니다.",
        "- **2분기 CIR** = 일반관리비 / 총영업이익, 2분기 단독(상반기 누적 − 1분기). 총영업이익 = 영업이익 + 일반관리비 + "
        "신용손실충당금 전입액으로, 일반관리비와 충당금을 빼기 전의 영업수익입니다.",
        "- **범위** = 대상 그룹의 보통주자본 × 나머지 세 그룹 PBR의 최저·중간값·최고. 동종 배수가 가리키는 수준을 보여 줄 "
        "뿐이며 적정가치나 예측이 아닙니다. 추정치는 쓰지 않습니다.", "",
        "## 고지", "",
        "이 문서는 모든 수치를 공시로 거슬러 확인할 수 있는 작업 방식을 보여 주는 교육용 예시입니다. 투자 권유가 아니고, "
        "어떤 증권의 거래도 권하지 않으며, 투자의견과 목표주가를 제시하지 않습니다. 수치에 오류가 있을 수 있으니 이용하기 "
        "전에 인용한 공시를 확인하십시오.", "",
        "## 출처", "",
        "| 금융지주 | 보고서 | 접수번호 | 접수일 | 최초 접수 |", "|---|---|---|---|---|",
    ]
    for c in CODES:
        company = ds.companies[c]
        for y, p in ((year - 1, "H1"), (year - 1, "FY"), (year, "Q1"), (year, "H1")):
            st = company.statement(y, p, "CFS")
            first = f"{st['first_filed']} ({dart_link(st['first_rcept_no'])})" if st.get("first_rcept_no") else "같음"
            out.append(f"| {company.name} | {y}년 {st.get('form') or p} | {dart_link(st['rcept_no'])} | {st['filed']} | {first} |")
    out += ["", "주가: " + "; ".join(f"{ds.companies[c].name} `{ds.companies[c].data['price_sources'][PRICE_DATE]}`"
                                   for c in CODES) + "."]
    return "\n".join(out).rstrip() + "\n"
