"""Single style module for the paper's matplotlib figures.

Every figure in ``experiments/analysis/figures.py`` (and any future figure
script) imports this module and calls :func:`figstyle.setup` once at import
time so all figures share one palette, one set of ink tokens, and one set of
matplotlib ``rcParams``.

Contents
--------
* Categorical slot colors (``BLUE``..``RED``) -- used AS-IS. The first three
  slots (``BLUE``, ``ORANGE``, ``AQUA``) form the all-pairs CVD-safe set
  (dE >= 9.2 on all three pairs); a figure must cap its series colors at
  three per axes so grayscale/CVD survival never depends on hue alone.
* ``SEQ_BLUES`` -- a light-to-dark sequential ramp for single-hue gradations.
* ``DIVERGING`` -- a blue<->red diverging ramp with a neutral gray midpoint,
  for signed quantities (delta, error, correlation).
* ``STATUS_GOOD``/``STATUS_WARNING``/``STATUS_SERIOUS``/``STATUS_CRITICAL``
  -- genuine status semantics only (health badges, error-rate callouts).
  NEVER as series colors; status hues live outside the categorical slots.
* Ink tokens (``INK``, ``INK_SECONDARY``, ``INK_MUTED``, ``GRID``,
  ``BASELINE``, ``SURFACE``) -- text, ticks, gridlines, and spine chrome.
* :func:`setup` -- apply the rcParams that all figures rely on.
* :func:`despine`, :func:`grid`, :func:`series_style` -- small helpers.

Series encoding
---------------
:func:`series_style(i)` returns ``(color, linestyle, marker)`` for the i-th
categorical series. The first three slots are the CVD-safe set; linestyles
and markers are fixed per slot so that, when printed in grayscale or viewed
by a colorblind reader, the series are distinguishable without hue alone.
The module caps series at three per axes; callers that need more should
split the panel rather than extend the ramp.
"""

from __future__ import annotations

from typing import Tuple

# ---------------------------------------------------------------------------
# Categorical slots (used AS-IS, slot order; do not re-derive).
# The first three (BLUE, ORANGE, AQUA) form the all-pairs-safe set; cap
# series colors at 3 per axes so grayscale/CVD survival never depends on
# hue alone.
# ---------------------------------------------------------------------------
BLUE    = "#2a78d6"   # slot 1
ORANGE  = "#eb6834"   # slot 2
AQUA    = "#1baf7a"   # slot 3
YELLOW  = "#eda100"   # slot 4
MAGENTA = "#e87ba4"   # slot 5
GREEN   = "#008300"   # slot 6
VIOLET  = "#6250d6"   # slot 7
RED     = "#e34948"   # slot 8

# The categorical slots in slot order -- the single source of truth.
CATEGORICAL: Tuple[str, ...] = (
    BLUE, ORANGE, AQUA, YELLOW, MAGENTA, GREEN, VIOLET, RED,
)

# All-pairs-safe prefix: first three slots.
SAFE_PREFIX: Tuple[str, ...] = (BLUE, ORANGE, AQUA)

# ---------------------------------------------------------------------------
# Sequential blue ramp, light -> dark (for single-hue magnitude encodings).
# ---------------------------------------------------------------------------
SEQ_BLUES: Tuple[str, ...] = (
    "#cde2fb",  # 0  lightest
    "#b7d3f6",  # 1
    "#9ec5f4",  # 2
    "#86b6ef",  # 3
    "#6da7ec",  # 4
    "#5598e7",  # 5
    "#3987e5",  # 6
    "#2a78d6",  # 7  (= BLUE, categorical slot 1)
    "#256abf",  # 8
    "#1c5cab",  # 9
    "#184f95",  # 10
    "#104281",  # 11
    "#0d366b",  # 12  darkest
)

# ---------------------------------------------------------------------------
# Diverging blue<->red with a neutral gray midpoint.
# ---------------------------------------------------------------------------
DIVERGING_MID = "#f0efec"
DIVERGING: Tuple[str, ...] = (
    "#2a78d6",   # blue extreme (most negative)
    "#f0efec",   # midpoint (neutral)
    "#e34948",   # red extreme (most positive)
)

# ---------------------------------------------------------------------------
# Status semantics ONLY (health badges / error callouts). Never as series
# colors -- status hues live outside the categorical slots.
# ---------------------------------------------------------------------------
STATUS_GOOD    = "#0ca30c"
STATUS_WARNING = "#fab219"
STATUS_SERIOUS = "#ec835a"
STATUS_CRITICAL = "#d03b3b"

# ---------------------------------------------------------------------------
# Ink tokens
# ---------------------------------------------------------------------------
INK          = "#0b0b0b"   # primary text
INK_SECONDARY = "#52514e"  # secondary text
INK_MUTED    = "#898781"   # axis labels / tick text
GRID         = "#e1e0d9"   # hairline gridlines
BASELINE     = "#c3c2b7"   # left + bottom spines
SURFACE      = "#ffffff"   # figure/axes background (white)

# ---------------------------------------------------------------------------
# Per-slot fixed linestyles and markers (grayscale/CVD survival):
# slot 0: BLUE solid  o
# slot 1: ORANGE dashed s
# slot 2: AQUA  dotted ^
# The first three slots are the all-pairs-safe set; cap at 3 per axes.
# ---------------------------------------------------------------------------
_LINESTYLES: Tuple[str, ...] = ("-", "--", ":")
_MARKERS:    Tuple[str, ...] = ("o", "s", "^")

_MAX_SERIES = 3


def setup() -> None:
    """Apply the paper-figure rcParams. Call once at module import."""
    from matplotlib import rcParams

    rcParams.update({
        # Font: DejaVu Sans everywhere (no Helvetica fallback in the list).
        "font.family":    "sans-serif",
        "font.sans-serif": ["DejaVu Sans"],
        "text.color":     INK,

        # Sizes.
        "font.size":         8.0,   # body / labels
        "axes.labelsize":    8.0,   # axis labels
        "xtick.labelsize":   7.0,   # tick labels
        "ytick.labelsize":   7.0,
        "legend.fontsize":   7.0,
        "axes.titlesize":    8.0,

        # No in-figure titles (captions live in the LaTeX source).
        "axes.titlelocation": "left",

        # Spines: only left + bottom, in the base chrome color.
        "axes.linewidth": 0.8,
        "axes.edgecolor": BASELINE,
        "axes.spines.top":   False,
        "axes.spines.right": False,
        "axes.spines.left":  True,
        "axes.spines.bottom": True,

        # Ticks: outward, muted, short.
        "xtick.direction": "out",
        "ytick.direction": "out",
        "xtick.major.size":  3.0,
        "ytick.major.size":  3.0,
        "xtick.major.width": 0.8,
        "ytick.major.width": 0.8,
        "xtick.color": INK_MUTED,
        "ytick.color": INK_MUTED,

        # Grid: hairline, horizontal only, behind data.
        "axes.grid":        True,
        "grid.linewidth":    0.5,
        "grid.color":        GRID,
        "grid.alpha":        1.0,
        "axes.axisbelow":    True,

        # Line / marker defaults (series_style overrides per series).
        "lines.linewidth":   1.6,
        "lines.markersize":  4.5,
        "lines.markeredgewidth": 0.8,

        # Legend: no frame.
        "legend.frameon":    False,
        "legend.handlelength": 1.6,

        # Output.
        "figure.dpi":    150,
        "savefig.dpi":   300,
        "figure.facecolor": SURFACE,
        "axes.facecolor":   SURFACE,
        "pdf.fonttype":  42,
        "ps.fonttype":   42,
    })


def despine(ax) -> None:
    """Remove top and right spines from ``ax``."""
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def grid(ax, horizontal: bool = True, vertical: bool = False) -> None:
    """Hairline grid behind data. Horizontal by default (matches ``setup()``)."""
    ax.grid(which="major", axis="y" if horizontal else "x",
            linestyle="-", linewidth=0.5, color=GRID, alpha=1.0)
    if horizontal and not vertical:
        # Explicitly disable vertical gridlines even if rcParams enables them.
        ax.grid(which="major", axis="x", visible=False)
    if vertical and not horizontal:
        ax.grid(which="major", axis="y", visible=False)
    ax.set_axisbelow(True)


def series_style(i: int) -> Tuple[str, str, str]:
    """Return ``(color, linestyle, marker)`` for the i-th categorical series.

    Slot order (0=BLUE, 1=ORANGE, 2=AQUA, ...). Linestyles and markers are
    fixed per slot so that, when printed in grayscale or viewed by a
    colorblind reader, the series are distinguishable without hue alone.

    Contract: defined for i in 0..2 only (the all-pairs-safe set -- BLUE /
    ORANGE / AQUA with linestyles ``-`` / ``--`` / `:` and markers
    ``o`` / ``s`` / ``^``). Raises ``IndexError`` for i < 0 or i >= 3:
    the module caps series colors at three per axes, so callers with more
    series must split the panel rather than extend the ramp.
    """
    if not (0 <= i < _MAX_SERIES):
        raise IndexError(
            f"series_style: i={i} out of range "
            f"[0, {_MAX_SERIES}) -- cap is 3 series per axes; "
            f"split the panel instead of extending the ramp."
        )
    return CATEGORICAL[i], _LINESTYLES[i], _MARKERS[i]


__all__ = [
    # categorical
    "BLUE", "ORANGE", "AQUA", "YELLOW",
    "MAGENTA", "GREEN", "VIOLET", "RED",
    "CATEGORICAL", "SAFE_PREFIX",
    # sequential
    "SEQ_BLUES",
    # diverging
    "DIVERGING", "DIVERGING_MID",
    # status
    "STATUS_GOOD", "STATUS_WARNING", "STATUS_SERIOUS", "STATUS_CRITICAL",
    # ink
    "INK", "INK_SECONDARY", "INK_MUTED", "GRID", "BASELINE", "SURFACE",
    # helpers
    "setup", "despine", "grid", "series_style",
]
