"""
probe_cutoff.py
---------------
Probes the NEPSE API (via NepseUnofficialApi) to find the oldest
available floorsheet date.

Strategy:
  1. Use NABIL (most liquid) as the probe stock.
  2. Spot-check recent dates to confirm the API is working.
  3. Binary search from today back to 3 years ago to find the cutoff.
  4. Fine-scan the boundary day-by-day.
  5. Spot-check 3 other liquid stocks at the cutoff date.

Run: python3 probe_cutoff.py
"""

import asyncio
from datetime import date, timedelta
import time

from nepse import AsyncNepse


# ── date helpers ──────────────────────────────────────────────────────────────

def is_trading_day(d: date) -> bool:
    """NEPSE trades Sun–Thu (weekday 6=Sun, 0=Mon, …, 3=Thu, 4=Fri, 5=Sat)."""
    return d.weekday() not in (4, 5)  # skip Friday and Saturday


def prev_trading_day(d: date) -> date:
    d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def nearest_trading_day(d: date) -> date:
    """Move forward to the nearest trading day if d falls on Fri/Sat."""
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


# ── probe helper ──────────────────────────────────────────────────────────────

async def has_data(nepse: AsyncNepse, symbol: str, d: date) -> bool:
    """Return True if the API returns ≥1 record for symbol on date d."""
    date_str = d.strftime("%Y-%m-%d")
    try:
        result = await nepse.getFloorSheetOf(symbol, business_date=date_str)
        # result is typically a dict or list; treat non-empty as success
        if isinstance(result, dict):
            content = result.get("floorsheets", result)
            if isinstance(content, dict):
                return content.get("totalElements", 0) > 0
            if isinstance(content, list):
                return len(content) > 0
        if isinstance(result, list):
            return len(result) > 0
        return False
    except Exception:
        return False


# ── main ──────────────────────────────────────────────────────────────────────

async def probe(symbol: str = "NABIL", max_years_back: int = 3):
    print(f"\n{'='*60}")
    print(f"  NEPSE Floorsheet Historical Cutoff Probe")
    print(f"  Symbol: {symbol}  |  Max lookback: {max_years_back} years")
    print(f"{'='*60}\n")

    nepse = AsyncNepse()
    nepse.init_client(tls_verify=False)
    nepse.floor_sheet_size = 10  # fetch minimal records per probe

    today = date.today()

    # ── Step 1: confirm recent data is accessible ──────────────────────────
    print("[1/3] Verifying recent data is accessible...")
    probe_date = prev_trading_day(today)
    found_recent = False
    for _ in range(10):
        result = await has_data(nepse, symbol, probe_date)
        print(f"      {probe_date}  →  {'OK' if result else 'no data'}")
        await asyncio.sleep(0.8)
        if result:
            found_recent = True
            break
        probe_date = prev_trading_day(probe_date)

    if not found_recent:
        print("\n[FAIL] No recent data found. API may be down or auth failed.")
        return

    recent_confirmed = probe_date

    # ── Step 2: check if data exists at the max lookback boundary ─────────
    print(f"\n[2/3] Checking {max_years_back}-year boundary...")
    earliest_boundary = nearest_trading_day(today - timedelta(days=max_years_back * 365))
    boundary_has_data = await has_data(nepse, symbol, earliest_boundary)
    print(f"      {earliest_boundary}  →  {'OK — data exists here!' if boundary_has_data else 'no data'}")
    await asyncio.sleep(0.8)

    if boundary_has_data:
        print(f"\n[NOTE] Data exists at the {max_years_back}-year boundary.")
        print(f"       Re-run with max_years_back={max_years_back + 2} to find true cutoff.")
        return

    # ── Step 3: binary search ──────────────────────────────────────────────
    print(f"\n[3/3] Binary searching for cutoff...")
    lo = earliest_boundary   # no data
    hi = recent_confirmed    # has data

    iteration = 0
    while (hi - lo).days > 5:
        mid = nearest_trading_day(lo + (hi - lo) // 2)
        result = await has_data(nepse, symbol, mid)
        tag = "OK" if result else "no data"
        print(f"      {mid}  →  {tag}  [iter {iteration + 1}, window {(hi-lo).days}d]")
        await asyncio.sleep(0.8)
        if result:
            hi = mid
        else:
            lo = mid
        iteration += 1

    # ── Step 4: fine-scan the boundary ────────────────────────────────────
    print(f"\n      Fine-scanning around {hi}...")
    candidate = hi
    for _ in range(14):   # up to 2 weeks of trading days
        prev = prev_trading_day(candidate)
        result = await has_data(nepse, symbol, prev)
        print(f"      {prev}  →  {'OK' if result else 'no data'}")
        await asyncio.sleep(0.6)
        if result:
            candidate = prev
        else:
            break

    days_back = (today - candidate).days
    trading_days_approx = int(days_back * 5 / 7)

    print(f"\n{'='*60}")
    print(f"  RESULT: Oldest available floorsheet date for {symbol}:")
    print(f"          {candidate}")
    print(f"          ~{days_back} calendar days  /  ~{trading_days_approx} trading days")
    print(f"{'='*60}\n")

    # ── Step 5: spot-check other liquid stocks at the cutoff ──────────────
    others = ["NMB", "NICA", "NLIC"]
    print(f"  Spot-check other stocks at cutoff date ({candidate}):")
    for sym in others:
        result = await has_data(nepse, sym, candidate)
        print(f"    {sym:<8}  →  {'OK' if result else 'no data'}")
        await asyncio.sleep(0.8)
    print()


if __name__ == "__main__":
    asyncio.run(probe(symbol="NABIL", max_years_back=3))
