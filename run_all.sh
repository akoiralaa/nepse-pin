#!/usr/bin/env bash
# run_all.sh — Full NEPSE PIN Model Pipeline
# Run this after the scraper finishes (or run sections individually).
#
# Usage: bash run_all.sh
# Or step by step:
#   bash run_all.sh scrape      # Step 1 only
#   bash run_all.sh estimate    # Step 2 only (needs data/)
#   bash run_all.sh analyze     # Step 3 only (needs pin_results.csv)

set -euo pipefail
SYMBOLS="NABIL NMB NICA SANIMA SBI NHPC UPPER AKPL NLIC GMFIL CFCL"
START="2021-01-01"
END="2026-03-16"
DATA_DIR="data"
RESULTS_DIR="results"
PIN_CSV="pin_results.csv"

step="${1:-all}"

# ── Step 1: Scrape ────────────────────────────────────────────────────────────
if [[ "$step" == "all" || "$step" == "scrape" ]]; then
  echo "[1/3] Scraping floorsheet data (this takes several hours)..."
  python3 scraper.py \
    --symbols $SYMBOLS \
    --start "$START" \
    --end   "$END" \
    --output "$DATA_DIR" \
    2>&1 | tee "$DATA_DIR/scrape_log.txt"
  echo "[1/3] Done."
fi

# ── Step 2: Estimate PIN ──────────────────────────────────────────────────────
if [[ "$step" == "all" || "$step" == "estimate" ]]; then
  echo "[2/3] Estimating PIN for all symbols..."
  # Combine all daily CSVs into one
  python3 - <<'PYEOF'
import pandas as pd
from pathlib import Path

daily_dir = Path("data/daily")
frames = [pd.read_csv(f, parse_dates=["date"]) for f in sorted(daily_dir.glob("*_daily.csv"))]
combined = pd.concat(frames, ignore_index=True)
out = Path("data/all_daily_combined.csv")
combined.to_csv(out, index=False)
print(f"Combined {len(frames)} files → {out} ({len(combined)} rows, {combined['symbol'].nunique()} symbols)")
PYEOF

  python3 pin_estimator.py \
    --input  "data/all_daily_combined.csv" \
    --output "$PIN_CSV" \
    --n-starts 64 \
    --bootstrap-n 1000 \
    2>&1 | tee "pin_estimation_log.txt"
  echo "[2/3] Done."
fi

# ── Step 3: Cross-sectional analysis ─────────────────────────────────────────
if [[ "$step" == "all" || "$step" == "analyze" ]]; then
  echo "[3/3] Running cross-sectional analysis..."
  python3 analysis.py \
    --daily   "data/daily" \
    --pin     "$PIN_CSV" \
    --output  "$RESULTS_DIR" \
    2>&1 | tee "analysis_log.txt"
  echo "[3/3] Done. Results in $RESULTS_DIR/"
fi

echo "Pipeline complete."
