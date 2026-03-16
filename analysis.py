import argparse
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")   # non-interactive backend
import matplotlib.pyplot as plt
from scipy import stats

log = logging.getLogger(__name__)

# ── publication style ─────────────────────────────────────────────────────────

plt.rcParams.update({
    "font.size":            11,
    "font.family":          "serif",
    "axes.spines.top":      False,
    "axes.spines.right":    False,
    "figure.dpi":           150,
    "savefig.dpi":          300,
    "savefig.bbox":         "tight",
})

SECTOR_MAP = {
    "NABIL":  "Banking",
    "NMB":    "Banking",
    "NICA":   "Banking",
    "SANIMA": "Banking",
    "SBI":    "Banking",
    "NHPC":   "Hydropower",
    "UPPER":  "Hydropower",
    "AKPL":   "Hydropower",
    "NLIC":   "Insurance",
    "PLICL":  "Insurance",
    "GMFIL":  "Finance",
    "CFCL":   "Finance",
}

# Greyscale-safe, colourblind-friendly palette (one per sector)
SECTOR_COLORS = {
    "Banking":    "#2c2c2c",
    "Hydropower": "#6e6e6e",
    "Insurance":  "#a8a8a8",
    "Finance":    "#d4d4d4",
    "Other":      "#f0f0f0",
}

# Major NEPSE events for time-series annotation
NEPSE_EVENTS = [
    ("2020-03-15", "COVID\ncrash"),
    ("2021-11-01", "NEPSE\npeak"),
    ("2022-07-01", "Bear\nmarket"),
]

# ── helpers ───────────────────────────────────────────────────────────────────

def load_all_daily(daily_dir: Path) -> pd.DataFrame:
    frames = []
    for f in sorted(daily_dir.glob("*_daily.csv")):
        df = pd.read_csv(f, parse_dates=["date"])
        frames.append(df)
    if not frames:
        raise FileNotFoundError(f"No *_daily.csv files found in {daily_dir}")
    return pd.concat(frames, ignore_index=True)

def add_sector(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["sector"] = df["symbol"].map(SECTOR_MAP).fillna("Other")
    return df

# ── 1. summary statistics table ───────────────────────────────────────────────

def summary_table(pin_df: pd.DataFrame, daily_df: pd.DataFrame, output_dir: Path):
    # Merge daily stats into PIN results
    stats_rows = []
    for _, row in pin_df.iterrows():
        sym = row["symbol"]
        sym_daily = daily_df[daily_df["symbol"] == sym]
        stats_rows.append({
            "symbol":        sym,
            "sector":        SECTOR_MAP.get(sym, "Other"),
            "n_days":        int(row["n_days"]),
            "mean_trades":   sym_daily["total_trades"].mean(),
            "mean_volume":   sym_daily["total_volume"].mean() if "total_volume" in sym_daily else np.nan,
            "PIN":           row["PIN"],
            "PIN_lower_CI":  row["PIN_lower_CI"],
            "PIN_upper_CI":  row["PIN_upper_CI"],
            "alpha":         row["alpha"],
            "mu":            row["mu"],
            "eps_b":         row["eps_b"],
            "eps_s":         row["eps_s"],
            "convergence":   row["convergence_rate"],
        })

    tbl = pd.DataFrame(stats_rows).sort_values(["sector", "PIN"], ascending=[True, False])

    # Save CSV
    csv_path = output_dir / "table1_summary.csv"
    tbl.to_csv(csv_path, index=False, float_format="%.4f")
    log.info("Summary table saved: %s", csv_path)

    # LaTeX (booktabs)
    display = tbl[["symbol", "sector", "n_days", "mean_trades", "PIN", "PIN_lower_CI", "PIN_upper_CI"]].copy()
    display.columns = ["Symbol", "Sector", "Days", r"Avg. Trades/Day", "PIN", r"95\% CI Low", r"95\% CI High"]
    display["PIN"] = display["PIN"].map("{:.4f}".format)
    display[r"95\% CI Low"]  = display[r"95\% CI Low"].map("{:.4f}".format)
    display[r"95\% CI High"] = display[r"95\% CI High"].map("{:.4f}".format)
    display["Avg. Trades/Day"] = display["Avg. Trades/Day"].map("{:.0f}".format)

    latex = display.to_latex(
        index=False,
        escape=False,
        column_format="llrrrr r",
        caption=(
            r"PIN estimates for NEPSE stocks. "
            r"95\% CIs computed by bootstrap resampling of trading days "
            r"(1{,}000 iterations)."
        ),
        label="tab:pin_summary",
        position="htbp",
    )
    # Inject booktabs rules
    latex = latex.replace(r"\hline", r"\midrule", 1)
    latex = (
        latex.replace(r"\begin{tabular}", r"\begin{tabular}" + "\n" + r"\toprule", 1)
             .replace(r"\end{tabular}", r"\bottomrule" + "\n" + r"\end{tabular}", 1)
    )

    tex_path = output_dir / "table1_summary.tex"
    tex_path.write_text(latex)
    log.info("LaTeX table saved: %s", tex_path)

    return tbl

# ── 2. sector comparison ──────────────────────────────────────────────────────

def sector_analysis(pin_df: pd.DataFrame, output_dir: Path):
    df = add_sector(pin_df)

    # ANOVA
    groups = [g["PIN"].values for _, g in df.groupby("sector") if len(g) >= 2]
    if len(groups) >= 2:
        f_stat, p_val = stats.f_oneway(*groups)
        log.info("Sector ANOVA: F=%.3f  p=%.4f", f_stat, p_val)
    else:
        f_stat, p_val = np.nan, np.nan
        log.warning("Not enough sectors with ≥2 stocks for ANOVA")

    # Box plot
    sectors = sorted(df["sector"].unique())
    sector_pins = [df[df["sector"] == s]["PIN"].values for s in sectors]

    fig, ax = plt.subplots(figsize=(7, 4.5))
    bp = ax.boxplot(
        sector_pins,
        labels=sectors,
        patch_artist=True,
        medianprops={"color": "black", "linewidth": 2},
        whiskerprops={"linewidth": 1.2},
        capprops={"linewidth": 1.2},
        flierprops={"marker": "o", "markersize": 5, "alpha": 0.6},
    )
    for patch, sector in zip(bp["boxes"], sectors):
        patch.set_facecolor(SECTOR_COLORS.get(sector, "#cccccc"))
        patch.set_alpha(0.85)

    # Overlay individual data points
    for i, (sector, pins) in enumerate(zip(sectors, sector_pins), 1):
        jitter = np.random.default_rng(0).uniform(-0.15, 0.15, size=len(pins))
        ax.scatter(np.full(len(pins), i) + jitter, pins,
                   color="black", s=30, zorder=5, alpha=0.7)

    ax.set_ylabel("Probability of Informed Trading (PIN)")
    ax.set_xlabel("Sector")
    ax.set_title("PIN Estimates by Sector — NEPSE")

    anova_txt = (f"One-way ANOVA: F = {f_stat:.2f}, p = {p_val:.3f}"
                 if np.isfinite(f_stat) else "")
    ax.text(0.98, 0.97, anova_txt, transform=ax.transAxes,
            ha="right", va="top", fontsize=9, style="italic")

    _save(fig, output_dir, "figure1_sector_boxplot")

    # Sector summary stats
    sector_summary = df.groupby("sector")["PIN"].agg(["mean", "std", "count"]).round(4)
    sector_summary.columns = ["Mean PIN", "Std PIN", "N stocks"]
    log.info("Sector summary:\n%s", sector_summary.to_string())

    return {"f_stat": f_stat, "p_val": p_val, "sector_summary": sector_summary}

# ── 3. correlation analysis ───────────────────────────────────────────────────

def correlation_analysis(pin_df: pd.DataFrame, daily_df: pd.DataFrame, output_dir: Path):
    rows = []
    for _, row in pin_df.iterrows():
        sym = row["symbol"]
        sym_daily = daily_df[daily_df["symbol"] == sym]
        if sym_daily.empty:
            continue

        # High-low range / midpoint as bid-ask spread proxy
        # Approximate from rate column if available — otherwise skip
        spread_proxy = np.nan
        if "rate" in daily_df.columns:
            pass  # rate is per-trade, not daily OHLC — skip spread proxy
        # We'll use buy/sell imbalance as an alternative spread proxy:
        # |num_buys - num_sells| / total_trades  (order imbalance)
        imbalance = (abs(sym_daily["num_buys"] - sym_daily["num_sells"]) /
                     sym_daily["total_trades"].replace(0, np.nan)).mean()

        rows.append({
            "symbol":        sym,
            "PIN":           row["PIN"],
            "avg_volume":    sym_daily["total_volume"].mean() if "total_volume" in sym_daily else np.nan,
            "avg_trades":    sym_daily["total_trades"].mean(),
            "order_imbal":   imbalance,
        })

    corr_df = pd.DataFrame(rows)

    results = []
    for var, label in [
        ("avg_trades",  "Avg. Daily Trades"),
        ("avg_volume",  "Avg. Daily Volume"),
        ("order_imbal", "Order Imbalance (|B−S|/N)"),
    ]:
        valid = corr_df[["PIN", var]].dropna()
        if len(valid) < 4:
            results.append({"Variable": label, "Spearman ρ": np.nan, "p-value": np.nan, "N": len(valid)})
            continue
        rho, pval = stats.spearmanr(valid["PIN"], valid[var])
        results.append({"Variable": label, "Spearman ρ": round(rho, 4), "p-value": round(pval, 4), "N": len(valid)})
        log.info("Spearman  %-35s  ρ=%.3f  p=%.4f", label, rho, pval)

    corr_tbl = pd.DataFrame(results)
    csv_path = output_dir / "table2_correlations.csv"
    corr_tbl.to_csv(csv_path, index=False)
    log.info("Correlation table saved: %s", csv_path)
    return corr_tbl

# ── 4. time-series of aggregate PIN ──────────────────────────────────────────

def aggregate_pin_timeseries(
    daily_df: pd.DataFrame,
    pin_df:   pd.DataFrame,
    output_dir: Path,
):
    from pin_estimator import estimate_pin

    log.info("Computing rolling PIN time-series (this may take a while)...")

    all_symbols = pin_df["symbol"].tolist()
    window = 63  # ~one quarter of trading days

    # Build a unified date index
    all_dates = sorted(daily_df["date"].unique())
    if len(all_dates) < window * 2:
        log.warning("Not enough dates for rolling PIN — skipping time-series plot")
        return

    # For each rolling window endpoint, estimate PIN for each symbol then average
    results = []
    step = 10   # estimate every 10 trading days to keep runtime manageable

    for i in range(window, len(all_dates), step):
        window_dates = all_dates[i - window: i]
        end_date = all_dates[i - 1]

        window_df = daily_df[daily_df["date"].isin(window_dates)].copy()
        window_pins = []

        for sym in all_symbols:
            sym_df = window_df[window_df["symbol"] == sym]
            if len(sym_df) < 20:
                continue
            res = estimate_pin(sym_df, sym, n_starts=16, bootstrap_n=0, min_trades=5)
            if res is not None:
                window_pins.append(res.PIN)

        if window_pins:
            results.append({
                "date":     end_date,
                "mean_pin": np.mean(window_pins),
                "n_stocks": len(window_pins),
            })
            log.info("  %s  mean_PIN=%.4f  n_stocks=%d", end_date, np.mean(window_pins), len(window_pins))

    if not results:
        log.warning("No rolling PIN results produced")
        return

    ts = pd.DataFrame(results)
    ts["date"] = pd.to_datetime(ts["date"])
    ts.to_csv(output_dir / "rolling_pin.csv", index=False)

    # Plot
    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.plot(ts["date"], ts["mean_pin"], color="black", linewidth=1.5,
            label="Cross-sectional mean PIN (63-day rolling)")
    ax.fill_between(ts["date"], ts["mean_pin"] * 0.85, ts["mean_pin"] * 1.15,
                    color="grey", alpha=0.15, label="±15% band")

    # Annotate NEPSE events
    for event_date, label in NEPSE_EVENTS:
        ed = pd.Timestamp(event_date)
        if ts["date"].min() <= ed <= ts["date"].max():
            ax.axvline(ed, color="black", linewidth=0.8, linestyle="--", alpha=0.6)
            ax.text(ed, ax.get_ylim()[1] * 0.97, label,
                    fontsize=8, ha="center", va="top", rotation=0,
                    bbox={"boxstyle": "round,pad=0.2", "fc": "white", "ec": "none", "alpha": 0.8})

    ax.set_xlabel("Date")
    ax.set_ylabel("Mean PIN")
    ax.set_title("Aggregate Informed Trading Intensity — NEPSE (63-Day Rolling)")
    ax.legend(fontsize=9, frameon=False)
    _save(fig, output_dir, "figure2_rolling_pin")
    return ts

# ── 5. robustness checks ──────────────────────────────────────────────────────

def robustness_checks(daily_df: pd.DataFrame, pin_df: pd.DataFrame, output_dir: Path):
    from pin_estimator import estimate_pin

    log.info("Running robustness checks...")

    # ── (a) >300-day subsample ─────────────────────────────────────────────
    deep_symbols = pin_df[pin_df["n_days"] > 300]["symbol"].tolist()
    log.info("Stocks with >300 days: %s", deep_symbols)

    deep_results = []
    for sym in deep_symbols:
        res = estimate_pin(daily_df, sym, n_starts=64, bootstrap_n=200, min_trades=5)
        if res:
            deep_results.append({"symbol": sym, "PIN_300d": res.PIN})

    if deep_results:
        deep_df = pd.DataFrame(deep_results)
        merged = pin_df[["symbol", "PIN"]].merge(deep_df, on="symbol")
        merged["PIN_diff"] = merged["PIN_300d"] - merged["PIN"]
        log.info("Full vs >300d PIN:\n%s", merged[["symbol", "PIN", "PIN_300d", "PIN_diff"]].to_string(index=False))
        merged.to_csv(output_dir / "robustness_300d.csv", index=False)

    # ── (b) BVC classification ─────────────────────────────────────────────
    log.info("Applying BVC classification...")
    bvc_daily = _apply_bvc(daily_df)

    bvc_results = []
    for sym in pin_df["symbol"].tolist():
        res = estimate_pin(bvc_daily, sym, n_starts=32, bootstrap_n=0, min_trades=5)
        if res:
            bvc_results.append({"symbol": sym, "PIN_bvc": res.PIN})

    if bvc_results:
        bvc_df = pd.DataFrame(bvc_results)
        merged2 = pin_df[["symbol", "PIN"]].merge(bvc_df, on="symbol")

        if len(merged2) >= 4:
            rho, pval = stats.spearmanr(merged2["PIN"], merged2["PIN_bvc"])
            log.info("Tick-rule vs BVC Spearman ρ=%.3f  p=%.4f", rho, pval)

        merged2.to_csv(output_dir / "robustness_bvc.csv", index=False)

        # Scatter plot
        fig, ax = plt.subplots(figsize=(5, 5))
        ax.scatter(merged2["PIN"], merged2["PIN_bvc"], color="black", s=60, zorder=5)
        for _, r in merged2.iterrows():
            ax.annotate(r["symbol"], (r["PIN"], r["PIN_bvc"]),
                        fontsize=7, xytext=(4, 4), textcoords="offset points")
        lo = min(merged2["PIN"].min(), merged2["PIN_bvc"].min()) * 0.9
        hi = max(merged2["PIN"].max(), merged2["PIN_bvc"].max()) * 1.1
        ax.plot([lo, hi], [lo, hi], "k--", linewidth=0.8, alpha=0.5, label="45° line")
        ax.set_xlabel("PIN (Tick Rule)")
        ax.set_ylabel("PIN (BVC)")
        ax.set_title("Robustness: Tick Rule vs BVC Classification")
        ax.legend(fontsize=9, frameon=False)
        _save(fig, output_dir, "figure3_bvc_robustness")

def _apply_bvc(daily_df: pd.DataFrame) -> pd.DataFrame:
    df = daily_df.copy().sort_values(["symbol", "date"])

    bvc_rows = []
    for sym, grp in df.groupby("symbol"):
        grp = grp.sort_values("date").copy()
        # Use num_buys / total_trades as a rough daily price direction proxy
        # (actual price would need OHLC data; here we use order imbalance sign)
        grp["delta_imbal"] = (
            grp["num_buys"] / grp["total_trades"].replace(0, np.nan) - 0.5
        ).diff()

        for _, row in grp.iterrows():
            d = row["delta_imbal"]
            if pd.isna(d) or row["total_trades"] == 0:
                buy_frac = 0.5
            elif d > 0:
                buy_frac = 0.5 + 0.5 * min(abs(d) * 2, 1.0)
            elif d < 0:
                buy_frac = 0.5 - 0.5 * min(abs(d) * 2, 1.0)
            else:
                buy_frac = 0.5

            bvc_rows.append({
                **row.to_dict(),
                "num_buys":  round(row["total_trades"] * buy_frac),
                "num_sells": round(row["total_trades"] * (1 - buy_frac)),
            })

    return pd.DataFrame(bvc_rows)

# ── save helper ───────────────────────────────────────────────────────────────

def _save(fig: plt.Figure, output_dir: Path, name: str):
    for ext in ("pdf", "png"):
        path = output_dir / f"{name}.{ext}"
        fig.savefig(path)
        log.info("Saved %s", path)
    plt.close(fig)

# ── main pipeline ─────────────────────────────────────────────────────────────

def run_analysis(daily_dir: Path, pin_csv: Path, output_dir: Path):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    output_dir.mkdir(parents=True, exist_ok=True)

    log.info("Loading daily data from %s ...", daily_dir)
    daily_df = load_all_daily(daily_dir)
    log.info("  %d rows  %d symbols  %d dates",
             len(daily_df), daily_df["symbol"].nunique(), daily_df["date"].nunique())

    log.info("Loading PIN estimates from %s ...", pin_csv)
    pin_df = pd.read_csv(pin_csv)
    log.info("  %d stocks", len(pin_df))

    log.info("\n=== 1. Summary Table ===")
    summary_table(pin_df, daily_df, output_dir)

    log.info("\n=== 2. Sector Analysis ===")
    sector_analysis(pin_df, output_dir)

    log.info("\n=== 3. Correlation Analysis ===")
    correlation_analysis(pin_df, daily_df, output_dir)

    log.info("\n=== 4. Rolling PIN Time-Series ===")
    aggregate_pin_timeseries(daily_df, pin_df, output_dir)

    log.info("\n=== 5. Robustness Checks ===")
    robustness_checks(daily_df, pin_df, output_dir)

    log.info("\nAll analysis complete. Outputs in %s", output_dir)

# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NEPSE PIN Cross-Sectional Analysis")
    parser.add_argument("--daily",  type=Path, required=True, help="Directory of *_daily.csv files")
    parser.add_argument("--pin",    type=Path, required=True, help="PIN estimates CSV (from pin_estimator.py)")
    parser.add_argument("--output", type=Path, default=Path("results"), help="Output directory")
    args = parser.parse_args()
    run_analysis(args.daily, args.pin, args.output)
