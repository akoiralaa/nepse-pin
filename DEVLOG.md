# DEVLOG — NEPSE PIN Model

Running development log. Most recent entries at top.

---

## 2026-03-16 — Task 3: Cross-Sectional Analysis

**Built `analysis.py`** — full cross-sectional analysis pipeline:

- **Summary table** (`table1_summary.csv` + LaTeX booktabs `table1_summary.tex`)
  - Per-stock: days, mean trades, PIN, 95% CI, sector
- **Sector ANOVA** + box plot (`figure1_sector_boxplot.pdf/.png`)
  - Groups: Banking, Hydropower, Insurance, Finance
  - One-way ANOVA via `scipy.stats.f_oneway`
  - Individual stock points overlaid on box plot
- **Spearman correlations** (`table2_correlations.csv`)
  - PIN vs avg. daily trades
  - PIN vs avg. daily volume
  - PIN vs order imbalance `|buys − sells| / total_trades`
- **Rolling quarterly PIN time-series** (`figure2_rolling_pin.pdf/.png`)
  - 63-trading-day rolling window, re-estimated every 10 days
  - Annotates COVID crash (Mar 2020), NEPSE peak (Nov 2021), bear market (Jul 2022)
- **Robustness checks**
  - >300-day subsample re-estimation (`robustness_300d.csv`)
  - BVC classification alternative (`robustness_bvc.csv`, `figure3_bvc_robustness.pdf/.png`)
  - BVC uses order imbalance direction as price-change proxy (no OHLC available)

All figures saved as `.pdf` (for paper) and `.png` (for working paper). Publication style (`font.family: serif`, no top/right spines, 300 DPI).

---

## 2026-03-16 — Parallel Scraper

**Built `scrape_parallel.py`** — replaces sequential `run_all.sh` scrape step.

- `ProcessPoolExecutor` with configurable `MAX_WORKERS` (default: 4)
- Staggered launches: 8s between worker starts to spread initial GETs
- Each worker writes to its own log file: `data/logs/{SYMBOL}.log`
- `--resume` flag skips symbols whose output CSV already exists
- Estimated speedup: ~4× vs sequential (11 symbols / 4 workers ≈ 2.75 batches)
- Estimated total time: ~1.5 days for 5-year window at 2s rate limit

**Killed** the earlier sequential run (was at NABIL day 5/1357 when stopped).

---

## 2026-03-16 — Task 2: PIN MLE Estimator

**Built `pin_estimator.py`** — full MLE estimation pipeline.

**Key design decisions:**

1. **Overflow-safe log-likelihood** using ELO (2010) factorization:
   - `log P(x|λ) = x·log(λ) − λ − lgamma(x+1)` via `scipy.special.gammaln`
   - Three mixture components combined via log-sum-exp (row-wise)
   - Validated: no NaN/inf even at B=S=5000

2. **Multi-start MLE**: 64 random starts + 4 structured starts on symmetry axes
   - Random starts scaled to observed mean trade counts
   - Convergence rate threshold: warn if < 80%
   - Optimizer: L-BFGS-B, `ftol=1e-12`, `gtol=1e-8`, `maxiter=1000`

3. **Bootstrap CI**: 1,000 resamples of trading days with replacement
   - Warm-started from MLE point estimate (full multi-start would be ~60hrs)
   - Falls back to 5 random perturbations if warm start fails

4. **Parameter bounds**: α,δ ∈ (1e-4, 1−1e-4), μ,ε_b,ε_s ∈ (1e-4, ∞)

**Validation on synthetic data (200 days, known θ):**
```
True PIN  = 0.1085  (α=0.35, δ=0.45, μ=80, ε_b=120, ε_s=110)
Est. PIN  = 0.1088  CI = [0.0895, 0.1290]
True PIN in CI: True
Convergence: 98%
```
PIN recovered to within 0.0003. δ has known weak identifiability (expected).

---

## 2026-03-16 — Task 1: Data Pipeline

**Built `scraper.py`** — full floorsheet scraper.

**Source selection:** After checking 6 candidate sources:

| Source | Floorsheet? | Depth | Decision |
|--------|------------|-------|----------|
| polymorphisma/nepse_scraper | No (OHLCV only) | — | Rejected |
| suyogdahal/nepse-data | Yes | Dec 2020–Oct 2021 only (dead) | Reference only |
| omitnomis/ShareSansarScraper | No (EOD prices) | Mar 2024–present | Rejected |
| basic-bgnr/NepseUnofficialApi | Yes | Rolling ~few weeks | Rejected (too short) |
| **Merolagani Floorsheet** | **Yes** | **May 2014–present** | **Selected** |

**Merolagani probe results:**

| Date tested | Result |
|-------------|--------|
| 2026-03-15 | 500 rows/page, 124,490 total records on that day |
| 2021-01-01 | 500 rows ✓ |
| 2020-01-05 | 500 rows ✓ |
| 2018-01-07 | 474 rows ✓ |
| 2015-01-04 | 500 rows ✓ |
| **2014-05-18** | **500 rows** ← oldest confirmed |
| 2014-05-15 | 0 rows ← cutoff |

**Scraper implementation:**
- Full-page ASP.NET POST (no Selenium, no Selenium deps)
- `__EVENTTARGET = ctl00$ContentPlaceHolder1$lbtnSearchFloorsheet`
- Viewstate chained forward across pages (1 GET per symbol, not per page)
- 500 rows/page, pagination via `PagerControl1$hdnCurrentPage`
- Company IDs via `AutoSuggestHandler.ashx?type=Company&q={SYMBOL}`
- 2s rate limit, 3-retry exponential backoff

**Smoke test (NABIL, 4 days):**
```
5,500 records × 8 columns
buy/sell split: 0.56% unclassified (excellent)
4/4 days usable (≥5 trades)
```

**Tick rule (Ellis, Michaely, O'Hara 2000):**
- Uptick → buy (+1), downtick → sell (−1)
- Zero-tick → carry forward previous direction (reverse tick rule)
- Applied within each (date, symbol) group

---

## 2026-03-16 — Data Source Investigation

**Context:** Building PIN model for NEPSE — need multi-year floorsheet data with buyer/seller broker IDs.

**NEPSE API limitation confirmed:** Official API (`basic-bgnr/NepseUnofficialApi`) returns 403 on floorsheet POST endpoint. Auth token obtainable but WAF blocks floorsheet access. Useless for historical data.

**Repos checked:**
- `Aabishkar2/nepse-data` — OHLCV from 2011, no broker data
- `sharesansar.com` — EOD prices, floorsheet not accessible via simple requests
- `nepalytix.com` — claims floorsheet API, no documented endpoints, 404 on `/api`
- `Prabesh01/nepalstock-api` — NEPSE proxy, same 403 limitation

**Decision:** Build original scraper targeting Merolagani.

---

## 2026-03-15 — Project Initialized

**Paper:** "Informed Trading and Market Microstructure on the Nepal Stock Exchange: A First Application of the PIN Model"

**Author:** Abhie Koirala, University of North Texas

**Target:** SSRN working paper, August 2026. Journals: Emerging Markets Review, Pacific-Basin Finance Journal, Journal of Financial Markets.

**JEL Codes:** G14, G15, O16

**Stack:** Python 3.14 — requests, beautifulsoup4, pandas, numpy, scipy, matplotlib

**Related work in repo:** `neural-pde-solvers` (separate project — PINNs for PDEs, Deep BSDE option pricing, DeepONet Heston; no overlap with PIN model).
