"""Zigzag peak/trough detector.

A bar is a *confirmed* high (dir=2) if its high is the maximum of
``size`` bars on each side.  A bar is an *early* high (dir=1) if its
high is the maximum of ``size`` bars to the left and ``middle_size``
bars to the right.  Troughs use lows with dir=-2 / dir=-1 respectively.

Ported from otherproj/stockAnalyzer/lib/indicators.py::zigzag.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Peak:
    bar_index: int   # position in the input array
    direction: int   # 2=confirmed high, -2=confirmed low, 1=early high, -1=early low
    price: float


def detect_peaks(
    highs: list[float],
    lows: list[float],
    size: int = 5,
    middle_size: int = 2,
) -> list[Peak]:
    """Return zigzag peaks/troughs in chronological order.

    Parameters
    ----------
    highs, lows:
        Per-bar high and low prices (same length).
    size:
        Number of bars that must be lower/higher on *each side* of the
        candidate bar for a *confirmed* peak (dir ±2).
    middle_size:
        Number of bars after the candidate for an *early* peak (dir ±1).
    """
    peak_idxs: list[int] = []
    dirs: list[int] = []

    def _prices_for(d: int) -> list[float]:
        return highs if d > 0 else lows

    def _update(new_dir: int, i: int) -> None:
        prices = _prices_for(new_dir)
        if abs(new_dir) == 1 or not dirs or new_dir * dirs[-1] <= -4:
            peak_idxs.append(i)
            dirs.append(new_dir)
            return
        # Walk back through same-sign run and replace/merge
        for j in range(1, len(dirs) + 1):
            if new_dir * dirs[-j] <= -4:
                break
            jidx = peak_idxs[-j]
            if abs(dirs[-j]) == 2:
                if new_dir > 0:
                    if prices[i] > prices[jidx]:
                        dirs[-j] = 1   # demote old confirmed to early
                    else:
                        new_dir = 1
                        break
                else:
                    if prices[i] < prices[jidx]:
                        dirs[-j] = -1
                    else:
                        new_dir = -1
                        break
        peak_idxs.append(i)
        dirs.append(new_dir)

    n = len(highs)
    for i in range(n - size * 2, 0, -1):
        midi = i + size
        win_full  = slice(i, i + size * 2)
        win_early = slice(i, i + size + middle_size + 1)
        if highs[midi] == max(highs[win_full]):
            _update(2, midi)
        elif lows[midi] == min(lows[win_full]):
            _update(-2, midi)
        elif highs[midi] == max(highs[win_early]):
            _update(1, midi)
        elif lows[midi] == min(lows[win_early]):
            _update(-1, midi)

    dirs.reverse()
    peak_idxs.reverse()

    result: list[Peak] = []
    for idx, d in zip(peak_idxs, dirs):
        price = highs[idx] if d > 0 else lows[idx]
        result.append(Peak(bar_index=idx, direction=d, price=price))
    return result
