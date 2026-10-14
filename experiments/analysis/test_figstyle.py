"""Tests for the paper figure style module (``figstyle.py``).

Covers the two things that must not drift:

* the exact color constants (categorical slots, sequential ramp,
  diverging ramp, status hues, ink tokens) -- these are specified
  AS-IS in the dispatch and are the ground truth the paper figures
  import from;
* the ``series_style(i)`` contract: for i in 0..2 it returns the
  all-pairs-safe set in slot order with distinct (linestyle, marker)
  so grayscale/CVD survival never depends on hue alone; for i outside
  0..2 it raises ``IndexError`` (cap of 3 series per axes).

Matplotlib is imported but no figure is created, so the suite stays
fast and headless-safe.
"""

from __future__ import annotations

import pytest

from experiments.analysis import figstyle


# ---------------------------------------------------------------------------
# Categorical slots -- exact hexes, exact slot order
# ---------------------------------------------------------------------------
def test_categorical_slot_hexes():
    assert figstyle.BLUE    == "#2a78d6"
    assert figstyle.ORANGE  == "#eb6834"
    assert figstyle.AQUA    == "#1baf7a"
    assert figstyle.YELLOW  == "#eda100"
    assert figstyle.MAGENTA == "#e87ba4"
    assert figstyle.GREEN   == "#008300"
    assert figstyle.VIOLET  == "#6250d6"
    assert figstyle.RED     == "#e34948"


def test_categorical_slot_order():
    assert figstyle.CATEGORICAL == (
        "#2a78d6",  # BLUE
        "#eb6834",  # ORANGE
        "#1baf7a",  # AQUA
        "#eda100",  # YELLOW
        "#e87ba4",  # MAGENTA
        "#008300",  # GREEN
        "#6250d6",  # VIOLET
        "#e34948",  # RED
    )
    assert len(figstyle.CATEGORICAL) == 8
    # all-pairs-safe prefix
    assert figstyle.SAFE_PREFIX == figstyle.CATEGORICAL[:3]


# ---------------------------------------------------------------------------
# Sequential blue ramp -- exact hexes, light -> dark
# ---------------------------------------------------------------------------
def test_seq_blues_ramp():
    assert figstyle.SEQ_BLUES == (
        "#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef",
        "#6da7ec", "#5598e7", "#3987e5", "#2a78d6",
        "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b",
    )
    assert len(figstyle.SEQ_BLUES) == 13
    # light -> dark: first is the lightest, last is the darkest; the
    # midpoint slot must equal BLUE so the ramp anchors on the palette.
    assert figstyle.SEQ_BLUES[0]  == "#cde2fb"
    assert figstyle.SEQ_BLUES[-1] == "#0d366b"
    assert "#2a78d6" in figstyle.SEQ_BLUES


# ---------------------------------------------------------------------------
# Diverging blue<->red with gray midpoint
# ---------------------------------------------------------------------------
def test_diverging_ramp():
    assert figstyle.DIVERGING_MID == "#f0efec"
    assert figstyle.DIVERGING[0] == "#2a78d6"   # blue extreme
    assert figstyle.DIVERGING[1] == "#f0efec"   # midpoint
    assert figstyle.DIVERGING[2] == "#e34948"   # red extreme


# ---------------------------------------------------------------------------
# Status hues (genuine status semantics only)
# ---------------------------------------------------------------------------
def test_status_hues():
    assert figstyle.STATUS_GOOD     == "#0ca30c"
    assert figstyle.STATUS_WARNING  == "#fab219"
    assert figstyle.STATUS_SERIOUS  == "#ec835a"
    assert figstyle.STATUS_CRITICAL == "#d03b3b"
    # status hues are NOT part of the categorical slots
    for s in (
        figstyle.STATUS_GOOD, figstyle.STATUS_WARNING,
        figstyle.STATUS_SERIOUS, figstyle.STATUS_CRITICAL,
    ):
        assert s not in figstyle.CATEGORICAL


# ---------------------------------------------------------------------------
# Ink tokens
# ---------------------------------------------------------------------------
def test_ink_tokens():
    assert figstyle.INK          == "#0b0b0b"
    assert figstyle.INK_SECONDARY == "#52514e"
    assert figstyle.INK_MUTED   == "#898781"
    assert figstyle.GRID        == "#e1e0d9"
    assert figstyle.BASELINE    == "#c3c2b7"
    assert figstyle.SURFACE     == "#ffffff"


# ---------------------------------------------------------------------------
# series_style contract
# ---------------------------------------------------------------------------
def test_series_style_slots():
    # slot order + fixed linestyles/markers
    assert figstyle.series_style(0) == ("#2a78d6", "-", "o")  # BLUE  solid o
    assert figstyle.series_style(1) == ("#eb6834", "--", "s")  # ORANGE dashed s
    assert figstyle.series_style(2) == ("#1baf7a", ":", "^")   # AQUA  dotted ^


def test_series_style_distinct_linestyle_and_marker():
    # the whole point: grayscale/CVD survival must not depend on hue alone
    styles = [figstyle.series_style(i) for i in range(3)]
    linestyles = {s[1] for s in styles}
    markers    = {s[2] for s in styles}
    assert len(linestyles) == 3
    assert len(markers)    == 3
    # and colors are also pairwise distinct
    colors = {s[0] for s in styles}
    assert len(colors) == 3


def test_series_style_out_of_range():
    with pytest.raises(IndexError):
        figstyle.series_style(-1)
    with pytest.raises(IndexError):
        figstyle.series_style(3)
    with pytest.raises(IndexError):
        figstyle.series_style(99)


def test_setup_is_idempotent_and_safe_headless():
    # calling setup twice must not raise; it just re-applies rcParams.
    figstyle.setup()
    figstyle.setup()
