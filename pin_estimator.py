import logging
import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import gammaln

log = logging.getLogger(__name__)

# ── parameter layout ──────────────────────────────────────────────────────────
# θ = [alpha, delta, mu, eps_b, eps_s]
#       0       1     2    3      4
ALPHA, DELTA, MU, EPS_B, EPS_S = 0, 1, 2, 3, 4

BOUNDS = [
    (1e-4, 1 - 1e-4),   # alpha  ∈ (0, 1)
    (1e-4, 1 - 1e-4),   # delta  ∈ (0, 1)
    (1e-4, None),        # mu     > 0
    (1e-4, None),        # eps_b  > 0
    (1e-4, None),        # eps_s  > 0
]

N_STARTS    = 64    # random starting points for multi-start MLE
BOOTSTRAP_N = 1000  # bootstrap iterations for CI
RANDOM_SEED = 42

# ── log-likelihood ────────────────────────────────────────────────────────────

def _log_poisson(x: np.ndarray, lam: float) -> np.ndarray:
    return x * np.log(lam) - lam - gammaln(x + 1)

def log_likelihood(theta: np.ndarray, buys: np.ndarray, sells: np.ndarray) -> float:
    alpha, delta, mu, eps_b, eps_s = theta

    # Log mixture weights
    log_w_good = np.log(alpha) + np.log(1 - delta)  # α(1-δ)
    log_w_bad  = np.log(alpha) + np.log(delta)       # αδ
    log_w_none = np.log(1 - alpha)                   # (1-α)

    # Log Poisson terms for buys and sells under each scenario
    # good news day: buys ~ Poisson(μ+ε_b), sells ~ Poisson(ε_s)
    log_b_good = _log_poisson(buys, mu + eps_b)
    log_s_good = _log_poisson(sells, eps_s)

    # bad news day: buys ~ Poisson(ε_b), sells ~ Poisson(μ+ε_s)
    log_b_bad  = _log_poisson(buys, eps_b)
    log_s_bad  = _log_poisson(sells, mu + eps_s)

    # no event day: buys ~ Poisson(ε_b), sells ~ Poisson(ε_s)
    log_b_none = _log_poisson(buys, eps_b)
    log_s_none = _log_poisson(sells, eps_s)

    # Log of each mixture component (per day)
    log_comp_good = log_w_good + log_b_good + log_s_good
    log_comp_bad  = log_w_bad  + log_b_bad  + log_s_bad
    log_comp_none = log_w_none + log_b_none + log_s_none

    # Combine via log-sum-exp for numerical stability
    # log-sum-exp(a, b, c) = max + log(exp(a-max) + exp(b-max) + exp(c-max))
    stack = np.column_stack([log_comp_good, log_comp_bad, log_comp_none])
    log_mix = _logsumexp_rows(stack)

    total = np.sum(log_mix)
    return total if np.isfinite(total) else -1e18

def _logsumexp_rows(arr: np.ndarray) -> np.ndarray:
    row_max = arr.max(axis=1, keepdims=True)
    return row_max.squeeze() + np.log(np.exp(arr - row_max).sum(axis=1))

def neg_log_likelihood(theta: np.ndarray, buys: np.ndarray, sells: np.ndarray) -> float:
    return -log_likelihood(theta, buys, sells)

# ── starting value generation ─────────────────────────────────────────────────

def generate_starting_values(
    n_starts: int,
    mean_buys: float,
    mean_sells: float,
    rng: np.random.Generator,
) -> list[np.ndarray]:
    mean_trades = (mean_buys + mean_sells) / 2
    starts = []

    for _ in range(n_starts):
        alpha = rng.uniform(0.05, 0.95)
        delta = rng.uniform(0.05, 0.95)
        # μ should be on the order of the excess daily trades
        mu    = rng.uniform(0.5, max(mean_trades * 0.8, 1.0))
        eps_b = rng.uniform(0.5, max(mean_buys  * 0.8, 1.0))
        eps_s = rng.uniform(0.5, max(mean_sells * 0.8, 1.0))
        starts.append(np.array([alpha, delta, mu, eps_b, eps_s]))

    # Also add a few structured starts (symmetry axis, etc.)
    mid = mean_trades / 2
    extra_starts = [
        np.array([0.5,  0.5,  mid,  mid,  mid]),
        np.array([0.3,  0.3,  mid,  mean_buys * 0.5, mean_sells * 0.5]),
        np.array([0.7,  0.5,  mid * 1.5, mean_buys * 0.3, mean_sells * 0.3]),
        np.array([0.2,  0.7,  mid * 0.5, mean_buys * 0.6, mean_sells * 0.6]),
    ]
    starts.extend(extra_starts[:max(0, n_starts - len(starts))])

    return starts[:n_starts]

# ── single MLE run ────────────────────────────────────────────────────────────

def _single_mle(
    theta0: np.ndarray,
    buys: np.ndarray,
    sells: np.ndarray,
) -> tuple[np.ndarray | None, float, bool]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = minimize(
            neg_log_likelihood,
            x0=theta0,
            args=(buys, sells),
            method="L-BFGS-B",
            bounds=BOUNDS,
            options={"maxiter": 1000, "ftol": 1e-12, "gtol": 1e-8},
        )

    if result.success and np.isfinite(result.fun):
        return result.x, result.fun, True
    # Accept solutions that didn't formally converge but improved significantly
    if np.isfinite(result.fun) and result.fun < neg_log_likelihood(theta0, buys, sells):
        return result.x, result.fun, False
    return None, np.inf, False

# ── main estimator ────────────────────────────────────────────────────────────

@dataclass
class PINResult:
    symbol:           str
    alpha:            float
    delta:            float
    mu:               float
    eps_b:            float
    eps_s:            float
    PIN:              float
    PIN_lower_CI:     float
    PIN_upper_CI:     float
    log_likelihood:   float
    n_days:           int
    convergence_rate: float   # fraction of starts that converged

def compute_pin(alpha: float, mu: float, eps_b: float, eps_s: float) -> float:
    return (alpha * mu) / (alpha * mu + eps_b + eps_s)

def estimate_pin(
    daily_df:     pd.DataFrame,
    symbol:       str,
    n_starts:     int = N_STARTS,
    bootstrap_n:  int = BOOTSTRAP_N,
    min_trades:   int = 5,
    seed:         int = RANDOM_SEED,
) -> PINResult | None:
    # ── data prep ──────────────────────────────────────────────────────────
    sym_df = daily_df[daily_df["symbol"] == symbol].copy() if "symbol" in daily_df.columns else daily_df.copy()
    sym_df = sym_df[sym_df["num_buys"] + sym_df["num_sells"] >= min_trades]

    if len(sym_df) < 30:
        log.warning("%s: only %d usable days — skipping (need ≥30)", symbol, len(sym_df))
        return None

    buys  = sym_df["num_buys"].values.astype(float)
    sells = sym_df["num_sells"].values.astype(float)
    n_days = len(buys)

    log.info("%s: %d usable trading days  (mean buys=%.1f  sells=%.1f)",
             symbol, n_days, buys.mean(), sells.mean())

    rng = np.random.default_rng(seed)

    # ── multi-start MLE ────────────────────────────────────────────────────
    starts = generate_starting_values(n_starts, buys.mean(), sells.mean(), rng)

    best_theta  = None
    best_neg_ll = np.inf
    n_converged = 0

    for i, theta0 in enumerate(starts):
        theta_opt, neg_ll, converged = _single_mle(theta0, buys, sells)
        if converged:
            n_converged += 1
        if theta_opt is not None and neg_ll < best_neg_ll:
            best_neg_ll = neg_ll
            best_theta  = theta_opt

    convergence_rate = n_converged / n_starts
    if convergence_rate < 0.80:
        log.warning("%s: only %.0f%% of starts converged (< 80%% threshold)",
                    symbol, convergence_rate * 100)

    if best_theta is None:
        log.error("%s: MLE failed — no valid solution found", symbol)
        return None

    alpha, delta, mu, eps_b, eps_s = best_theta
    pin = compute_pin(alpha, mu, eps_b, eps_s)

    log.info("%s: α=%.3f  δ=%.3f  μ=%.2f  ε_b=%.2f  ε_s=%.2f  PIN=%.4f  conv=%.0f%%",
             symbol, alpha, delta, mu, eps_b, eps_s, pin, convergence_rate * 100)

    # ── bootstrap confidence interval ─────────────────────────────────────
    pin_boot = _bootstrap_pin(buys, sells, best_theta, bootstrap_n, n_starts, rng)
    lower_ci = float(np.percentile(pin_boot, 2.5))
    upper_ci = float(np.percentile(pin_boot, 97.5))

    log.info("%s: PIN 95%% CI = [%.4f, %.4f]", symbol, lower_ci, upper_ci)

    return PINResult(
        symbol           = symbol,
        alpha            = float(alpha),
        delta            = float(delta),
        mu               = float(mu),
        eps_b            = float(eps_b),
        eps_s            = float(eps_s),
        PIN              = float(pin),
        PIN_lower_CI     = lower_ci,
        PIN_upper_CI     = upper_ci,
        log_likelihood   = float(-best_neg_ll),
        n_days           = n_days,
        convergence_rate = float(convergence_rate),
    )

def _bootstrap_pin(
    buys:      np.ndarray,
    sells:     np.ndarray,
    theta_hat: np.ndarray,
    n_boot:    int,
    n_starts:  int,
    rng:       np.random.Generator,
) -> np.ndarray:
    n = len(buys)
    pin_samples = []

    mean_b = buys.mean()
    mean_s = sells.mean()

    for _ in range(n_boot):
        idx = rng.integers(0, n, size=n)
        b_b = buys[idx]
        s_b = sells[idx]

        # Try MLE point estimate as start first, then a few random perturbations
        theta_opt, neg_ll, _ = _single_mle(theta_hat, b_b, s_b)

        if theta_opt is None:
            # Fallback: try 5 random starts
            for __ in range(5):
                t0 = theta_hat * rng.uniform(0.5, 2.0, size=5)
                t0 = np.clip(t0, [b[0] for b in BOUNDS[:2]] + [1e-4] * 3, None)
                t0[:2] = np.clip(t0[:2], 1e-4, 1 - 1e-4)
                theta_opt, neg_ll, _ = _single_mle(t0, b_b, s_b)
                if theta_opt is not None:
                    break

        if theta_opt is not None:
            pin_samples.append(compute_pin(
                theta_opt[ALPHA], theta_opt[MU], theta_opt[EPS_B], theta_opt[EPS_S]
            ))

    if len(pin_samples) < n_boot * 0.5:
        log.warning("Bootstrap: only %d/%d iterations converged", len(pin_samples), n_boot)

    return np.array(pin_samples) if pin_samples else np.array([np.nan, np.nan])

# ── batch estimation ──────────────────────────────────────────────────────────

def estimate_all(
    daily_df:    pd.DataFrame,
    symbols:     list[str] | None = None,
    n_starts:    int = N_STARTS,
    bootstrap_n: int = BOOTSTRAP_N,
    min_trades:  int = 5,
    seed:        int = RANDOM_SEED,
) -> pd.DataFrame:
    if symbols is None:
        symbols = sorted(daily_df["symbol"].unique())

    results = []
    for i, sym in enumerate(symbols, 1):
        log.info("─" * 50)
        log.info("Estimating PIN for %s  [%d/%d]", sym, i, len(symbols))
        result = estimate_pin(
            daily_df, sym,
            n_starts=n_starts,
            bootstrap_n=bootstrap_n,
            min_trades=min_trades,
            seed=seed + i,  # different seed per stock, but reproducible
        )
        if result is not None:
            results.append(result.__dict__)

    if not results:
        return pd.DataFrame()

    df = pd.DataFrame(results)
    df = df.sort_values("PIN", ascending=False).reset_index(drop=True)
    return df

# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    from pathlib import Path

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="PIN MLE Estimator for NEPSE")
    parser.add_argument("--input",  type=Path, required=True,
                        help="Path to daily CSV (output of scraper.py aggregate_daily)")
    parser.add_argument("--output", type=Path, default=Path("pin_results.csv"),
                        help="Output CSV path for PIN estimates")
    parser.add_argument("--symbols", nargs="+", help="Symbols to estimate (default: all)")
    parser.add_argument("--n-starts",    type=int, default=N_STARTS)
    parser.add_argument("--bootstrap-n", type=int, default=BOOTSTRAP_N)
    parser.add_argument("--min-trades",  type=int, default=5)
    args = parser.parse_args()

    daily = pd.read_csv(args.input)
    results = estimate_all(
        daily,
        symbols=args.symbols,
        n_starts=args.n_starts,
        bootstrap_n=args.bootstrap_n,
        min_trades=args.min_trades,
    )

    if results.empty:
        print("No results produced.")
    else:
        results.to_csv(args.output, index=False)
        print(f"\nPIN estimates saved to {args.output}")
        print(results[["symbol", "PIN", "PIN_lower_CI", "PIN_upper_CI",
                        "alpha", "mu", "n_days", "convergence_rate"]].to_string(index=False))
