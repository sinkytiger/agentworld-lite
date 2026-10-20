# Canadian desk: Big 5 bank filings on the same protocol

The finance domain's research desk (a collector fetches filings, an analyst computes, a reporter submits, and every number is a cited fact) now also runs on real filings of Canada's five largest banks. This page covers where the numbers come from, how they are normalized, how they were checked, and the two tasks built on them.

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

Amounts in C$ millions.

**Share prices** are not in the XBRL. RBC's TSX closes at quarter end (C$293.41 on 2026-07-31 and C$177.79 on 2025-07-31) come from the "Common share price (RY on TSX)" line of its Q3 2026 Report to Shareholders (Exhibit 99.2 of 6-K 0001193125-26-369461), and the dataset stores that citation.

## Tasks (`tasks/finance_ca`)

Both tasks come as matched shared / private pairs, like the Korean ones: the methodology sentences sit either in the shared document or in the analyst's private brief. The as-of date is 2026-09-30 and the tolerance is 0.1%, tight enough to separate basic from diluted EPS.

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

The scripted reference solves c01 in 7 rounds of 16 and c02 in 11 rounds of 18. An idle team fails both.

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
