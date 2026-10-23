"""A six-page carousel (PDF, plus one PNG per page) for a LinkedIn post about the Korea and Canada desks, drawn from the
committed data only, in the palette of demo/make_charts.py, sized for a phone (1080 x 1350 pixels per page):

    python demo/make_carousel.py --out <folder>

Every figure on the pages is computed from the repository's files (objectives_ca, valueup, the leaderboard and the AI
report fact-check), so a rerun after new filings updates the pages. Educational project, not investment advice.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402

from agentworld_lite.finance import objectives_ca as oc  # noqa: E402
from agentworld_lite.finance import valueup as vu  # noqa: E402
from make_charts import (AXIS, BLUE, GRID, INK, INK2, MUTED, NAMES_EN, ORANGE, SURFACE,  # noqa: E402
                         leaderboard_rows)

W, H, DPI = 10.8, 13.5, 100                      # 1080 x 1350 px
BIG, TITLE, BODY, LABEL, NOTE = 96, 38, 22, 18, 14  # the five type sizes
LEFT = 0.08
REPO = "github.com/sinkytiger/agentworld-lite"
plt.rcParams.update({"font.family": ["Segoe UI", "DejaVu Sans"], "text.color": INK})


def facts() -> dict:
    roe = {b: oc.roe_check(b) for b in oc.DOCS}
    kr = [r for r in vu.scorecard_rows() if r["target"].metric == "roe"]
    fc = json.loads((ROOT / "data" / "finance" / "ai_report" / "factcheck.json").read_text(encoding="utf-8"))
    gain = re.search(r"\$([\d,]+) million", oc.ITEMS_OF_NOTE["TD"][0]).group(1)
    return {"roe": roe, "kr": kr, "models": leaderboard_rows(), "fc": {k: v["summary"] for k, v in fc.items()},
            "meets": [b for b, r in roe.items() if r["verdict"] in ("meets", "at target", "above range")],
            "quotes": sum(len(oc.quotes(b)) for b in oc.DOCS), "td_gain": gain}


def page(n: int, total: int):
    fig = plt.figure(figsize=(W, H), dpi=DPI)
    fig.patch.set_facecolor(SURFACE)
    fig.text(LEFT, 0.035, REPO, fontsize=NOTE, color=MUTED, ha="left")
    fig.text(1 - LEFT, 0.035, f"{n} / {total}", fontsize=NOTE, color=MUTED, ha="right")
    return fig


def heading(fig, text: str, y: float = 0.9) -> None:
    fig.text(LEFT, y, text, fontsize=TITLE, fontweight="bold", va="top", wrap=True)


def lines(fig, rows: list[str], y: float, size: int = BODY, color: str = INK, gap: float = 0.038) -> float:
    for row in rows:
        fig.text(LEFT, y, row, fontsize=size, color=color, va="top")
        y -= gap
    return y


def _frame(ax) -> None:
    ax.set_facecolor(SURFACE)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(AXIS)
    ax.tick_params(axis="y", length=0, labelsize=LABEL, labelcolor=INK)
    ax.tick_params(axis="x", length=0, labelsize=NOTE, labelcolor=MUTED)
    ax.grid(axis="x", color=GRID, linewidth=1)
    ax.set_axisbelow(True)


def dumbbell(ax, rows, color, xlim, decimals: int = 1) -> None:
    """rows: (label, value on the basis that counts, other value or None, target, target wording), top to bottom."""
    for y, (_, value, other, target, wording) in enumerate(rows[::-1]):
        if other is not None:
            ax.plot([other, value], [y, y], color=AXIS, linewidth=3, zorder=1)
            ax.scatter([other], [y], s=260, facecolor=SURFACE, edgecolor=color, linewidth=3, zorder=3)
        ax.scatter([value], [y], s=300, color=color, edgecolor=SURFACE, linewidth=2, zorder=4)
        ax.plot([target, target], [y - 0.24, y + 0.24], color=INK, linewidth=4, zorder=2)
        ax.text(value, y + 0.24, f"{value:.{decimals}f}%", ha="center", va="bottom",
                fontsize=LABEL, fontweight="bold", color=INK)
        ax.text(target, y - 0.28, wording, ha="center", va="top", fontsize=NOTE, color=INK2)
    ax.set_yticks(range(len(rows)), [r[0] for r in rows[::-1]])
    ax.set_ylim(-0.75, len(rows) - 0.35)
    ax.set_xlim(*xlim)
    ax.xaxis.set_major_formatter(lambda v, _: f"{v:g}%")
    _frame(ax)


def p1(f, total):
    fig = page(1, total)
    fig.text(LEFT, 0.86, "Do banks keep the promises\nthey publish?", fontsize=TITLE, fontweight="bold", va="top")
    fig.text(LEFT, 0.66, f"{len(f['meets'])} of {len(f['roe'])}", fontsize=BIG, fontweight="bold", color=ORANGE,
             va="top")
    lines(fig, ["of Canada's Big 5 banks meet their own", "medium-term ROE objective (fiscal 2025)."], 0.53)
    lines(fig, ["Three AI agents checked Korea's four financial", "groups and Canada's Big 5 against their own",
                "filings. Every number cites the filing it came from."], 0.36, color=INK2)
    return fig


def p2(f, total):
    fig = page(2, total)
    heading(fig, "Why Korea and Canada")
    y = 0.8
    for head, body in (
            ("Comparable systems.", ["A few large banking groups dominate both", "markets; both report capital under Basel III."]),
            ("Both publish targets.", ["Korea's groups: value-up plans.", "Canada's banks: medium-term financial objectives."]),
            ("Different filing systems.", ["DART vs SEDAR+ and SEC EDGAR: a fair test", "that the method is not tuned to one market."]),
            ("My next step.", ["I'm preparing to work in Canada's financial", "sector, so I applied the same method there."])):
        fig.text(LEFT, y, head, fontsize=BODY, fontweight="bold", va="top")
        y = lines(fig, body, y - 0.042, color=INK2) - 0.04
    return fig


def p3(f, total):
    fig = page(3, total)
    heading(fig, "Canada: fiscal 2025 ROE\nvs each bank's own objective")
    rows = []
    for b, r in f["roe"].items():
        o = r["objective"]
        goal = f"~{o.low:g}%" if o.high == o.low else f"{o.low:g}%+"
        rows.append((oc.NAMES[b], r["tested"].value, r["other"] and r["other"].value, o.low,
                     f"objective {goal}" + (" adj." if o.basis == "adjusted" else "")))
    ax = fig.add_axes([0.3, 0.33, 0.62, 0.42])
    dumbbell(ax, rows, ORANGE, (8.5, 19))
    td = f["roe"]["TD"]
    lines(fig, ["Filled: ROE on the objective's basis. Hollow: the other basis."], 0.27, size=NOTE, color=MUTED)
    lines(fig, [f"TD's reported {td['other'].value:.1f}% includes a C${f['td_gain']} million",
                f"pre-tax gain on selling Schwab shares. On the adjusted",
                f"basis its objective uses, it is {td['tested'].value:.1f}%."], 0.22, color=INK2, gap=0.038)
    return fig


def p4(f, total):
    fig = page(4, total)
    heading(fig, "Korea: TTM ROE vs\nvalue-up targets")
    rows = []
    for r in f["kr"]:
        t = r["target"]
        mark = "*" if vu.from_image(t) else ""
        rows.append((NAMES_EN[t.code] + mark, r["now"][0], r["prev"][0], t.threshold,
                     f"target {t.threshold:g}%" + (f" by {t.deadline[:4]}" if t.deadline else "")))
    ax = fig.add_axes([0.3, 0.33, 0.62, 0.42])
    dumbbell(ax, rows, BLUE, (8, 12.8), decimals=2)
    lines(fig, ["Filled: at 2026-06-30. Hollow: a year earlier.",
                "* Read from the page images of Woori's plan PDF (no text to match)."], 0.27, size=NOTE, color=MUTED,
          gap=0.03)
    met = [NAMES_EN[r["target"].code] for r in f["kr"] if r["status"] == "달성"]
    lines(fig, [f"{', '.join(met)} meets its ROE target. The others are short;",
                "only Shinhan has a deadline (2027) and is on track."], 0.2, color=INK2, gap=0.038)
    return fig


def p5(f, total):
    fig = page(5, total)
    heading(fig, "The agents: who solved what")
    models = f["models"]
    ax = fig.add_axes([0.36, 0.5, 0.56, 0.3])
    for y, m in enumerate(models[::-1]):
        rate = m["won"] / m["n"] * 100
        ax.barh([y], [rate], color=BLUE, height=0.55)
        cost = f", ${m['cost_per_success']:.3f} per solve" if m["cost_per_success"] else ""
        ax.text(rate + 2, y, f"{m['won']}/{m['n']}{cost}", va="center", fontsize=NOTE, color=INK)
    ax.set_yticks(range(len(models)), [m["model"].replace("Claude ", "") for m in models[::-1]])
    ax.set_xlim(0, 175)
    ax.set_xticks([0, 50, 100])
    ax.xaxis.set_major_formatter(lambda v, _: f"{v:g}%")
    _frame(ax)
    nod, dat = f["fc"]["nodata"], f["fc"]["data"]
    lines(fig, [f"Episodes solved on the Korean tasks ({sum(m['n'] for m in models)} in all).",
                "No model submitted a wrong number."], 0.44, size=NOTE, color=MUTED, gap=0.03)
    lines(fig, ["All six Haiku failures: the method sat only in one",
                "agent's private brief, and chat was blocked.",
                f"With no data, Opus 5.5 wrote \"needs checking\" {nod['확인 필요 표시']} times;",
                f"with cited data, {dat['일치']} of {dat['figures']} figures matched the filings."], 0.35, color=INK2,
          gap=0.038)
    return fig


def p6(f, total):
    fig = page(6, total)
    heading(fig, "How it is checked")
    y = lines(fig, [f"{f['quotes']} quotes from the five Canadian annual reports,",
                    "each matched against the filed document, whose",
                    "SHA-256 is recorded.",
                    "",
                    "Every figure carries its filing; a rule-based",
                    "checker refuses to print an uncited number.",
                    "",
                    "Share prices are left out: they cannot be",
                    "checked against a filing."], 0.79, color=INK2, gap=0.038)
    fig.text(LEFT, 0.33, "Try it", fontsize=BODY, fontweight="bold", va="top")
    lines(fig, ["Live demo: agentworld-canada-desk.streamlit.app", f"Code and write-up: {REPO}"], 0.28, gap=0.038)
    lines(fig, ["Educational project, not investment advice."], 0.15, size=NOTE, color=MUTED)
    return fig


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="folder for carousel.pdf and page_1.png ... page_6.png")
    out = Path(ap.parse_args(argv).out)
    out.mkdir(parents=True, exist_ok=True)
    f = facts()
    makers = (p1, p2, p3, p4, p5, p6)
    with PdfPages(out / "carousel.pdf") as pdf:
        for i, make in enumerate(makers, 1):
            fig = make(f, len(makers))
            fig.savefig(out / f"page_{i}.png", dpi=DPI, facecolor=SURFACE)
            pdf.savefig(fig, facecolor=SURFACE)
            plt.close(fig)
    print(f"wrote {out / 'carousel.pdf'} and {len(makers)} PNG pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
