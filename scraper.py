import argparse
import logging
import time
import re
import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── constants ─────────────────────────────────────────────────────────────────

BASE_URL    = "https://merolagani.com/Floorsheet.aspx"
AUTOSUGGEST = "https://merolagani.com/handlers/AutoSuggestHandler.ashx"
PAGE_SIZE        = 500   # Merolagani returns 500 rows per page
RATE_LIMIT       = 2.0   # seconds between requests (be a good citizen)
RATE_LIMIT_ZERO  = 0.3   # shorter pause after confirmed 0-trade days (holidays)

# Known NEPSE public holidays (fixed-date or near-fixed) — skip without a request.
# Covers 2021-2026. Lunar holidays (Dashain, Tihar, Holi) shift ~1-2 weeks per year
# and are NOT included here; those get caught by the 0-trade short-circuit below.
_FIXED_HOLIDAYS: set[date] = {
    # Prithvi Jayanti / National Unity Day — Jan 11
    date(2021,1,11), date(2022,1,11), date(2023,1,11), date(2024,1,11), date(2025,1,11), date(2026,1,11),
    # Martyrs' Day — Jan 30
    date(2021,1,30), date(2022,1,30), date(2023,1,30), date(2024,1,30), date(2025,1,30), date(2026,1,30),
    # Democracy Day — Feb 18 / Feb 19
    date(2021,2,18), date(2022,2,18), date(2023,2,18), date(2024,2,18), date(2025,2,18), date(2026,2,18),
    # Women's Day observed by NEPSE — Mar 8
    date(2021,3,8),  date(2022,3,8),  date(2023,3,8),  date(2024,3,8),  date(2025,3,8),  date(2026,3,8),
    # Nepali New Year (Baisakh 1) — around Apr 13/14
    date(2021,4,14), date(2022,4,14), date(2023,4,14), date(2024,4,14), date(2025,4,14), date(2026,4,14),
    # Labour Day — May 1
    date(2021,5,1),  date(2022,5,1),  date(2023,5,1),  date(2024,5,1),  date(2025,5,1),  date(2026,5,1),
    # Republic Day — May 29
    date(2021,5,29), date(2022,5,29), date(2023,5,29), date(2024,5,29), date(2025,5,29), date(2026,5,29),
    # Constitution Day — Sep 20 / Sep 19
    date(2021,9,20), date(2022,9,20), date(2023,9,20), date(2024,9,19), date(2025,9,20), date(2026,9,20),
    # Christmas — Dec 25
    date(2021,12,25),date(2022,12,25),date(2023,12,25),date(2024,12,25),date(2025,12,25),date(2026,12,25),
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── session ───────────────────────────────────────────────────────────────────

def make_session() -> requests.Session:
    s = requests.Session()
    s.verify = False
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://merolagani.com/",
    })
    return s

# ── company ID lookup ─────────────────────────────────────────────────────────

def get_company_id(session: requests.Session, symbol: str) -> int | None:
    try:
        r = session.get(
            AUTOSUGGEST,
            params={"type": "Company", "q": symbol},
            headers={"X-Requested-With": "XMLHttpRequest"},
            timeout=15,
        )
        r.raise_for_status()
        results = r.json()
        exact = [x for x in results if x.get("d") == symbol.upper()]
        if exact:
            return int(exact[0]["v"])
        log.warning("Symbol %s not found in autosuggest results", symbol)
    except Exception as e:
        log.error("Failed to get company ID for %s: %s", symbol, e)
    return None

# ── viewstate helpers ─────────────────────────────────────────────────────────

def extract_viewstate(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    def val(field_id: str) -> str:
        tag = soup.find("input", {"id": field_id})
        return tag["value"] if tag else ""

    return {
        "__VIEWSTATE":          val("__VIEWSTATE"),
        "__VIEWSTATEGENERATOR": val("__VIEWSTATEGENERATOR"),
        "__EVENTVALIDATION":    val("__EVENTVALIDATION"),
    }

def fetch_fresh_viewstate(session: requests.Session) -> dict:
    r = session.get(BASE_URL, timeout=90)
    r.raise_for_status()
    return extract_viewstate(r.text)

def parse_total_pages(html: str) -> int:
    m = re.search(r"Total pages:\s*(\d+)", html)
    return int(m.group(1)) if m else 1

# ── single page fetch ─────────────────────────────────────────────────────────

def fetch_page(
    session:    requests.Session,
    date_str:   str,      # MM/DD/YYYY
    company_id: int,
    symbol:     str,
    page:       int,      # 0-indexed
    vs:         dict,     # current viewstate — updated and returned each call
    retries:    int = 5,
) -> tuple[BeautifulSoup | None, int, dict]:
    for attempt in range(retries + 1):
        try:
            data = {
                "__EVENTTARGET":   "ctl00$ContentPlaceHolder1$lbtnSearchFloorsheet",
                "__EVENTARGUMENT": "",
                **vs,
                "ctl00$ASCompany$hdnAutoSuggest":  "0",
                "ctl00$ASCompany$txtAutoSuggest":  "",
                "ctl00$AutoSuggest1$hdnAutoSuggest":  "0",
                "ctl00$AutoSuggest1$txtAutoSuggest":  "",
                "ctl00$ContentPlaceHolder1$ASCompanyFilter$hdnAutoSuggest": str(company_id),
                "ctl00$ContentPlaceHolder1$ASCompanyFilter$txtAutoSuggest": symbol,
                "ctl00$ContentPlaceHolder1$txtBuyerBrokerCodeFilter":  "",
                "ctl00$ContentPlaceHolder1$txtSellerBrokerCodeFilter": "",
                "ctl00$ContentPlaceHolder1$txtFloorsheetDateFilter":   date_str,
                "ctl00$ContentPlaceHolder1$PagerControl1$hdnPCID":     "PC1",
                "ctl00$ContentPlaceHolder1$PagerControl1$hdnCurrentPage": str(page),
                "ctl00$ContentPlaceHolder1$PagerControl2$hdnPCID":     "PC2",
                "ctl00$ContentPlaceHolder1$PagerControl2$hdnCurrentPage": str(page),
            }
            # Page 0: trigger via search button; page N>0: trigger via pager button
            if page > 0:
                data["__EVENTTARGET"] = "ctl00$ContentPlaceHolder1$PagerControl1$btnPaging"
                data["ctl00$ContentPlaceHolder1$PagerControl1$btnPaging"] = ""

            r = session.post(
                BASE_URL,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=90,
            )
            r.raise_for_status()

            # Extract updated viewstate from this response for the next page
            updated_vs = extract_viewstate(r.text)
            total_pages = parse_total_pages(r.text)
            return BeautifulSoup(r.text, "html.parser"), total_pages, updated_vs

        except requests.RequestException as e:
            wait = 2 ** attempt * 3
            if attempt < retries:
                log.warning("Request failed (attempt %d/%d): %s. Retrying in %ds",
                            attempt + 1, retries, e, wait)
                time.sleep(wait)
            else:
                log.error("Gave up after %d retries for %s on %s p%d: %s",
                          retries, symbol, date_str, page, e)
                return None, 1, vs

# ── parse table ───────────────────────────────────────────────────────────────

def parse_table(soup: BeautifulSoup) -> list[dict]:
    table = soup.find("table")
    if not table:
        return []

    rows = []
    for tr in table.find_all("tr")[1:]:   # skip header
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(cells) < 8:
            continue
        try:
            rows.append({
                "transact_no":   cells[1],
                "symbol":        cells[2],
                "buyer_broker":  int(cells[3]),
                "seller_broker": int(cells[4]),
                "quantity":      int(cells[5].replace(",", "")),
                "rate":          float(cells[6].replace(",", "")),
                "amount":        float(cells[7].replace(",", "")),
            })
        except (ValueError, IndexError):
            continue  # skip malformed rows
    return rows

# ── scrape one symbol × one date ──────────────────────────────────────────────

def scrape_date(
    session:    requests.Session,
    symbol:     str,
    company_id: int,
    trade_date: date,
    vs:         dict,
) -> tuple[list[dict], dict]:
    date_str = trade_date.strftime("%m/%d/%Y")
    all_rows: list[dict] = []

    # Page 0 — also learns total_pages; vs is chained forward
    soup, total_pages, vs = fetch_page(
        session, date_str, company_id, symbol, page=0, vs=vs
    )
    if soup is None:
        return [], vs

    rows = parse_table(soup)
    if not rows:
        return [], vs   # no trades on this date (holiday / no data)

    all_rows.extend(rows)
    log.debug("  %s  page 0/%d  rows=%d", date_str, total_pages - 1, len(rows))

    for page in range(1, total_pages):
        time.sleep(RATE_LIMIT)
        soup, _, vs = fetch_page(
            session, date_str, company_id, symbol, page=page, vs=vs
        )
        if soup is None:
            break
        page_rows = parse_table(soup)
        if not page_rows:
            break
        all_rows.extend(page_rows)
        log.debug("  %s  page %d/%d  rows=%d  total=%d",
                  date_str, page, total_pages - 1, len(page_rows), len(all_rows))

    for row in all_rows:
        row["date"] = trade_date.isoformat()

    return all_rows, vs

# ── date range helpers ────────────────────────────────────────────────────────

def nepse_trading_days(start: date, end: date) -> list[date]:
    days = []
    d = start
    while d <= end:
        # NEPSE trades Sun(6)–Thu(3); skip Fri(4), Sat(5), and known public holidays
        if d.weekday() not in (4, 5) and d not in _FIXED_HOLIDAYS:
            days.append(d)
        d += timedelta(days=1)
    return days

# ── main scrape loop ──────────────────────────────────────────────────────────

def scrape_symbol(
    symbol:     str,
    start:      date,
    end:        date,
    output_dir: Path,
) -> Path | None:
    session = make_session()

    log.info("Resolving company ID for %s ...", symbol)
    company_id = get_company_id(session, symbol)
    if company_id is None:
        log.error("Cannot find company ID for %s — skipping", symbol)
        return None
    log.info("  %s → company_id=%d", symbol, company_id)

    trading_days = nepse_trading_days(start, end)
    log.info("Trading days in range: %d  (%s → %s)", len(trading_days), start, end)

    # Seed viewstate once — then chain it across all pages of all dates.
    # This eliminates one GET per date (saves ~50% of requests).
    log.info("  Fetching initial viewstate...")
    vs = fetch_fresh_viewstate(session)

    VS_REFRESH_EVERY = 50   # force a fresh GET every N days to prevent session expiry

    all_records: list[dict] = []
    for i, trade_date in enumerate(trading_days, 1):
        log.info("[%d/%d] %s  %s", i, len(trading_days), symbol, trade_date)

        # Periodic hard refresh + stale-viewstate recovery
        if i % VS_REFRESH_EVERY == 0 or not vs.get("__VIEWSTATE"):
            log.info("  Refreshing viewstate (day %d)...", i)
            vs = fetch_fresh_viewstate(session)

        records, vs = scrape_date(session, symbol, company_id, trade_date, vs)

        # If viewstate came back empty the POST likely hit an expired session — refresh now
        if not vs.get("__VIEWSTATE"):
            log.warning("  Viewstate went stale after %s — refreshing", trade_date)
            vs = fetch_fresh_viewstate(session)

        log.info("  → %d trades", len(records))
        all_records.extend(records)
        # Confirmed holiday/closure — don't burn the full rate-limit window
        time.sleep(RATE_LIMIT_ZERO if len(records) == 0 else RATE_LIMIT)

    if not all_records:
        log.warning("No data collected for %s", symbol)
        return None

    df = pd.DataFrame(all_records)
    # Reorder columns
    df = df[["date", "transact_no", "symbol", "buyer_broker", "seller_broker",
             "quantity", "rate", "amount"]]
    df = df.sort_values(["date", "transact_no"]).reset_index(drop=True)

    out_path = output_dir / f"{symbol}_{start}_{end}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    log.info("Saved %d records to %s", len(df), out_path)
    return out_path

# ── tick rule classifier ──────────────────────────────────────────────────────

def classify_trade_direction(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy().sort_values(["date", "symbol", "transact_no"]).reset_index(drop=True)

    directions = []

    for (trade_date, symbol), group in df.groupby(["date", "symbol"], sort=False):
        prices = group["rate"].values
        n = len(prices)
        d = [0] * n  # 0 = unclassified

        for i in range(1, n):
            if prices[i] > prices[i - 1]:
                # Uptick → buy
                d[i] = 1
            elif prices[i] < prices[i - 1]:
                # Downtick → sell
                d[i] = -1
            else:
                # Zero tick → inherit previous non-zero direction
                # (reverse tick rule: carry forward last known direction)
                prev_nonzero = next(
                    (d[j] for j in range(i - 1, -1, -1) if d[j] != 0),
                    0
                )
                d[i] = prev_nonzero

        directions.extend(d)

    df["direction"] = directions
    return df

def aggregate_daily(df: pd.DataFrame) -> pd.DataFrame:
    # Classify direction
    df = classify_trade_direction(df)

    records = []
    for (trade_date, symbol), group in df.groupby(["date", "symbol"]):
        buys  = group[group["direction"] ==  1]
        sells = group[group["direction"] == -1]
        uncl  = group[group["direction"] ==  0]

        records.append({
            "date":          trade_date,
            "symbol":        symbol,
            "num_buys":      len(buys),
            "num_sells":     len(sells),
            "unclassified":  len(uncl),
            "total_trades":  len(group),
            "buy_volume":    buys["quantity"].sum(),
            "sell_volume":   sells["quantity"].sum(),
            "total_volume":  group["quantity"].sum(),
        })

    daily = pd.DataFrame(records).sort_values(["symbol", "date"]).reset_index(drop=True)
    daily["date"] = pd.to_datetime(daily["date"])
    return daily

# ── data quality checker ──────────────────────────────────────────────────────

def quality_report(daily: pd.DataFrame, symbol: str, output_dir: Path) -> str:
    total_days    = len(daily)
    zero_days     = daily[daily["total_trades"] == 0]
    sparse_days   = daily[(daily["total_trades"] > 0) & (daily["total_trades"] < 5)]
    usable_days   = daily[daily["total_trades"] >= 5]
    high_uncl     = daily[
        (daily["total_trades"] > 0) &
        (daily["unclassified"] / daily["total_trades"] > 0.20)
    ]

    coverage_pct  = 100 * len(usable_days) / total_days if total_days > 0 else 0
    avg_trades    = daily["total_trades"].mean()
    median_trades = daily["total_trades"].median()

    lines = [
        f"# Data Quality Report: {symbol}",
        "",
        "## Summary",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Date range | {daily['date'].min().date()} → {daily['date'].max().date()} |",
        f"| Total trading days (Sun–Thu) | {total_days} |",
        f"| Days with ≥5 trades (usable) | {len(usable_days)} ({coverage_pct:.1f}%) |",
        f"| Zero-trade days | {len(zero_days)} |",
        f"| Sparse days (1–4 trades) | {len(sparse_days)} |",
        f"| Days with >20% unclassified | {len(high_uncl)} |",
        f"| Average daily trades | {avg_trades:.1f} |",
        f"| Median daily trades | {median_trades:.1f} |",
        "",
    ]

    if len(zero_days) > 0:
        lines += [
            "## Zero-Trade Days (first 20)",
            "",
            "| Date |",
            "|------|",
        ]
        for _, row in zero_days.head(20).iterrows():
            lines.append(f"| {row['date'].date()} |")
        lines.append("")

    if len(sparse_days) > 0:
        lines += [
            "## Sparse Days (<5 trades)",
            "",
            "| Date | Trades |",
            "|------|--------|",
        ]
        for _, row in sparse_days.head(20).iterrows():
            lines.append(f"| {row['date'].date()} | {row['total_trades']} |")
        lines.append("")

    lines += [
        "## Methodology Note",
        "",
        "Trade direction is inferred via the **tick rule** (Ellis, Michaely,",
        "O'Hara 2000): uptick → buy-initiated; downtick → sell-initiated;",
        "zero-tick → carry forward previous direction (reverse tick rule).",
        "The tick rule misclassifies approximately 30% of trades on average.",
        "Robustness checks using BVC (Bulk Volume Classification) are",
        "performed in the analysis stage.",
        "",
        "Zero-trade days are treated as missing data and excluded from MLE.",
        "Sparse days (<5 trades) are flagged but retained; users may wish",
        "to drop them for more reliable MLE convergence.",
    ]

    report = "\n".join(lines)

    out_path = output_dir / "quality" / f"{symbol}_quality.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report)
    log.info("Quality report saved to %s", out_path)
    return report

# ── pipeline ──────────────────────────────────────────────────────────────────

def run_pipeline(
    symbols:    list[str],
    start:      date,
    end:        date,
    output_dir: Path = Path("data"),
):
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "raw").mkdir(exist_ok=True)
    (output_dir / "daily").mkdir(exist_ok=True)
    (output_dir / "quality").mkdir(exist_ok=True)

    results = {}

    for symbol in symbols:
        log.info("=" * 60)
        log.info("Processing %s  (%s → %s)", symbol, start, end)
        log.info("=" * 60)

        # 1. Scrape raw floorsheet
        raw_path = scrape_symbol(symbol, start, end, output_dir / "raw")
        if raw_path is None:
            log.error("Skipping %s — no data scraped", symbol)
            continue

        # 2. Load, classify, aggregate
        raw_df = pd.read_csv(raw_path)
        daily_df = aggregate_daily(raw_df)

        daily_path = output_dir / "daily" / f"{symbol}_{start}_{end}_daily.csv"
        daily_df.to_csv(daily_path, index=False)
        log.info("Daily aggregation saved to %s", daily_path)

        # 3. Quality report
        quality_report(daily_df, symbol, output_dir)

        results[symbol] = {
            "raw_path":   raw_path,
            "daily_path": daily_path,
            "n_raw":      len(raw_df),
            "n_days":     len(daily_df),
        }

    return results

# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NEPSE Floorsheet Scraper for PIN Model")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--symbol",  type=str, help="Single stock symbol (e.g. NABIL)")
    group.add_argument("--symbols", type=str, nargs="+", help="Multiple symbols")
    parser.add_argument("--start",  type=date.fromisoformat, required=True,
                        help="Start date YYYY-MM-DD")
    parser.add_argument("--end",    type=date.fromisoformat, required=True,
                        help="End date YYYY-MM-DD")
    parser.add_argument("--output", type=Path, default=Path("data"),
                        help="Output directory (default: ./data)")
    parser.add_argument("--debug",  action="store_true")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    symbols = [args.symbol] if args.symbol else args.symbols
    run_pipeline(symbols, args.start, args.end, args.output)
