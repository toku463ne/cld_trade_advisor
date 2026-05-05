"""Nikkei 225 constituent stock codes.

Fetches the current constituent list from the official Nikkei index page and
caches it to ``configs/nikkei225.ini`` so that the existing stock-code loader
can consume it as a named stock set.

CLI
---
    # Fetch from Nikkei and write configs/nikkei225.ini:
    uv run --env-file devenv python -m src.data.nikkei225

    # Print codes without writing:
    uv run --env-file devenv python -m src.data.nikkei225 --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
from loguru import logger

_NIKKEI_URL  = "https://indexes.nikkei.co.jp/en/nkave/index/component?idx=nk225"
_DEFAULT_INI = Path("configs/nikkei225.ini")
_SECTION     = "nikkei225"


def fetch_codes() -> list[tuple[str, str]]:
    """Return list of (yfinance_code, company_name) from the Nikkei official page.

    The constituent page returns multiple sectoral tables each with
    ``Code`` and ``Company Name`` columns.  Only pure 4-digit numeric codes
    are included (newer alphanumeric codes are skipped as yfinance does not
    support them yet).
    """
    import io
    import requests

    resp = requests.get(
        _NIKKEI_URL,
        headers={"User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)"},
        timeout=30,
    )
    resp.raise_for_status()
    tables = pd.read_html(io.StringIO(resp.text), flavor="html5lib")

    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for df in tables:
        if "Code" not in df.columns or "Company Name" not in df.columns:
            continue
        for _, row in df.iterrows():
            code = str(row["Code"]).strip()
            name = str(row["Company Name"]).strip()
            if len(code) == 4 and code.isdigit() and code not in seen:
                seen.add(code)
                pairs.append((code + ".T", name))

    if len(pairs) < 100:
        raise RuntimeError(
            f"Only found {len(pairs)} codes — Nikkei page format may have changed."
        )
    logger.info("Fetched {} Nikkei 225 codes from official page", len(pairs))
    return pairs


def write_ini(pairs: list[tuple[str, str]], path: Path = _DEFAULT_INI) -> None:
    """Write codes to an INI file consumable by load_stock_codes()."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"[{_SECTION}]\n"]
    for code, name in sorted(pairs, key=lambda p: p[0]):
        comment = f"  # {name}" if name else ""
        lines.append(f"{code}{comment}\n")
    path.write_text("".join(lines), encoding="utf-8")
    logger.info("Wrote {} codes to {}", len(pairs), path)


def load_or_fetch(path: Path = _DEFAULT_INI) -> list[str]:
    """Return codes from *path* if it exists, else fetch from Wikipedia."""
    if path.exists():
        from src.config import load_stock_codes
        codes = load_stock_codes(str(path), _SECTION)
        logger.info("Loaded {} codes from {}", len(codes), path)
        return codes
    logger.info("{} not found — fetching from Wikipedia …", path)
    pairs = fetch_codes()
    write_ini(pairs, path)
    return [c for c, _ in pairs]


def main(argv: list[str] | None = None) -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO")

    p = argparse.ArgumentParser(
        prog="python -m src.data.nikkei225",
        description="Fetch Nikkei 225 constituents from Wikipedia and write configs/nikkei225.ini",
    )
    p.add_argument("--out", default=str(_DEFAULT_INI), help="Output INI path")
    p.add_argument("--dry-run", action="store_true", help="Print codes without writing")
    args = p.parse_args(argv)

    pairs = fetch_codes()
    if args.dry_run:
        for code, name in pairs:
            print(f"{code}  # {name}")
        print(f"\nTotal: {len(pairs)}")
    else:
        write_ini(pairs, Path(args.out))


if __name__ == "__main__":
    main()
