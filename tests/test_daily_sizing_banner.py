"""Daily-tab sizing-regime banner (confluence item 2 adoption).

Locks the operator-facing text of the standing N225-60bar sizing-regime banner: NEUTRAL must
say HALF-SIZE + SKIP (the bimodal integer-lot guideline); bull/bear must say full size; an
unavailable momentum shows no banner. The underlying classification/trim logic is covered by
test_portfolio_sizing; this guards the wording the operator reads off the Daily tab.
"""
from __future__ import annotations


def _text(component) -> str:
    """Flatten a Dash component tree to its concatenated string children."""
    if isinstance(component, str):
        return component
    out = []
    children = getattr(component, "children", None)
    if isinstance(children, (list, tuple)):
        for c in children:
            out.append(_text(c))
    elif children is not None:
        out.append(_text(children))
    return " ".join(out)


def test_banner_none_when_momentum_unavailable():
    from src.viz.daily import _sizing_regime_banner
    assert _sizing_regime_banner(None) is None


def test_banner_neutral_says_half_size_and_skip():
    from src.viz.daily import _sizing_regime_banner
    txt = _text(_sizing_regime_banner(0.03))   # -0.1% < 3% <= 8.1% → neutral
    assert "NEUTRAL" in txt
    assert "HALF-SIZE" in txt
    assert "SKIP" in txt


def test_banner_bull_and_bear_say_full_size():
    from src.viz.daily import _sizing_regime_banner
    bull = _text(_sizing_regime_banner(0.12))   # > 8.1% → bull
    bear = _text(_sizing_regime_banner(-0.05))  # <= -0.1% → bear
    assert "BULL" in bull and "full size" in bull
    assert "BEAR" in bear and "full size" in bear
