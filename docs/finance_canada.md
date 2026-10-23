# Canadian desk: Big 5 bank filings on the same protocol

The finance domain's research desk (a collector fetches filings, an analyst computes, a reporter submits, and every number is a cited fact) now also runs on real filings of Canada's five largest banks. This page covers where the numbers come from, how they are normalized, how they were checked, the three tasks built on them, and the sector note written from a finished run.

## Where Canadian filings can be collected

| Route | What it is | Automated use | In this project |
|---|---|---|---|
| SEDAR+ public website | Search, view and download filings | Not allowed. The terms of use prohibit scraping and automated access, and there is no public API | No crawler, by design |
| CSA Data Distribution Services (DDS) | Licensed bulk feed of SEDAR+ (real time), SEDI and NRD (daily) over SFTP | Yes, for subscribers. Pricing and schemas come from the DDS team | `sedar_dds`, a documented placeholder |
| SEC EDGAR | Cross-listed issuers file their annual report on Form 40-F and interim reports on Form 6-K, with XBRL | Yes, free, no key. The SEC asks for a User-Agent with a contact e-mail and at most 10 requests per second | `edgar` |
| Documents you downloaded yourself | PDFs from the SEDAR+ website or an issuer's site | You transcribe; the tool checks | `local_pdf` |

We asked the CSA in October 2026: bulk access to SEDAR+ filings is available only through DDS, a paid institutional subscription.

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
| rwa | `TotalCapitalRiskWeightedAssets` | `RiskWeightedAssetsUsedInCalculatingTotalCapitalRatio` | `RiskWeightedAssets` | `CommonEquityTierOneCapitalRatioRiskWeightedAssets` | `TotalCapitalRiskWeightedAssets` |

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

Six periods per bank: fiscal 2025 H1, Q3 and FY, and fiscal 2026 Q1, H1 and Q3. Every statement records the form, the accession number, the filing date and the period end. Fiscal 2025 H1 (the 6-Ks for the period ending 2025-04-30, which give the year-ago quarter in the earnings review) was added on 2026-10-23: sec.gov was refusing this machine's scripted requests, so the five XBRL instances were fetched in a browser and saved as one bundle, their SHA-256 checked against what sec.gov served, and parsed by the same `EdgarSource.statement()` as every other period.

| Bank | Ticker | Accounts per period | Not tagged |
|---|---|---|---|
| Royal Bank of Canada | RY | 12 | |
| The Toronto-Dominion Bank | TD | 10 | `net_income_owners` and `equity_owners`. TD tags income for common shareholders and for preferred holders separately; the values are left empty rather than derived |
| The Bank of Nova Scotia | BNS | 12 | |
| Bank of Montreal | BMO | 12 | |
| Canadian Imperial Bank of Commerce | CM | 12 | |

Accounts: `revenue` (total revenue), `non_interest_expense`, `net_income`, `net_income_owners`, `net_income_common`, `eps_basic`, `eps_diluted`, `equity_owners`, `equity_total`, `cet1_ratio` (percent), `total_assets` and `rwa` (total risk-weighted assets, the CET1 ratio's denominator).

`total_assets` and `rwa` were added in October 2026. Every bank tags total assets as `ifrs-full:Assets`. Each tags RWA under its own name (table above); Canadian banks report one RWA figure for all three capital ratios, and RWA is 30% to 32% of total assets at every bank in every period. These two accounts are the XBRL as filed and have not been compared with the banks' releases. Provision for credit losses was left out: no bank tags the income-statement line as a consolidated total. The only candidates are cash-flow add-backs, tagged `ifrs-full:AdjustmentsForImpairmentLossReversalOfImpairmentLossRecognisedInProfitOrLoss` by four banks and as the broader `ifrs-full:AdjustmentsForProvisions` by BMO, so the five figures would not be like for like.

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

**Share prices** are not in the XBRL. RBC's TSX closes at quarter end (C$293.41 on 2026-07-31 and C$177.79 on 2025-07-31) come from the "Common share price (RY on TSX)" line of its Q3 2026 Report to Shareholders (Exhibit 99.2 of 6-K 0001193125-26-369461), and the dataset stores that citation. The 2026-07-31 closes of the other four come from Exhibit 99.1 (the Q3 2026 Report to Shareholders) of the same 6-K that carries each bank's XBRL, and each figure was found in the exhibit text: TD C$168.04 ("Closing share price (TSX)"), Scotiabank C$122.97 ("Closing share price (TSX)" under common share information), BMO C$251.47 and CIBC C$166.04 ("Closing share price"; those two exhibits do not name the exchange, and the amounts are in Canadian dollars).

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

Single quarter = Q3 YTD − H1 YTD. Answers: RBC **52.81%**, TD **50.19%**.

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

### c04: each bank's fiscal 2025 ROE against its own ROE objective

Five answers: each bank's medium-term ROE objective minus its fiscal 2025 ROE, in percentage points, on the basis the objective states. Nothing is fetched: the collector starts with the nine ROE figures the banks quoted in their fiscal 2025 MD&A (reported, and adjusted where given) and the analyst with the five quoted objectives, as facts that keep their 40-F accession numbers (`objectives_ca.py` quotes the same figures; a test keeps them equal). The method sentences tell the collector to share both bases and the analyst to use adjusted ROE only where the objective says adjusted.

| Bank | Objective | ROE on that basis | Gap (pp) | The basis trap |
|---|---|---|---|---|
| RY | 16%+ (basis not stated) | 16.3 reported | −0.3 | none (RBC gives one ROE) |
| TD | about 16%, adjusted | 12.9 adjusted | 3.1 | reported 17.8 (gain on selling Schwab shares included) gives −1.8 |
| BNS | 14%+ (basis not stated) | 9.7 reported | 4.3 | adjusted 11.8 gives 2.2 |
| BMO | 15%+, adjusted | 11.3 adjusted | 3.7 | reported 10.6 gives 4.4 |
| CM | 15%+, adjusted | 14.4 adjusted | 0.6 | reported 14.3 gives 0.7 |

The scripted reference solves it in 8 rounds of 14. **Claude Haiku 5.5** (2026-10-23, seeds 7 and 8, both placements, chat allowed and blocked) solved all 8 episodes in 6 to 8 rounds, with every gap on the right basis, for US$0.049 in total. The trap did not separate anything: once the basis rule is written down, a small model applies it.

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

## Sector risk digest (news)

```bash
python -m agentworld_lite.finance news --cache C:/news_cache --out digest.md
```

`agentworld_lite/finance/news.py` reads public RSS feeds and writes an as-of-dated Markdown digest, with no model. The
default feeds are the Financial Post main, banking and economy feeds. CBC Business is not a default: its server
answers ordinary clients but lets requests that carry this tool's declared bot User-Agent time out (checked
2026-10-21), and the tool does not disguise its User-Agent. It can still be tried with `--feed`. BNN Bloomberg has no
RSS feed and is not used.

- **What is kept.** Per item only the headline, link, date, feed name and the feed's own short summary (at most 280
  characters). Article pages are never opened. Article text that a feed carries (`content:encoded`) is dropped before
  anything is cached, and the cache (`--cache`, default `data/finance/cache/news`, gitignored) holds parsed items, not
  raw feeds. Requests name the project in their User-Agent and pause between feeds; `--offline` uses the cache only.
- **Tagging.** Banks by name and alias from the dataset (RBC, TD, Scotiabank, BMO, CIBC; short upper-case names only
  as whole words, so "Nova Scotia" is not Scotiabank), the banks as a group ("Canada's biggest banks", "Big Five
  banks"), and seven risk themes by keyword: credit and loan losses, housing and mortgages, tariffs and trade, rates
  and the Bank of Canada, capital and CET1, regulation (OSFI, AML, fines), and restructuring or layoffs. The digest
  prints every rule.
- **Output.** Sources with fetch status; the Bank of Canada policy rate and 5-year yield from the free Valet API,
  cited by series and date; theme counts per bank; the latest headlines per theme with link and date; and a paragraph
  per bank that sets its news themes beside its filed CET1 ratio, single-quarter efficiency ratio and quarterly net
  income to common, each cited by fact id and accession. Provision for credit losses is not in the dataset, so credit
  themes are set beside net income.
- **Self-check.** A digest with a filed figure that lacks a citation is refused; headline text is quoted, not checked.

## Valuation snapshot

```bash
python -m agentworld_lite.finance valuation --bank RY --out ry_valuation.md
```

`agentworld_lite/finance/valuation.py` compares one bank with the other four on one page, with no model. For all five
banks it shows the fiscal Q3 2026 close, TTM diluted EPS (FY 2025 - Q3 YTD 2025 + Q3 YTD 2026), P/E, the
efficiency ratio of fiscal Q3 alone and the CET1 ratio; then the peers' lowest, median and highest P/E and the price
range those multiples imply for the subject's TTM EPS. Every number cites its fact ids and filings, and the page
carries a methods section and an educational-sample disclaimer. It gives no rating and no target price.

| Bank | Close, 2026-07-31 | TTM diluted EPS | P/E | Efficiency ratio, Q3 alone | CET1 |
|---|---|---|---|---|---|
| RY | 293.41 | 15.86 | 18.50x | 52.81% | 13.5% |
| TD | 168.04 | 9.34 | 17.99x | 50.19% | 14.3% |
| BNS | 122.97 | 7.65 | 16.07x | 52.74% | 13.1% |
| BMO | 251.47 | 12.27 | 20.49x | 67.48% | 13.0% |
| CM | 166.04 | 10.42 | 15.93x | 55.99% | 13.4% |

For RBC the peer P/E runs from 15.93x (CIBC) to 20.49x (BMO), median 17.03x, which implies C$252.72 to C$325.05 per
share (C$270.14 at the median) against its close of C$293.41. BMO's P/E and efficiency ratio are high partly because
its reported fiscal Q3 includes the goodwill charge described above; the snapshot uses reported earnings and says so.

## Earnings review

```bash
python -m agentworld_lite.finance earnings-review --market ca --group all
```

`agentworld_lite/finance/earnings_review_ca.py` follows the Earnings Reviewer agent of
[anthropics/financial-services](https://github.com/anthropics/financial-services) on EDGAR filings only, with no model.
For each bank's latest report it derives the quarter alone from the year-to-date filings, sets it against the previous
quarter (and against the same quarter a year earlier once the dataset has the prior H1 report), and adds year-to-date
variance, capital, the bank's quoted objectives, QC checks and a coverage model (`model.json`), staged in
`reports/earnings/ca/<bank>_2026Q3` for a person to review. On fiscal Q3 2026 the QC flags BMO: net income to common
down 32.97% from the previous quarter, the quarter of its goodwill charge.

## Financial objectives

```bash
python -m agentworld_lite.finance objectives-ca --out reports/objectives_ca.md
```

`agentworld_lite/finance/objectives_ca.py` quotes each bank's financial objectives verbatim from the MD&A exhibit of
its fiscal 2025 annual filing (Form 40-F) and measures progress from filings: the bank's own fiscal 2025 ROE (quoted,
reported and adjusted where given), diluted EPS growth over the first nine months of fiscal 2026 (reported, so shown
without a verdict) and the CET1 ratio at 2026-07-31. It ends with the Korean groups' value-up targets side by side.

| Bank | 40-F accession | Exhibit | ROE objective | Fiscal 2025 ROE | Verdict |
|---|---|---|---|---|---|
| RY | 0001193125-25-305927 | EX-2 `d95203dex2.htm` | 16%+ | 16.3% | meets |
| TD | 0001562762-25-000289 | EX-99.2 `ex992.htm` | ~16% adjusted (fiscal 2029) | 12.9% adjusted (17.8% reported) | below |
| BNS | 0001193125-25-304643 | EX-99.2 `d936350dex992.htm` | 14%+ | 9.7% (11.8% adjusted) | below |
| BMO | 0001193125-25-307982 | EX-99.2 `d938207dex992.htm` | 15%+ adjusted | 11.3% adjusted | below |
| CM | 0001193125-25-307488 | EX-99.B3(C) `d91021dex99b3c.htm` | 15%+ adjusted | 14.4% adjusted | below |

**How the quotes are checked.** On 2026-10-22 sec.gov answered this machine's scripted requests with HTTP 403
("Request Rate Threshold Exceeded") while a browser could open the same pages, so the five exhibits were read in a
browser: the SHA-256 of each served document is recorded in `DOCS`, and each of the 35 quotes was found in the page
text with whitespace collapsed. Two more TD quotes (Table 3, items of note: the gain on selling Schwab shares and the
total of adjustments) were added on 2026-10-23 and checked against the saved copy, whose SHA-256 is the same. `--verify-local DIR` repeats the check on saved copies (found by size and hash, so a
renamed download still counts): same SHA-256, and every quote present once tags, entities and whitespace are dropped.
Saved copies of all five passed. `EdgarClient.documents()` and `document()` fetch a filing's file list and one
document (cached) for when scripted access works again.

## Demo

```bash
pip install -e ".[demo]"
```

```bash
streamlit run demo/streamlit_app.py
```

A Streamlit app over the same code, with no model and no key. It has seven views; all but the news digest work offline.

![Demo: the c03 run replayed round by round, the Big 5 charts, and the cited sector note](img/canada_desk_demo.gif)

- **Desk replay.** A round slider over the scripted reference or an uploaded trajectory. Each agent's moves, the size of its notebook, and which answers are within tolerance so far.
- **Big 5 data.** Single-quarter net income to common and the CET1 ratio for each bank, as charts and as a table that names the filing behind each figure.
- **Objectives.** Each bank's fiscal 2025 ROE against its quoted ROE objective (on the objective's basis, with the other basis alongside), as a chart and a table, then the full objectives scorecard with a Markdown download.
- **Sector note.** The cited note above, with a Markdown download.
- **Valuation.** The snapshot above for any of the five banks, with headline metrics and a Markdown download.
- **News digest.** Builds the sector risk digest from the live feeds when its button is pressed (cached for an hour); nothing is fetched until then.
- **Task.** The brief, roles and tool limits, where the methodology sits (shared document or private briefs), and the answer key.

An uploaded trajectory is replayed first and rejected if it does not reproduce on the dataset. Query parameters open a given state, e.g. `?task=c03_big5_q3_sector_shared&view=Desk%20replay&round=11`.

`python demo/record_gif.py --out demo.gif` records a GIF of the app. It starts the app, drives a headless Chrome or Edge through the DevTools protocol, waits until each view has finished rendering, and joins the screenshots with Pillow (about 0.5 MB for 12 frames). The app runs on Streamlit Community Cloud at [agentworld-canada-desk.streamlit.app](https://agentworld-canada-desk.streamlit.app), deployed from branch `canada-desk` with `demo/streamlit_app.py` as the main file; `demo/requirements.txt` lists what it installs.

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

The Canadian desk gets its own system prompt and tool descriptions (fiscal periods, CAD units, no share-count tool).

## Results: Claude Opus 5.5

First model run, 2026-10-20: `claude-opus-5-5` at its default effort, all six tasks, chat allowed (`full`) and blocked
(`no_comm`), seeds 7, 8 and 9: 36 episodes.

| Task | Setting | Solved | Rounds, mean (range) | Scripted reference | Rule CCE, mean | Cost per episode |
|---|---|---|---|---|---|---|
| c01 RBC trailing P/E | full | 6/6 | 6.0 (6-6) of 16 | 7 | 0.61 | $0.22 |
| c01 | no_comm | 6/6 | 6.2 (6-7) of 16 | 7 | 0.50 | $0.22 |
| c02 RBC and TD efficiency | full | 6/6 | 7.0 (7-7) of 18 | 11 | 0.69 | $0.26 |
| c02 | no_comm | 6/6 | 7.0 (7-7) of 18 | 11 | 0.52 | $0.26 |
| c03 Big 5 earnings and capital | full | 6/6 | 17.7 (17-18) of 30 | 18 | 0.65 | $1.02 |
| c03 | no_comm | 6/6 | 17.7 (17-18) of 30 | 18 | 0.58 | $1.03 |

- **Every episode solved every answer**, with no failed tool call in 36 episodes. The team never fell for a listed
  trap and solved each task in about as many rounds as the scripted reference or fewer. On c03 the collector shared
  bank by bank and the reporter filed answers as they arrived, a pipeline the reference does not use.
- **Blocking chat did not change success or rounds**, in either information placement (pair report: 9 of 9 complete
  blocks solved in all four cells). It did lower the share of actions that contributed to success (rule CCE) on all
  three tasks. With the methodology in private briefs, teams sent more messages (3.2 per chat-allowed episode against
  2.1 when it was shared).
- **Cost:** US$18.13 in total (US$6.01, US$5.98 and US$6.13 per seed), 1,107 model calls, 59 minutes of
  wall-clock time. Seed 7 ran before a fix to the call counter in `finance/run.py`, which had counted every call
  twice; its cost was always counted correctly.
- **What it means:** these three tasks do not separate a frontier model from the scripted reference, so differences
  between models will have to come from smaller models or harder variants. The trajectories and evaluations stay in
  `runs/` (not committed); `python -m agentworld_lite.finance note --trajectory <file>` turns any of them into a
  cited sector note.

## Limits

- Values are as originally filed. A later filing may restate a comparative; the dataset keeps the first figure and its date, which is what the as-of guard needs, but it does not flag restatements.
- Extension concepts are matched by name. A bank that renames an element needs a new candidate in `ACCOUNT_RULES`; the stored `concept` shows what matched.
- Period matching allows ±7 days around the computed period end, which suits month-end fiscal years such as the banks'. 52/53-week fiscal years have not been tested.
- Share prices and share counts at period end come from the report text, not from XBRL, so they are added by hand with a citation.
- Fact labels describe every account of a filing as "year-to-date", including period-end ratios such as CET1, which the sector note repeats.
