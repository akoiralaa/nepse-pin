# NEPSE PIN Model

**Informed Trading and Market Microstructure on the Nepal Stock Exchange: A First Application of the PIN Model**

*Abhie Koirala — University of North Texas*
*Target: SSRN working paper, August 2026*

---

## Overview

This repository implements the full empirical pipeline for estimating the **Probability of Informed Trading (PIN)** on the Nepal Stock Exchange (NEPSE). It is the first application of the Easley-Hvidkjaer-O'Hara PIN model to NEPSE data.

The PIN model decomposes daily order flow into informed and uninformed components, estimating the probability that any given trade originates from a trader with private information. In thin, retail-dominated markets like NEPSE, measuring information asymmetry is especially policy-relevant.

---

## Repository Structure

```
nepse_pin/
├── scraper.py            # Task 1 — floorsheet scraper, tick rule, quality report
├── scrape_parallel.py    # Parallel runner (4 symbols concurrently)
├── pin_estimator.py      # Task 2 — overflow-safe MLE + bootstrap CI
├── analysis.py           # Task 3 — sector analysis, correlations, rolling PIN, BVC robustness
├── run_all.sh            # Master pipeline (scrape → estimate → analyze)
├── probe_cutoff.py       # Utility: find oldest available Merolagani date
├── DEVLOG.md             # Running development log
├── data/
│   ├── raw/              # Per-symbol raw floorsheet CSVs
│   ├── daily/            # Daily buy/sell aggregations
│   ├── quality/          # Data quality reports (Markdown)
│   └── logs/             # Per-symbol scraper logs
└── results/              # PIN estimates, tables, figures
```

---

## The PIN Model

The model (Easley et al. 1996, 2002) assumes a daily tree structure:

- With probability **α**: an information event occurs
  - Bad news with prob **δ** → informed traders sell at rate **μ**
  - Good news with prob **1−δ** → informed traders buy at rate **μ**
- With probability **1−α**: no event, only uninformed trading (**ε_b** buys, **ε_s** sells)

**PIN = αμ / (αμ + ε_b + ε_s)**

Parameters θ = (α, δ, μ, ε_b, ε_s) are estimated by Maximum Likelihood from daily (buys, sells) counts.

---

## Data

**Source:** [Merolagani](https://merolagani.com/Floorsheet.aspx) floorsheet data

**Coverage confirmed:** May 2014 → present (~12 years)

**Fields:** `date, transact_no, symbol, buyer_broker, seller_broker, quantity, rate, amount`

**Trade direction:** Inferred via the **tick rule** (Ellis, Michaely, O'Hara 2000):
- Uptick → buy-initiated
- Downtick → sell-initiated
- Zero-tick → carry forward last direction (reverse tick rule)

**Target stocks:**

| Symbol | Name | Sector |
|--------|------|--------|
| NABIL  | Nabil Bank Limited | Banking |
| NMB    | NMB Bank | Banking |
| NICA   | NIC Asia Bank | Banking |
| SANIMA | Sanima Bank | Banking |
| SBI    | Nepal SBI Bank | Banking |
| NHPC   | Nepal Hydro & Electric | Hydropower |
| UPPER  | Upper Tamakoshi | Hydropower |
| AKPL   | Aadhikhola Khola | Hydropower |
| NLIC   | Nepal Life Insurance | Insurance |
| GMFIL  | GMFIL Finance | Finance |
| CFCL   | Central Finance | Finance |

---

## Quickstart

### 1. Install dependencies

```bash
pip install requests beautifulsoup4 pandas numpy scipy matplotlib
```

### 2. Scrape floorsheet data (parallel, ~1.5 days for 5-year window)

```bash
python3 scrape_parallel.py --start 2021-01-01 --end 2026-03-16 --workers 4
# Resume after interruption:
python3 scrape_parallel.py --start 2021-01-01 --end 2026-03-16 --resume
```

### 3. Estimate PIN

```bash
# Combine daily CSVs
python3 -c "
import pandas as pd; from pathlib import Path
frames = [pd.read_csv(f, parse_dates=['date']) for f in sorted(Path('data/daily').glob('*_daily.csv'))]
pd.concat(frames).to_csv('data/all_daily_combined.csv', index=False)
"

python3 pin_estimator.py \
  --input  data/all_daily_combined.csv \
  --output pin_results.csv \
  --n-starts 64 \
  --bootstrap-n 1000
```

### 4. Cross-sectional analysis

```bash
python3 analysis.py \
  --daily  data/daily/ \
  --pin    pin_results.csv \
  --output results/
```

### Or run everything

```bash
bash run_all.sh
```

---

## Implementation Notes

### Numerical Stability

The standard EHO likelihood overflows for large buy/sell counts (common on active NEPSE days with 2,000+ trades). We use the ELO (2010) log-sum-exp formulation:

```
log L = Σ_t  log-sum-exp(log w_g + log P(B|μ+ε_b) + log P(S|ε_s),
                          log w_b + log P(B|ε_b)    + log P(S|μ+ε_s),
                          log w_n + log P(B|ε_b)    + log P(S|ε_s))
```

with `log P(x|λ) = x·log(λ) − λ − lgamma(x+1)` via `scipy.special.gammaln`.

### MLE Strategy

- **64 random starting points** per stock (+ 4 structured starts on symmetry axes)
- **L-BFGS-B** with bounds: α,δ ∈ (0,1), μ,ε_b,ε_s > 0
- Tight tolerances: `ftol=1e-12`, `gtol=1e-8`
- Warn if convergence rate < 80%

### Bootstrap CI

1,000 bootstrap iterations resampling trading days with replacement. MLE point estimate used as warm start for speed.

---

## Known Limitations

1. **Tick rule misclassification** (~30% error rate, Ellis et al. 2000). Robustness check using BVC (Easley, Lopez de Prado, O'Hara 2012) provided.
2. **No quote data** — Lee-Ready algorithm not applicable. Tick rule only.
3. **PIN bias in thin markets** — sparse-day exclusion (< 5 trades) applied; VPIN comparison discussed in paper.
4. **NEPSE holidays** not filtered — zero-trade days flagged in quality reports and dropped from MLE.

---

## Citation

If you use this code, please cite:

> Koirala, A. (2026). *Informed Trading and Market Microstructure on the Nepal Stock Exchange: A First Application of the PIN Model.* SSRN Working Paper.

---

## Key References

- Easley, D., Kiefer, N., O'Hara, M., Paperman, J. (1996). Liquidity, information, and infrequently traded stocks. *Journal of Finance*, 51(4), 1405–1436.
- Easley, D., Hvidkjaer, S., O'Hara, M. (2002). Is information risk a determinant of asset returns? *Journal of Finance*, 57(5), 2185–2221.
- Easley, D., Lopez de Prado, M., O'Hara, M. (2010). Measuring flow toxicity in a high-frequency world. *Review of Financial Studies*, 25(5), 1457–1493.
- Ellis, K., Michaely, R., O'Hara, M. (2000). The accuracy of trade classification rules. *Journal of Financial and Quantitative Analysis*, 35(4), 529–551.
- Venter, J., De Jongh, D. (2006). Extending the PIN model. *Studies in Economics and Finance*, 23(1), 54–77.

---

*JEL Codes: G14, G15, O16*
