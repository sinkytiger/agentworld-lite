# Canadian desk: Big 5 bank filings on the same protocol

The finance domain's research desk (a collector fetches filings, an analyst computes, a reporter submits, and every number is a cited fact) now also runs on real filings of Canada's five largest banks. This page covers where the numbers come from, how they are normalized, how they were checked, the three tasks built on them, and the sector note written from a finished run.

## Where Canadian filings can be collected

| Route | What it is | Automated use | In this project |
|---|---|---|---|
| SEDAR+ public website | Search, view and download filings | Not allowed. The terms of use prohibit scraping and automated access, and there is no public API | No crawler, by design |
| CSA Data Distribution Services (DDS) | Licensed bulk feed of SEDAR+ (real time), SEDI and NRD (daily) over SFTP | Yes, for subscribers. Pricing and schemas come from the DDS team | `sedar_dds`, a documented placeholder |
| SEC EDGAR | Cross-listed issuers file their annual report on Form 40-F and interim reports on Form 6-K, with XBRL | Yes, free, no key. The SEC asks for a User-Agent with a contact e-mail and at most 10 requests per second | `edgar` |
| Documents you downloaded yourself | PDFs from the SEDAR+ website or an issuer's site | You transcribe; the tool checks | `local_pdf` |

The 40-F and 6-K carry the same annual report, interim report, MD&A and press releases that the banks file on SEDAR+, so EDGAR is the automated route for cross-listed issuers. Issuers listed only in Canada, such as Loblaw, Dollarama, Alimentation Couche-Tard and National Bank, are not on EDGAR. For those, use `local_pdf` with documents downloaded by hand.

## Filing sources

All sources write the same dataset JSON (`agentworld_lite/finance/dataset.py`), so the desk, the tasks and the verifiers do not change between Korea and Canada. The interface lives in `agentworld_lite/finance/sources/`.

### `edgar`

```powershell
$env:SEC_USER_AGENT = "Your Name your.email@example.com"
```

```bash
python -m agentworld_lite.finance fetch --source edgar --company RY --periods 2025:Q3 2025:FY 2026:Q1 2026:H1 2026:Q3 --name "Royal Bank of Canada" --exchange TSX --alias RBC "Royal Bank" --cache C:/edgar_cache
```

- **One filing per period.** The adapter reads the XBRL instance of the filing that first reported each period, not the `companyfacts` API. In October 2026 `companyfacts` still had no bank 6-K after February 2026, although the May and August 6-Ks carry XBRL. It also repeats every comparative a value appeared in, which loses the original filing date.
- **Fiscal, year-to-date periods.** `Q1`, `H1`, `Q3` and `FY` cover 3, 6, 9 and 12 months from the start of the fiscal year, as in DART. The fiscal year is named for the calendar year it ends in. The banks' year ends on October 31, so fiscal 2026 Q3 is November 2025 to July 2026.
- **Matching a filing.** The period end comes from the company's fiscal year end. A filing qualifies when its report date matches, it has XBRL, and its `dei:DocumentFiscalPeriodFocus` is that period. A Q2 report is the H1 period. Banks file thousands of structured-note supplements, so older submission pages are read when the recent list does not reach back far enough.
- **Choosing a value.** Duration accounts use the context that ends on the period end and spans the expected months. Balance-sheet accounts use the period-end instant. Only contexts without dimensions are used, which are the consolidated totals.
- **Extension concepts.** Banks tag several items in their own namespace, so each account lists candidates in order and `ext:Name` matches an extension element by its local name. The concept that matched is stored on every fact.

| Account | RY | TD | BNS | BMO | CM |
|---|---|---|---|---|---|
| non_interest_expense | `NonInterestExpense` | `NonInterestExpense1` | `NonInterestExpense1` | `NonInterestExpense1` | `NonInterestExpense1` |
| cet1_ratio | `CommonEquityTierOneCapitalRatio1` | `CommonEquityTier1CapitalRatio1` | `CommonEquityTierOneCapitalRatio1` | `CommonEquityTierOneCapitalRatio1` | `CET1CapitalRatio` |

- **Cache.** Only the parsed facts without dimensions are cached, as small JSON files (about 2 MB for a bank's five periods instead of 10 MB per raw instance). The default is `data/finance/cache/edgar`, which is gitignored. Pass `--cache` to keep it outside a synced folder such as OneDrive.

### `local_pdf`

For filings you downloaded from the SEDAR+ website. Write a YAML manifest that gives, for each figure, the document, the page and the number as printed. The source then checks two things before accepting it.

1. The printed text appears on that page.
2. The value equals the printed number times 1, 1,000, a million or a billion.

It also stores each document's SHA-256 and refuses a manifest whose file has changed. PDFs need `pip install pypdf`. Text files with pages separated by form feeds and HTML files also work. The documents stay on your disk, and only the numbers and their citations go into the dataset. The manifest format is in the module docstring.

```bash
python -m agentworld_lite.finance fetch --source local_pdf --manifest my_filings/loblaw.yaml
```

### `sedar_dds`

A placeholder that raises a clear error. It documents how a subscriber would connect the DDS feed: point `DDS_DELIVERY_DIR` at the SFTP sync directory, take the filing date from the feed's metadata for the as-of guard, and run the same citation checks on the documents.

## The Big 5 dataset (`data/finance/edgar`)

Five periods per bank: fiscal 2025 Q3 and FY, and fiscal 2026 Q1, H1 and Q3. Every statement records the form, the accession number, the filing date and the period end.

| Bank | Ticker | Accounts per period | Not tagged |
|---|---|---|---|
| Royal Bank of Canada | RY | 10 | |
| The Toronto-Dominion Bank | TD | 8 | `net_income_owners` and `equity_owners`. TD tags income for common shareholders and for preferred holders separately; the values are left empty rather than derived |
| The Bank of Nova Scotia | BNS | 10 | |
| Bank of Montreal | BMO | 10 | |
| Canadian Imperial Bank of Commerce | CM | 10 | |

Accounts: `revenue` (total revenue), `non_interest_expense`, `net_income`, `net_income_owners`, `net_income_common`, `eps_basic`, `eps_diluted`, `equity_owners`, `equity_total`, `cet1_ratio` (percent).

**Cross-check.** The task inputs were compared with the banks' own Q3 2026 releases (Exhibit 99.1 of each 6-K).

| Figure | Bank release | From the dataset |
|---|---|---|
| RBC Q3 total revenue | 18,538 | 53,951 − 35,413 = 18,538 |
| RBC Q3 non-interest expense | 9,789 | 28,689 − 18,900 = 9,789 |
| RBC diluted EPS, Q3 / 9M 2026 / 9M 2025 | 4.23 / 12.10 / 10.31 | 12.10 − 7.87 = 4.23 / 12.10 / 10.31 |
| RBC net income available to common, 9M 2026 / 9M 2025 | 16,894 / 14,575 | 16,894 / 14,575 |
| TD Q3 total revenue | 16,885 | 49,267 − 32,382 = 16,885 |
| TD reported efficiency ratio, Q3 / 9M | 50.2% / 52.0% | 50.19% / 51.96% |
| Q3 reported net income, TD / BNS / BMO / CIBC | 4,615 / 2,953 / 1,750 / 2,409 | 12,909 − 8,294 / 7,884 − 4,931 / 6,869 − 5,119 / 7,974 − 5,565 |
| CET1 ratio at July 31, 2026, RY / TD / BNS / BMO / CIBC | 13.5% / 14.3% / 13.1% / 13.0% / 13.4% | the same |

Amounts in C$ millions. For c03, total net income and the CET1 ratio were compared with each bank's Q3 2026 release; the split between common and preferred holders (`net_income_common`) is the XBRL as filed. BMO's reported Q3 includes a C$962 million after-tax goodwill charge tied to the announced sale of its Transportation Finance and Vendor Finance businesses, so its reported quarter sits well below its adjusted earnings. The tasks use reported figures.

**Share prices** are not in the XBRL. RBC's TSX closes at quarter end (C$293.41 on 2026-07-31 and C$177.79 on 2025-07-31) come from the "Common share price (RY on TSX)" line of its Q3 2026 Report to Shareholders (Exhibit 99.2 of 6-K 0001193125-26-369461), and the dataset stores that citation.

## Tasks (`tasks/finance_ca`)

All tasks come as matched shared / private pairs, like the Korean ones: the methodology sentences sit either in the shared document or in the analyst's private brief. The as-of date is 2026-09-30 and the tolerance is 0.1%, tight enough to separate basic from diluted EPS.

### c01: RBC trailing P/E at fiscal Q3 2026

TTM = FY 2025 − Q3 YTD 2025 + Q3 YTD 2026. Answers: TTM net income available to common shareholders **C$22,187 million**, and P/E = 293.41 / (14.07 − 10.31 + 12.10) = **18.50x**.

| Mistake | P/E | Off by |
|---|---|---|
| Basic instead of diluted EPS | 18.45 | −0.25% |
| Nine-month EPS used as annual | 24.25 | +31% |
| Fiscal 2025 EPS (stale) | 20.85 | +13% |
| One quarter × 4 | 17.34 | −6.3% |
| Price of a year earlier | 11.21 | −39% |

Using net income attributable to all shareholders (C$22,745 million) or net income including non-controlling interests (C$22,752 million) misses by about 2.5%.

### c02: RBC and TD efficiency ratios for fiscal Q3 2026 alone

Single quarter = Q3 YTD − H1 YTD. Answers: RBC **52.80%**, TD **50.19%**.

| Mistake | RBC | TD |
|---|---|---|
| Nine-month ratio | 53.18% (+0.7%) | 51.96% (+3.5%) |
| Six-month ratio | 53.37% (+1.1%) | 52.88% (+5.4%) |
| Fraction instead of percent | 0.528 | 0.502 |

### c03: Big 5 earnings and capital at fiscal Q3 2026

Seven answers across all five banks: each bank's net income available to common shareholders for fiscal Q3 2026 alone (Q3 YTD − H1 YTD), and the simple average and the spread (highest minus lowest) of the five CET1 ratios at July 31, 2026.

| Bank | Q3 net income to common | CET1 |
|---|---|---|
| RY | 16,894 − 11,015 = **5,879** | 13.5% |
| TD | 12,512 − 7,991 = **4,521** | 14.3% |
| BNS | 7,401 − 4,623 = **2,778** | 13.1% |
| BMO | 6,563 − 4,896 = **1,667** | 13.0% |
| CM | 7,601 − 5,330 = **2,271** | 13.4% |

CET1 average **13.46%**; spread 14.3 − 13.0 = **1.30 pp** (TD highest, BMO lowest). Amounts in C$ millions.

| Mistake | Off by |
|---|---|
| Nine-month or six-month figure as the quarter | +66% to +294% |
| `net_income` (incl. non-controlling interests and preferred holders) | +2.1% (TD) to +6.3% (BNS) |
| `net_income_owners` where tagged (TD has none, which invites mixing definitions) | +2.4% (RY) to +5.6% (CM) |
| Nine months ÷ 3 | −11% (BNS) to +31% (BMO) |
| Fiscal Q2 alone (H1 − Q1) | −11% to +49% |
| Stale quarter, fiscal Q4 2025 (FY − Q3 YTD 2025) | −32% to +27% |
| CET1 average from H1, Q1, FY 2025 or Q3 2025 | 13.54 to 13.64 (+0.6% to +1.3%) |
| CET1 spread from Q1, FY 2025 or Q3 2025 | 1.4 to 1.6 (+7.7% to +23%) |
| Median instead of mean | 13.40 (−0.45%) |
| Average of four banks (TD left out) | 13.25 (−1.6%) |
| Spread in basis points, as a fraction, or relative (14.3 / 13.0 − 1) | 130, 0.013, 10.0 |

H1 ratios give the same spread, because TD and BMO did not move between April and July. The spread alone would let that period mistake through, so the average is asked too.

The scripted reference solves c01 in 7 rounds of 16, c02 in 11 rounds of 18 and c03 in 18 rounds of 30 (ten fetches by the collector, then seven calculations by the analyst). An idle team fails all three.

## Sector note

```bash
python -m agentworld_lite.finance note --trajectory runs/finance_ca_gemma/trajectories/c03_big5_q3_sector_shared__full__s7.json --out reports/c03_note.md
```

```bash
python -m agentworld_lite.finance note --reference c03_big5_q3_sector_shared
```

`agentworld_lite/finance/note.py` turns a finished trajectory into an English Markdown note, without calling a model. It replays the recorded actions on a fresh desk (the environment is deterministic) to recover each submitted fact and its provenance, and it refuses the trajectory if the replay does not reproduce the recorded submission. Every figure is followed by its fact id and its filing, and a derived figure lists every filing behind it:

```
C$5,879 million [fact `calc.ry_q3_net_income_common` from 6-K 0001193125-26-369461; 6-K 0001193125-26-242928]
```

The note has a comparison table (per-bank answers, plus inputs taken from every bank, such as the CET1 ratios), the sector-wide answers, the derivation of each answer down to the filed figures, and a sources table that links each accession number to its EDGAR folder. Before returning, it checks its own text: every figure (a number with a currency sign, decimal point, thousands separator or unit) must be followed by a citation. Notes are written only for successful runs unless `--allow-failed` is passed, in which case each answer is marked within or outside tolerance. `--reference TASK_ID` replays the task's scripted reference solution instead of a saved run.

## Demo

```bash
pip install -e ".[demo]"
```

```bash
streamlit run demo/streamlit_app.py
```

A Streamlit app over the same code, offline (no model, no network, no key). It has four views.

- **Desk replay.** A round slider over the scripted reference or an uploaded trajectory. Each agent's moves, the size of its notebook, and which answers are within tolerance so far.
- **Big 5 data.** Single-quarter net income to common and the CET1 ratio for each bank, as charts and as a table that names the filing behind each figure.
- **Sector note.** The cited note above, with a Markdown download.
- **Task.** The brief, roles and tool limits, where the methodology sits (shared document or private briefs), and the answer key.

An uploaded trajectory is replayed first and rejected if it does not reproduce on the dataset. Query parameters open a given state, e.g. `?task=c03_big5_q3_sector_shared&view=Desk%20replay&round=11`.

`python demo/record_gif.py --out demo.gif` records a GIF of the app. It starts the app, drives a headless Chrome or Edge through the DevTools protocol, waits until each view has finished rendering, and joins the screenshots with Pillow (about 0.5 MB for 12 frames). For Streamlit Community Cloud, point the app at `demo/streamlit_app.py`; `demo/requirements.txt` lists what it installs. That deployment has not been tried yet.

## Running

```bash
python -m agentworld_lite.finance validate --tasks tasks/finance_ca
```

```bash
python -m agentworld_lite.finance run --tasks tasks/finance_ca --agent llm --provider ollama --model gemma4:cloud --settings full no_comm --seeds 7 8 9 --out runs/finance_ca_gemma
```

```bash
python -m agentworld_lite evaluate --run runs/finance_ca_gemma --judge rule
```

```bash
python -m agentworld_lite pair-report --runs runs/finance_ca_gemma --out reports/finance_ca_pairs.md
```

The Canadian desk gets its own system prompt and tool descriptions (fiscal periods, CAD units, no share-count tool). No model has been run on these tasks yet.

## Limits

- Values are as originally filed. A later filing may restate a comparative; the dataset keeps the first figure and its date, which is what the as-of guard needs, but it does not flag restatements.
- Extension concepts are matched by name. A bank that renames an element needs a new candidate in `ACCOUNT_RULES`; the stored `concept` shows what matched.
- Period matching allows ±7 days around the computed period end, which suits month-end fiscal years such as the banks'. 52/53-week fiscal years have not been tested.
- Share prices and share counts at period end come from the report text, not from XBRL, so they are added by hand with a citation.
- Fact labels describe every account of a filing as "year-to-date", including period-end ratios such as CET1, which the sector note repeats.
