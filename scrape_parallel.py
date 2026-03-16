import argparse
import subprocess
import sys
import time
import logging
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

SYMBOLS = [
    # Banking (most liquid — more pages per day)
    "NABIL", "NMB", "NICA", "SANIMA", "SBI",
    # Hydropower
    "NHPC", "UPPER", "AKPL",
    # Insurance
    "NLIC",
    # Finance
    "GMFIL", "CFCL",
]

MAX_WORKERS   = 6     # concurrent symbols
STAGGER_SECS  = 8     # seconds between launching each worker

def already_done(symbol: str, start: date, end: date, output_dir: Path) -> bool:
    expected = output_dir / "raw" / f"{symbol}_{start}_{end}.csv"
    if not expected.exists():
        return False
    # Quick sanity check: file should have at least a few KB
    return expected.stat().st_size > 10_000

def scrape_one(symbol: str, start: str, end: str, output_dir: str, log_dir: str) -> tuple[str, int]:
    log_path = Path(log_dir) / f"{symbol}.log"
    cmd = [
        sys.executable, "scraper.py",
        "--symbol", symbol,
        "--start",  start,
        "--end",    end,
        "--output", output_dir,
    ]
    with open(log_path, "w") as lf:
        result = subprocess.run(cmd, stdout=lf, stderr=lf, text=True)
    return symbol, result.returncode

def run_parallel(
    start:      date,
    end:        date,
    output_dir: Path,
    workers:    int,
    resume:     bool,
):
    output_dir.mkdir(parents=True, exist_ok=True)
    log_dir = output_dir / "logs"
    log_dir.mkdir(exist_ok=True)

    # Filter symbols if resuming
    todo = []
    for sym in SYMBOLS:
        if resume and already_done(sym, start, end, output_dir):
            log.info("SKIP  %s — output already exists", sym)
        else:
            todo.append(sym)

    if not todo:
        log.info("All symbols already scraped. Nothing to do.")
        return

    log.info("Scraping %d symbols with %d parallel workers", len(todo), workers)
    log.info("Symbols: %s", ", ".join(todo))
    log.info("Date range: %s → %s", start, end)
    log.info("Logs: %s/", log_dir)
    log.info("")

    start_str = start.isoformat()
    end_str   = end.isoformat()

    succeeded, failed = [], []

    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {}
        for i, sym in enumerate(todo):
            # Stagger launches to avoid simultaneous initial GETs
            if i > 0:
                time.sleep(STAGGER_SECS)
            future = pool.submit(scrape_one, sym, start_str, end_str,
                                 str(output_dir), str(log_dir))
            futures[future] = sym
            log.info("Launched  %s  (worker %d/%d)", sym, min(i+1, workers), workers)

        for future in as_completed(futures):
            sym = futures[future]
            try:
                _, rc = future.result()
                if rc == 0:
                    succeeded.append(sym)
                    log.info("DONE  %s", sym)
                else:
                    failed.append(sym)
                    log.error("FAIL  %s  (exit code %d) — check %s/%s.log",
                              sym, rc, log_dir, sym)
            except Exception as e:
                failed.append(sym)
                log.error("EXCEPTION  %s: %s", sym, e)

    log.info("")
    log.info("=" * 50)
    log.info("Succeeded: %s", ", ".join(succeeded) if succeeded else "none")
    if failed:
        log.error("Failed:    %s", ", ".join(failed))
        log.info("Re-run with --resume to retry failed symbols only")
    log.info("=" * 50)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Parallel NEPSE Floorsheet Scraper")
    parser.add_argument("--start",   type=date.fromisoformat, default=date(2021, 1, 1))
    parser.add_argument("--end",     type=date.fromisoformat, default=date(2026, 3, 16))
    parser.add_argument("--output",  type=Path, default=Path("data"))
    parser.add_argument("--workers", type=int,  default=MAX_WORKERS)
    parser.add_argument("--resume",  action="store_true",
                        help="Skip symbols whose output CSV already exists")
    args = parser.parse_args()

    run_parallel(args.start, args.end, args.output, args.workers, args.resume)
