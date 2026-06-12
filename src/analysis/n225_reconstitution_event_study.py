"""Nikkei 225 reconstitution events — Stage-0 event study (proposal #1, 2026-06-13).

Index reconstitution is the one "large per-name edge, few names suffice" direction not
yet refuted (event-driven was rejected only for lack of data; the event list is now
hand-curated from Nikkei Inc. press releases in data/nikkei225_reconstitution.csv).
Mechanics: passive N225 trackers must trade at the CLOSE of effective_date − 1 business
day, forcing one-day flow ~ several days of ADV in the affected names. Documented
pattern elsewhere: deletions overshoot down then REVERT (buy the forced dip — long,
fits the book); additions run up into the close then FADE (short idea — out of scope,
short sleeve is closed; measured here descriptively only).

Per tradable leg, β-stripped CAR (β from 120 bars ending 10 bars before announcement,
vs ^N225):
  ANT     close(announce) -> close(eff−1)      announcement-to-execution drift
  POST_h  close(eff−1) -> close(eff−1 + h)     h = 5 / 20 / 60 bars (the tradable leg)
  FULL    close(announce) -> close(eff−1 + 60) does the move round-trip?

Announcements are published after the session close, so close(announce) is pre-news.

Pre-stated Stage-0 gates (deletion-reversal = the deployable hypothesis):
  ESCALATE to a pre-registration only if deletes POST_20 or POST_60 pooled β-stripped
  CAR ≥ +3%, with ≥60% of events positive, and not concentrated in a single calendar
  year. Otherwise descriptive REJECT — write the memory and stop.
  (n≈22/leg is small by construction; the edge must be LARGE to matter, which is the
  premise of the direction. A +1% mean with wide spread = not worth the pre-reg.)

Read-only.  Run:
    PYTHONPATH=. uv run --env-file devenv python -m src.analysis.n225_reconstitution_event_study
"""
from __future__ import annotations

import bisect
import csv
import datetime
from collections import defaultdict
from pathlib import Path

import numpy as np
from loguru import logger

from src.data.db import get_session
from src.simulator.cache import DataCache

_CSV = Path(__file__).parent / "data" / "nikkei225_reconstitution.csv"
_BETA_BARS = 120
_BETA_GAP = 10          # end the beta window this many bars before the announcement
_POST_H = [5, 20, 60]
_TODAY = datetime.date(2026, 6, 13)


def _load_events() -> list[dict]:
    rows = []
    with open(_CSV, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                continue
            f2 = [line] + list(f)
            rdr = csv.DictReader(f2)
            for r in rdr:
                if r.get("event_id"):
                    rows.append(r)
            break
    for r in rows:
        r["announce_date"] = datetime.date.fromisoformat(r["announce_date"])
        r["effective_date"] = datetime.date.fromisoformat(r["effective_date"])
        r["tradable"] = r["tradable"] == "1"
    return rows


def _closes(cache: DataCache) -> tuple[list[datetime.date], dict[datetime.date, float],
                                        dict[datetime.date, float]]:
    dts, cmap, omap, seen = [], {}, {}, set()
    for b in cache.bars:
        d = b.dt.date()
        if d in seen:
            continue
        seen.add(d)
        dts.append(d)
        cmap[d] = b.close
        omap[d] = b.open
    dts.sort()
    return dts, cmap, omap


def _ret(cmap, dts, i0: int, i1: int) -> float | None:
    if i0 < 0 or i1 >= len(dts) or i0 >= i1:
        return None
    p0, p1 = cmap[dts[i0]] or None, cmap[dts[i1]] or None
    if not p0 or not p1:
        return None
    return p1 / p0 - 1.0


def _beta(s_dts, s_cmap, n_cmap, end_idx: int) -> float | None:
    lo = end_idx - _BETA_BARS
    if lo < 1:
        return None
    sr, nr = [], []
    for k in range(lo, end_idx):
        d0, d1 = s_dts[k - 1], s_dts[k]
        if d0 in n_cmap and d1 in n_cmap and s_cmap[d0] and n_cmap[d0]:
            sr.append(s_cmap[d1] / s_cmap[d0] - 1.0)
            nr.append(n_cmap[d1] / n_cmap[d0] - 1.0)
    if len(sr) < 60:
        return None
    sr_a, nr_a = np.asarray(sr), np.asarray(nr)
    v = nr_a.var()
    return float(np.cov(sr_a, nr_a)[0, 1] / v) if v > 0 else None


def _n225_ret(n_dts, n_cmap, d0: datetime.date, d1: datetime.date) -> float | None:
    i0 = bisect.bisect_right(n_dts, d0) - 1
    i1 = bisect.bisect_right(n_dts, d1) - 1
    if i0 < 0 or i1 < 0:
        return None
    p0, p1 = n_cmap[n_dts[i0]], n_cmap[n_dts[i1]]
    return p1 / p0 - 1.0 if p0 and p1 else None


def _line(label: str, vals: list[float]) -> str:
    if not vals:
        return f"    {label:<8} n=0"
    a = np.asarray(vals)
    t = a.mean() / (a.std(ddof=1) / np.sqrt(len(a))) if len(a) > 1 and a.std(ddof=1) > 0 else float("nan")
    return (f"    {label:<8} n={len(a):>2}  mean {a.mean()*100:+6.2f}%  med {np.median(a)*100:+6.2f}%  "
            f"pos {float((a > 0).mean()*100):3.0f}%  t={t:+.2f}")


def run() -> None:
    events = _load_events()
    logger.info("loaded {} legs ({} tradable)", len(events), sum(e["tradable"] for e in events))

    lo = min(e["announce_date"] for e in events) - datetime.timedelta(days=700)
    hi = _TODAY
    with get_session() as s:
        n225 = DataCache("^N225", "1d")
        n225.load(s, datetime.datetime.combine(lo, datetime.time.min, tzinfo=datetime.timezone.utc),
                  datetime.datetime.combine(hi, datetime.time.max, tzinfo=datetime.timezone.utc))
    n_dts, n_cmap, _ = _closes(n225)

    res: dict[str, dict[str, list[float]]] = {"add": defaultdict(list), "delete": defaultdict(list)}
    by_year: dict[str, dict[int, list[float]]] = {"add": defaultdict(list), "delete": defaultdict(list)}
    detail: list[str] = []
    missing: list[str] = []

    for e in events:
        if not e["tradable"]:
            continue
        code, leg = e["code"], e["leg"]
        w0 = e["announce_date"] - datetime.timedelta(days=400)
        w1 = min(e["effective_date"] + datetime.timedelta(days=150), hi)
        with get_session() as s:
            c = DataCache(code, "1d")
            c.load(s, datetime.datetime.combine(w0, datetime.time.min, tzinfo=datetime.timezone.utc),
                   datetime.datetime.combine(w1, datetime.time.max, tzinfo=datetime.timezone.utc))
        s_dts, s_cmap, s_omap = _closes(c)
        if not s_dts:
            missing.append(f"{code} ({e['name']}, {e['event_id']})")
            continue
        ia = bisect.bisect_right(s_dts, e["announce_date"]) - 1   # close(announce) = pre-news
        ie = bisect.bisect_left(s_dts, e["effective_date"]) - 1   # eff−1 = forced-flow close
        if ia < 0 or ie <= ia:
            missing.append(f"{code} ({e['name']}, {e['event_id']}) — no bars around event")
            continue
        b = _beta(s_dts, s_cmap, n_cmap, max(ia - _BETA_GAP, 1))
        b_used = b if b is not None else 1.0

        def car(i0: int, i1: int) -> float | None:
            r = _ret(s_cmap, s_dts, i0, i1)
            if r is None:
                return None
            m = _n225_ret(n_dts, n_cmap, s_dts[i0], s_dts[i1])
            return r - b_used * m if m is not None else None

        parts = {"ANT": car(ia, ie)}
        # ANT_CAP = the slice a manual trader can capture under the two-bar fill
        # convention: open of announce+1 (announcement is after the close) -> close of
        # eff−1.  Exploratory (not a pre-stated gate); excludes the overnight gap.
        if ia + 1 <= ie and s_omap.get(s_dts[ia + 1]) and s_cmap.get(s_dts[ie]):
            r_cap = s_cmap[s_dts[ie]] / s_omap[s_dts[ia + 1]] - 1.0
            m_cap = _n225_ret(n_dts, n_cmap, s_dts[ia], s_dts[ie])
            parts["ANT_CAP"] = r_cap - b_used * m_cap if m_cap is not None else None
        else:
            parts["ANT_CAP"] = None
        for h in _POST_H:
            parts[f"POST_{h}"] = car(ie, ie + h)
        parts["FULL"] = car(ia, ie + 60)
        yr = e["announce_date"].year
        for k, v in parts.items():
            if v is not None:
                res[leg][k].append(v)
                if k == "POST_20":
                    by_year[leg][yr].append(v)
        d = " ".join(f"{k}={v*100:+6.2f}%" if v is not None else f"{k}=  n/a  "
                     for k, v in parts.items())
        detail.append(f"  {e['event_id']:<9} {leg:<6} {code:<7} β={b_used:4.2f}{'*' if b is None else ' '} {d}  {e['name']}")

    print("\n=== N225 reconstitution — Stage-0 event study (β-stripped CAR vs ^N225) ===")
    print(f"events file: {_CSV.name}; β window {_BETA_BARS} bars ending {_BETA_GAP} before announce "
          f"(*=β unavailable, 1.0 used)\n")
    for leg in ("delete", "add"):
        print(f"  {leg.upper()} legs ({'deployable: buy forced dip at eff−1 close' if leg == 'delete' else 'descriptive only (fade = short idea, out of scope)'}):")
        for k in ["ANT", "ANT_CAP"] + [f"POST_{h}" for h in _POST_H] + ["FULL"]:
            print(_line(k, res[leg].get(k, [])))
        print()

    print("  per-year POST_20 means (concentration check):")
    for leg in ("delete", "add"):
        items = sorted(by_year[leg].items())
        s_ = "  ".join(f"{y}:{np.mean(v)*100:+5.1f}%({len(v)})" for y, v in items)
        print(f"    {leg:<6} {s_}")

    print("\n  per-event detail:")
    for d in sorted(detail):
        print(d)
    if missing:
        print("\n  MISSING price data (need OHLCV download):")
        for m in missing:
            print(f"    {m}")

    dels = res["delete"]
    print("\n(VERDICT inputs — gates in module docstring)")
    for k in ("POST_20", "POST_60"):
        v = dels.get(k, [])
        if v:
            a = np.asarray(v)
            ok = a.mean() >= 0.03 and (a > 0).mean() >= 0.60
            print(f"  deletes {k}: mean {a.mean()*100:+.2f}%  pos {float((a>0).mean()*100):.0f}%  "
                  f"→ {'meets' if ok else 'fails'} (+3% & 60% pos)")
    print()


if __name__ == "__main__":
    run()
