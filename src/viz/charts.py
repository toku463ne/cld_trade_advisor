"""Re-export facade — keeps existing imports working.

Content lives in the topic modules; add new charts there, not here:
  palette.py      — color constants
  price.py        — empty_figure, build_price_figure
  pair.py         — ZigzagPoint, build_pair_figure
  moving_corr.py  — build_moving_corr_figure
"""

from src.viz.palette import (  # noqa: F401
    BG, SIDEBAR_BG, CARD_BG, BORDER, TEXT, MUTED, ACCENT, GREEN, RED,
)
from src.viz.price import (  # noqa: F401
    empty_figure, build_price_figure,
)
from src.viz.pair import (  # noqa: F401
    ZigzagPoint, build_pair_figure,
)
from src.viz.moving_corr import (  # noqa: F401
    build_moving_corr_figure,
)
