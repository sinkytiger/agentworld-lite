"""Charts for the English write-up (docs/overview_en.md), from committed files only:

    python demo/make_charts.py            # writes docs/img/chart_*.png

- chart_models.png      success rate and cost per solved episode by model (docs/finance_kr_leaderboard.md)
- chart_valueup_roe.png the Korean groups' TTM ROE now, a year earlier and their value-up ROE target
- chart_objectives_roe.png  the Canadian Big 5's fiscal 2025 ROE against their quoted medium-term ROE objective
- chart_cet1.png        CET1 ratios of the Korean groups (2026-06-30, quoted from H1 reports) and the Canadian Big 5
                        (2026-07-31, XBRL of the Q3 reports)

Colours are the first slots of the reference categorical palette (blue, orange), validated for colour vision deficiency
in that palette's documentation; text stays in ink colours, and every chart labels its values directly.
"""

from __future__ import annotations

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

from agentworld_lite.finance import valueup as vu  # noqa: E402

OUT = ROOT / "docs" / "img"
SURFACE, INK, INK2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
BLUE, ORANGE = "#2a78d6", "#eb6834"
NAMES_EN = {"105560": "KB Financial", "055550": "Shinhan Financial", "086790": "Hana Financial",
            "316140": "Woori Financial"}
MODELS_EN = {"claude-opus-5-5": "Claude Opus 5.5", "claude-sonnet-5-5": "Claude Sonnet 5.5",
             "claude-haiku-5-5": "Claude Haiku 5.5", "ollama:qwen3:4b": "qwen3:4b (local)"}

plt.rcParams.update({"font.family": ["Segoe UI", "DejaVu Sans"], "font.size": 10, "axes.edgecolor": AXIS,
                     "axes.labelcolor": INK2, "xtick.color": MUTED, "ytick.color": INK2, "text.color": INK,
                     "axes.titlesize": 12, "axes.titleweight": "bold", "axes.titlelocation": "left"})


def _frame(ax, xgrid=True):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(AXIS)
    ax.tick_params(axis="y", length=0)
    if xgrid:
        ax.grid(axis="x", color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)


def _save(fig, name: str, source: str) -> Path:
    fig.text(0.01, -0.04, source, color=MUTED, fontsize=8, ha="left", va="top")  # below the axis labels
    fig.patch.set_facecolor(SURFACE)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    fig.savefig(path, dpi=200, facecolor=SURFACE, bbox_inches="tight", pad_inches=0.25)
    plt.close(fig)
    return path


def leaderboard_rows(path: Path = ROOT / "docs" / "finance_kr_leaderboard.md") -> list[dict]:
    """The summary table of the generated leaderboard."""
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and cells[0] in MODELS_EN and len(cells) >= 12:
            n = int(cells[1])
            won = int(cells[2].split()[0])
            per = cells[11]
            rows.append({"model": MODELS_EN[cells[0]], "n": n, "won": won,
                         "cost_per_success": float(per.strip("$")) if per.startswith("$") else None})
    return rows


def chart_models() -> Path:
    rows = leaderboard_rows()
    fig, (a, b) = plt.subplots(1, 2, figsize=(10, 3.2), gridspec_kw={"wspace": 0.55})
    names = [r["model"] for r in rows][::-1]
    rate = [r["won"] / r["n"] * 100 for r in rows][::-1]
    a.barh(names, rate, color=BLUE, height=0.5)
    for y, r in enumerate(rows[::-1]):
        a.text(r["won"] / r["n"] * 100 + 2, y, f"{r['won']}/{r['n']}", va="center", color=INK)
    a.set_xlim(0, 118)
    a.set_title("Episodes solved (%)")
    _frame(a)
    cost = [r["cost_per_success"] or 0 for r in rows][::-1]
    b.barh(names, cost, color=BLUE, height=0.5)
    for y, r in enumerate(rows[::-1]):
        c = r["cost_per_success"]
        b.text((c or 0) + 0.015, y, f"${c:.3f}" if c else "none solved", va="center", color=INK if c else INK2)
    b.set_xlim(0, max(c for c in cost) * 1.3)
    b.set_title("Cost per solved episode (US$)")
    _frame(b)
    fig.suptitle("Korean desk, tasks k01-k06: who solved what, at what cost", x=0.01, ha="left", fontsize=13,
                 fontweight="bold", y=1.08)
    return _save(fig, "chart_models.png", "Source: docs/finance_kr_leaderboard.md (saved runs replayed; Opus ran "
                 "k01-k05, seed 7 only; qwen3:4b partial). Costs: token counts x list prices.")


def chart_valueup_roe() -> Path:
    rows = [r for r in vu.scorecard_rows() if r["target"].metric == "roe"]
    fig, ax = plt.subplots(figsize=(8, 2.8))
    for y, r in enumerate(rows[::-1]):
        t = r["target"]
        now, prev = r["now"][0], r["prev"][0]
        ax.plot([prev, now], [y, y], color=AXIS, linewidth=2, zorder=1)
        ax.scatter([prev], [y], s=70, facecolor=SURFACE, edgecolor=BLUE, linewidth=2, zorder=3)
        ax.scatter([now], [y], s=80, color=BLUE, edgecolor=SURFACE, linewidth=2, zorder=4)
        ax.plot([t.threshold, t.threshold], [y - 0.28, y + 0.28], color=INK, linewidth=2.5, zorder=2)
        ax.text(now, y + 0.3, f"{now:.2f}%", ha="center", va="bottom", color=INK, fontsize=9)
        ax.text(t.threshold, y - 0.32, f"target {t.threshold:g}%" + ("" if t.deadline else " (no deadline)"),
                ha="center", va="top", color=INK2, fontsize=8.5)
    ax.set_yticks(range(len(rows)), [NAMES_EN[r["target"].code] for r in rows[::-1]])
    ax.set_ylim(-0.8, len(rows) - 0.3)
    ax.set_xlim(8, 12.8)
    ax.xaxis.set_major_formatter(lambda v, _: f"{v:g}%")
    ax.set_title("Korea's value-up targets: TTM ROE at 2026-06-30 (filled) vs a year earlier (hollow)")
    _frame(ax)
    return _save(fig, "chart_valueup_roe.png", "Source: DART filings; targets quoted verbatim from each group's "
                 "value-up plan (Woori's read from the page images of its plan PDF; receipt numbers in\n"
                 "docs/finance_korea_fhc.md). ROE = TTM net income to owners / average owners' equity.")


def chart_objectives_roe() -> Path:
    from agentworld_lite.finance import objectives_ca as oc
    rows = []
    for bank in oc.DOCS:
        r = oc.roe_check(bank)
        rows.append((oc.NAMES[bank], r["objective"], r["tested"].value, r["other"] and r["other"].value))
    fig, ax = plt.subplots(figsize=(8, 4.4))
    for y, (_, o, tested, other) in enumerate(rows[::-1]):
        if other is not None:
            ax.plot([other, tested], [y, y], color=AXIS, linewidth=2, zorder=1)
            ax.scatter([other], [y], s=70, facecolor=SURFACE, edgecolor=ORANGE, linewidth=2, zorder=3)
            if abs(other - tested) >= 1:                                  # CIBC's two figures sit 0.1 pt apart
                ax.text(other, y + 0.2, f"{other:.1f}%", ha="center", va="bottom", color=INK2, fontsize=8.5)
        ax.scatter([tested], [y], s=80, color=ORANGE, edgecolor=SURFACE, linewidth=2, zorder=4)
        ax.plot([o.low, o.low], [y - 0.2, y + 0.2], color=INK, linewidth=2.5, zorder=2)
        ax.text(tested, y + 0.2, f"{tested:.1f}%", ha="center", va="bottom", color=INK, fontsize=9)
        goal = f"~{o.low:g}%" if o.high == o.low else f"{o.low:g}%+"
        ax.text(o.low, y - 0.22, f"objective {goal}" + (" adj." if o.basis == "adjusted" else ""), ha="center",
                va="top", color=INK2, fontsize=8.5)
    ax.set_yticks(range(len(rows)), [n for n, *_ in rows[::-1]])
    ax.set_ylim(-0.8, len(rows) - 0.3)
    ax.set_xlim(8.5, 19)
    ax.xaxis.set_major_formatter(lambda v, _: f"{v:g}%")
    ax.scatter([], [], color=ORANGE, s=60, label="on the objective's basis")
    ax.scatter([], [], facecolor=SURFACE, edgecolor=ORANGE, linewidth=2, s=50, label="the other basis")
    ax.legend(loc="upper left", frameon=False, fontsize=9)
    ax.set_title("Canada's Big 5: fiscal 2025 ROE vs the medium-term ROE objective")
    _frame(ax)
    return _save(fig, "chart_objectives_roe.png", "Source: each bank's fiscal 2025 annual filing (40-F MD&A exhibit, "
                 "SEC EDGAR), objectives and ROE quoted verbatim\n(python -m agentworld_lite.finance objectives-ca). "
                 "ROE as each bank computes it, on average common equity.")


def chart_cet1() -> Path:
    kr = [(NAMES_EN[c], vu.cet1_fact(c).value) for c in vu.CODES if vu.cet1_fact(c)]
    ca = []
    for t in ("RY", "TD", "BNS", "BMO", "CM"):
        d = json.loads((ROOT / "data" / "finance" / "edgar" / f"{t}.json").read_text(encoding="utf-8"))
        ca.append((d["name"], d["statements"]["2026:Q3:CFS"]["cet1_ratio"]))
    rows = [(n, v, "Korea") for n, v in sorted(kr, key=lambda x: x[1])] + \
           [(n, v, "Canada") for n, v in sorted(ca, key=lambda x: x[1])]
    fig, ax = plt.subplots(figsize=(8, 3.8))
    for y, (n, v, country) in enumerate(rows[::-1]):
        ax.scatter([v], [y], s=80, color=BLUE if country == "Korea" else ORANGE, edgecolor=SURFACE, linewidth=2,
                   zorder=3)
        ax.text(v + 0.04, y, f"{v:.2f}%", va="center", color=INK, fontsize=9)
    ax.axvline(13, color=INK2, linewidth=1, linestyle=(0, (4, 3)), zorder=1)
    ax.text(13.02, len(rows) - 0.45, "13%: the Korean groups' value-up CET1 target", color=INK2, fontsize=8.5,
            va="center")
    ax.set_yticks(range(len(rows)), [re.sub(r"\s+", " ", n) for n, _, _ in rows[::-1]])
    ax.set_xlim(12.8, 14.6)
    ax.set_ylim(-0.6, len(rows) + 0.1)
    ax.xaxis.set_major_formatter(lambda v, _: f"{v:.1f}%")
    ax.scatter([], [], color=BLUE, s=60, label="Korea, 2026-06-30")
    ax.scatter([], [], color=ORANGE, s=60, label="Canada, 2026-07-31")
    ax.legend(loc="upper right", bbox_to_anchor=(1.0, 0.93), frameon=False, fontsize=9)
    ax.set_title("CET1 ratio: Korea's financial groups and Canada's Big 5 banks")
    _frame(ax)
    return _save(fig, "chart_cet1.png", "Source: Korea, each group's 2026 H1 report on DART (Woori gives no 2026 H1 "
                 "group CET1); Canada, XBRL of each bank's fiscal Q3 2026 report on SEC EDGAR.")


if __name__ == "__main__":
    for make in (chart_models, chart_valueup_roe, chart_objectives_roe, chart_cet1):
        print(f"wrote {make().relative_to(ROOT)}")
