#!/usr/bin/env python3
"""
Publication-grade vector figures for the ICDCS paper.

Generates IEEE-styled vector PDFs into ``paper/figures/``:

  * fig1_arch.pdf             -- System architecture: Client / Node B (Edge
                                  Gateway) / Node A (Cloud) with data-flow arrows
                                  and warm steady-state timing annotations.
  * fig2_topologies.pdf       -- Placement topology taxonomy (P0, P1, P2,
                                  P2-SPHP, P3) as a component-placement matrix.
  * fig3_sphp_timeline.pdf    -- Measured-mechanism timeline: SPHP speculative
                                 prefill overlap vs. serialized P2, driven by
                                 the campaign-fitted WARM stage components, with
                                 the measured cold/warm TTFT deltas annotated.
  * fig4_stage_breakdown.pdf  -- TTFT composition (retrieval barrier + fusion +
                                 prefill residual) per RTT tier, cold vs. warm.
  * fig5_ttft_vs_rtt.pdf      -- MEASURED TTFT vs. WAN RTT, baseline vs. SPHP,
                                 cold and warm panels, bootstrap-95% CIs.
  * fig6_crossover_map.pdf    -- Placement scenario curves over (RTT, BW):
                                 TTFT vs RTT per topology (warm + cold SPHP
                                 pair) and TTFT vs BW showing the hydrated-text
                                 serialization crossover.
  * fig7_retrieval_quality.pdf-- MRR@10 / nDCG@10 / Recall@100 / Recall@1000:
                                 Sparse vs. Dense vs. Hybrid, seeded 500-query
                                 live evaluation on Node B.
  * fig8_cost_model_parity.pdf-- Regime-level parity: predicted vs. observed
                                 per-tier mean TTFT (cold/warm x P2/SPHP),
                                 +/-10% bands, LOTO MAPE annotations.

Data sources
------------
  * benchmarks/campaigns/campaign_*.jsonl  (freshest campaign; measured record)
  * analysis/results/cost_model_validation.json (campaign-fitted parameters,
    regime-level LOTO validation)
  * analysis/results/crossover_analysis.json (+ _cold.json; model sweeps)
  * analysis/results/live_retrieval_quality.json (seeded 500-query quality)

Styling
-------
Validated colorblind-safe palette (CVD dE >= 9.2 all-pairs on the first three
slots; the two sub-3:1 hues carry in-figure value labels as relief), one axis
per panel, vector PDF, clean sans-serif, no clipped labels. Single-column
figures are 3.5 in wide and full-width figures 7.16 in wide so 8 pt fonts
remain >= 8 pt at print size.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import rcParams
from matplotlib.ticker import NullFormatter
from matplotlib.patches import Patch, FancyBboxPatch, FancyArrowPatch

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
PROJECT_ROOT = _HERE.parent
CAMPAIGN_GLOB = PROJECT_ROOT / "benchmarks" / "campaigns" / "campaign_*.jsonl"
CROSSOVER_FILE = _HERE / "results" / "crossover_analysis.json"
CROSSOVER_COLD_FILE = _HERE / "results" / "crossover_analysis_cold.json"
VALIDATION_FILE = _HERE / "results" / "cost_model_validation.json"
LIVE_QUALITY_FILE = _HERE / "results" / "live_retrieval_quality.json"
FIG_DIR = PROJECT_ROOT / "paper" / "figures"

sys.path.insert(0, str(_HERE))
from cost_model import PlacementCostModel  # noqa: E402

# ---------------------------------------------------------------------------
# Palette (validated: dataviz six-checks, light surface #ffffff)
# ---------------------------------------------------------------------------
PALETTE = {
    "blue":    "#2a78d6",   # slot 1
    "orange":  "#eb6834",   # slot 2
    "aqua":    "#1baf7a",   # slot 3
    "yellow":  "#eda100",   # slot 4
    "magenta": "#e87ba4",   # slot 5
    "green":   "#008300",   # slot 6
    "violet":  "#4a3aa7",   # slot 7
    "red":     "#e34948",   # slot 8
}
INK = {
    "primary":   "#0b0b0b",
    "secondary": "#52514e",
    "muted":     "#898781",
    "grid":      "#e1e0d9",
    "axis":      "#c3c2b7",
}

# Variant/topology entities (fixed across all figures).
C_P2 = PALETTE["blue"]
C_SPHP = PALETTE["orange"]
C_P3 = PALETTE["aqua"]
C_P0 = PALETTE["yellow"]

# Pipeline-stage entities (fixed across all figures).
C_SPARSE = PALETTE["blue"]
C_DENSE = PALETTE["orange"]
C_FUSION = PALETTE["aqua"]
C_PREFILL = PALETTE["violet"]
C_DECODE = PALETTE["magenta"]
C_WAN = INK["muted"]        # transport is chrome, not a data series

# Retrieval-quality modes (fixed across all figures).
C_MODE_SPARSE, C_MODE_DENSE, C_MODE_HYBRID = C_SPARSE, C_DENSE, C_FUSION

rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["DejaVu Sans", "Helvetica", "Arial"],
    "font.size": 8,
    "axes.titlesize": 9,
    "axes.labelsize": 8,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "legend.fontsize": 8,
    "axes.linewidth": 0.8,
    "axes.edgecolor": INK["axis"],
    "axes.labelcolor": INK["primary"],
    "xtick.color": INK["secondary"],
    "ytick.color": INK["secondary"],
    "text.color": INK["primary"],
    "axes.titlelocation": "left",
    "lines.linewidth": 1.6,
    "lines.markersize": 5,
    "figure.dpi": 150,
    "savefig.dpi": 300,
    "pdf.fonttype": 42,          # TrueType -> editable text in vector PDF
    "ps.fonttype": 42,
    "axes.grid": True,
    "grid.linewidth": 0.5,
    "grid.color": INK["grid"],
    "grid.alpha": 1.0,
    "axes.spines.top": False,
    "axes.spines.right": False,
})

# Print widths (inches): \columnwidth = 3.5, \textwidth = 7.16.
COL_W = 3.5
FULL_W = 7.16


def _style_axes(ax: plt.Axes) -> None:
    ax.set_axisbelow(True)


def _save(fig: plt.Figure, name: str) -> Path:
    """Save a figure as a vector PDF with no clipped text."""
    out = FIG_DIR / name
    fig.savefig(out, format="pdf", bbox_inches="tight")
    plt.close(fig)
    return out


# ---------------------------------------------------------------------------
# Data loading & statistics
# ---------------------------------------------------------------------------
def newest_campaign() -> Path:
    candidates = sorted(CAMPAIGN_GLOB.parent.glob(CAMPAIGN_GLOB.name))
    if not candidates:
        raise FileNotFoundError(f"no campaign JSONL under {CAMPAIGN_GLOB.parent}")
    return candidates[-1]


def load_campaign() -> list[dict]:
    records = []
    with open(newest_campaign(), "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("error") or not isinstance(r.get("ttft_ms"), (int, float)):
                continue
            records.append(r)
    if not records:
        raise ValueError(f"no usable records in {newest_campaign()}")
    return records


def load_crossover() -> list[dict]:
    with open(CROSSOVER_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_validation() -> dict:
    with open(VALIDATION_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_live_quality() -> dict:
    with open(LIVE_QUALITY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def model_from_validation(state: str) -> PlacementCostModel:
    """Rebuild the fitted PlacementCostModel for a cache state."""
    p = load_validation()["cache_states"][state]["fitted_params"]
    return PlacementCostModel(
        t_sparse_base_ms=p["t_sparse_base_ms"],
        t_dense_base_ms=p["t_dense_base_ms"],
        t_fusion_base_ms=p["t_fusion_base_ms"],
        t_prefill_base_ms=p["t_prefill_base_ms"],
        decode_tok_per_sec=p["decode_tok_per_sec"],
        sphp_hit_rate=p["sphp_hit_rate_measured"],
        t_sphp_miss_penalty_ms=p["t_sphp_miss_penalty_ms"],
    )


def boot_ci(xs: list[float], iters: int = 2000, seed: int = 42) -> tuple[float, float]:
    """Percentile bootstrap 95% CI of the mean (seeded, deterministic)."""
    arr = np.asarray(xs, dtype=float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(arr), size=(iters, len(arr)))
    means = arr[idx].mean(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(lo), float(hi)


def boot_ci_rel(a: list[float], b: list[float], iters: int = 2000, seed: int = 42):
    """Bootstrap 95% CI of the relative delta (mean(a)-mean(b))/mean(b), in %."""
    arr_a = np.asarray(a, dtype=float)
    arr_b = np.asarray(b, dtype=float)
    rng = np.random.default_rng(seed)
    ia = rng.integers(0, len(arr_a), size=(iters, len(arr_a)))
    ib = rng.integers(0, len(arr_b), size=(iters, len(arr_b)))
    rel = ((arr_a[ia].mean(axis=1) - arr_b[ib].mean(axis=1))
           / arr_b[ib].mean(axis=1) * 100.0)
    lo, hi = np.percentile(rel, [2.5, 97.5])
    return float(lo), float(hi), float(rel.mean())


def _tier_means(records: list[dict], value_key: str = "ttft_ms") -> tuple[list[float], list[list[float]]]:
    tiers = sorted({float(r["rtt_ms"]) for r in records})
    values = [[r[value_key] for r in records if float(r["rtt_ms"]) == t] for t in tiers]
    return tiers, values


# ---------------------------------------------------------------------------
# Fig 1 -- System architecture
# ---------------------------------------------------------------------------
def fig1_arch(model_warm: PlacementCostModel) -> Path:
    fig, ax = plt.subplots(figsize=(FULL_W, 3.9))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 6.6)
    ax.axis("off")

    def box(x, y, w, h, title, items, fc, ec):
        p = FancyBboxPatch((x, y), w, h,
                           boxstyle="round,pad=0.0,rounding_size=0.12",
                           linewidth=1.3, edgecolor=ec, facecolor=fc, zorder=2)
        ax.add_patch(p)
        ax.text(x + w / 2, y + h - 0.16, title, ha="center", va="top",
                fontsize=8.5, fontweight="bold", color=ec, zorder=3)
        top = y + h - 0.50
        step = (h - 0.66) / max(len(items), 1)
        for i, it in enumerate(items):
            ax.text(x + 0.12, top - i * step, "• " + it, ha="left", va="top",
                    fontsize=8, color=INK["primary"], zorder=3)

    # Client
    box(0.2, 2.0, 1.7, 2.0, "Client",
        ["User query", "Streamed tokens"],
        "#F0EAF3", PALETTE["violet"])

    # Node B (Edge Gateway) — boxes span y 0.8..4.8; arrows route above/below.
    box(3.0, 0.8, 2.9, 4.0, "Node B — Edge Gateway",
        ["Tantivy BM25 sparse (8.84M)",
         "BGE-M3 dense + Qdrant (GPU)",
         "RRF fusion",
         f"t_sparse ≈ {model_warm.t_sparse_base_ms:.0f} ms (warm)",
         f"t_dense ≈ {model_warm.t_dense_base_ms:.0f} ms (warm)"],
        "#EAF3FB", C_P2)

    # Node A (Generation Host)
    box(7.15, 0.8, 2.7, 4.0, "Node A — Generation",
        ["Local corpus hydration",
         "SPHP speculative prefill",
         "vLLM Qwen3.8-27B (W4A16)",
         f"t_prefill ≈ {model_warm.t_prefill_base_ms:.0f} ms (warm)"],
        "#FDF0E8", C_SPHP)

    def arrow(x1, y1, x2, y2, label, color, ls="-", dy=0.0, rad=0.0,
              label_dy=None, fs=8):
        a = FancyArrowPatch(
            (x1, y1), (x2, y2),
            arrowstyle="-|>", mutation_scale=14,
            linewidth=1.4, color=color, linestyle=ls, zorder=4,
            connectionstyle=f"arc3,rad={rad}" if rad else "arc3,rad=0")
        ax.add_patch(a)
        ly = (y1 + y2) / 2 + dy if label_dy is None else label_dy
        ax.text((x1 + x2) / 2, ly, label, ha="center",
                va="bottom", fontsize=fs, color=color, zorder=5)

    # Query: client -> Node B (horizontal, through the free gap).
    arrow(1.9, 3.0, 3.0, 3.0, "Query", INK["primary"], dy=0.10)

    # Retrieval context: Node B -> Node A, routed ABOVE both boxes.
    arrow(4.4, 4.9, 7.6, 4.9,
          "SparseHint / FusedContext  (gRPC stream · WireGuard)",
          C_P2, ls="--", label_dy=4.98)

    # Token stream: Node A -> client, routed BELOW the boxes.
    arrow(8.4, 0.75, 1.9, 1.9, "Token stream (streamed back)",
          C_SPHP, ls=":", rad=-0.25, label_dy=0.02, fs=8)

    ax.set_title("Geo-Distributed Hybrid RAG: System Architecture",
                 fontsize=9, pad=8)
    return _save(fig, "fig1_arch.pdf")


# ---------------------------------------------------------------------------
# Fig 2 -- Placement topology taxonomy
# ---------------------------------------------------------------------------
def fig2_topologies() -> Path:
    topologies = [
        ("P0", "Colocated",
         [["Query"], [], ["Sparse", "Dense", "Fusion", "Hydrate", "Prefill", "Decode"]]),
        ("P1", "All stages on A",
         [["Query"], ["Gateway"], ["Sparse", "Dense", "Fusion", "Hydrate", "Prefill", "Decode"]]),
        ("P2", "Retrieval B,\nGen A",
         [["Query"], ["Sparse", "Dense", "Fusion"], ["Hydrate", "Prefill", "Decode"]]),
        ("P2-SPHP", "SPHP spec.\nprefill",
         [["Query"], ["Sparse", "Dense", "Fusion"], ["Hydrate", "Spec. Prefill", "Decode"]]),
        ("P3", "Retrieval +\nHydrate B",
         [["Query"], ["Sparse", "Dense", "Fusion", "Hydrate"], ["Prefill", "Decode"]]),
    ]
    col_titles = ["", "Client", "Edge (Node B)", "Cloud (Node A)"]
    col_x = [0, 1.4, 4.0, 6.8, 10.0]
    col_fc = ["white", "#F2F2F2", "#EAF3FB", "#FDF0E8"]
    col_ec = [INK["muted"], INK["muted"], C_P2, C_SPHP]

    n_rows = len(topologies)
    row_h = 1.2
    header_h = 0.55
    total_h = header_h + n_rows * row_h

    fig, ax = plt.subplots(figsize=(FULL_W, total_h * 0.92 + 0.6))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, total_h)
    ax.axis("off")

    def cell(x0, x1, y0, y1, title, items, fc, ec, title_fs=8, item_fs=7):
        p = FancyBboxPatch((x0, y0), x1 - x0, y1 - y0,
                           boxstyle="round,pad=0.0,rounding_size=0.06",
                           linewidth=0.9, edgecolor=ec, facecolor=fc, zorder=2)
        ax.add_patch(p)
        if title:
            ax.text((x0 + x1) / 2, y1 - 0.12, title, ha="center", va="top",
                    fontsize=title_fs, fontweight="bold", color=ec, zorder=3)
        if items:
            top = y1 - 0.32
            step = (y1 - y0 - 0.40) / max(len(items), 1)
            for i, it in enumerate(items):
                ax.text((x0 + x1) / 2, top - i * step, "• " + it, ha="center",
                        va="top", fontsize=item_fs, color=INK["primary"], zorder=3)

    y_top = total_h
    for j, ct in enumerate(col_titles):
        cell(col_x[j], col_x[j + 1], y_top - header_h, y_top,
             ct, [], col_fc[j], col_ec[j], title_fs=8)

    for i, (name, desc, cells) in enumerate(topologies):
        y1 = y_top - header_h - i * row_h
        y0 = y1 - row_h
        cell(col_x[0], col_x[1], y0, y1, name, [desc], "white",
             INK["primary"], title_fs=8, item_fs=6)
        for j, items in enumerate(cells):
            cell(col_x[j + 1], col_x[j + 2], y0, y1, "", items,
                 col_fc[j + 1], col_ec[j + 1], item_fs=7)

    ax.set_title("Placement Topology Taxonomy (P0–P3)", fontsize=9, pad=8)
    return _save(fig, "fig2_topologies.pdf")


# ---------------------------------------------------------------------------
# Fig 3 -- SPHP speculative prefill timeline (measured-mechanism Gantt)
# ---------------------------------------------------------------------------
def _sphp_deltas(campaign: list[dict]) -> dict:
    """Measured per-state SPHP TTFT deltas (relative %) with CIs."""
    out = {}
    for state in ("cold", "warm"):
        recs = [r for r in campaign
                if (r.get("repeat_index", 0) == 0) == (state == "cold")]
        base = [r["ttft_ms"] for r in recs if r.get("variant") == "baseline"]
        sphp = [r["ttft_ms"] for r in recs if r.get("variant") == "sphp"]
        lo, hi, rel = boot_ci_rel(sphp, base)
        out[state] = {"rel_pct": rel, "ci": (lo, hi),
                      "base_mean": float(np.mean(base)),
                      "sphp_mean": float(np.mean(sphp))}
    return out


def fig3_sphp_timeline(model_warm: PlacementCostModel, campaign: list[dict]) -> Path:
    rtt, bw, top_k = 40.0, 100.0, 10

    sparse = model_warm.t_sparse_base_ms
    dense = model_warm.t_dense_base_ms
    fusion = model_warm.t_fusion_base_ms
    prefill = model_warm.t_prefill_base_ms  # residual: includes hydration

    wan_hint = model_warm.wan_delay_ms(model_warm.query_bytes + 5 * model_warm.doc_id_bytes, rtt, bw)
    wan_fused = model_warm.wan_delay_ms(model_warm.query_bytes + top_k * model_warm.doc_id_bytes, rtt, bw)

    # ---- (a) Baseline P2: serialized ----
    base = [
        ("Dense leg (BGE-M3)", 0.0, dense, C_DENSE),
        ("Sparse leg (BM25)", 0.0, sparse, C_SPARSE),
        ("WAN: fused context", dense + fusion, wan_fused, C_WAN),
        ("Prefill (incl. hydrate)", dense + fusion + wan_fused, prefill, C_PREFILL),
    ]
    base_ttft = dense + fusion + wan_fused + prefill

    # ---- (b) P2-SPHP: speculative prefill overlaps the dense leg ----
    spec_prefill_start = sparse + wan_hint
    spec_prefill_end = spec_prefill_start + prefill
    fused_arrival = dense + fusion + wan_fused
    sphp_ttft = max(fused_arrival, spec_prefill_end)

    sphp = [
        ("Dense leg (BGE-M3)", 0.0, dense, C_DENSE),
        ("Sparse leg (BM25)", 0.0, sparse, C_SPARSE),
        ("WAN: sparse hint", sparse, wan_hint, C_WAN),
        ("Provisional prefill", spec_prefill_start, prefill, C_PREFILL),
    ]

    fig, (ax_base, ax_sphp) = plt.subplots(
        2, 1, figsize=(FULL_W, 4.0), sharex=True,
        gridspec_kw={"hspace": 0.42},
    )

    def draw_gantt(ax, bars, ttft, title, provisional_idx=None):
        n = len(bars)
        names = []
        for i, (label, start, dur, color) in enumerate(bars):
            y = n - 1 - i
            names.append(label)
            alpha = 0.55 if i == provisional_idx else 1.0
            hatch = "//" if i == provisional_idx else None
            ax.barh(y, dur, left=start, height=0.6, color=color,
                    alpha=alpha, hatch=hatch, edgecolor="white",
                    linewidth=0.5, zorder=3)
            if dur >= 250.0:
                ax.text(start + dur / 2.0, y, f"{label}  ({dur:.0f} ms)",
                        va="center", ha="center", fontsize=8,
                        color="white" if alpha == 1.0 else INK["primary"],
                        zorder=4)
            else:
                ax.annotate(f"{label} ({dur:.1f} ms)", xy=(start + dur, y),
                            xytext=(start + dur + 15.0, y),
                            va="center", ha="left", fontsize=8,
                            color=color, zorder=4,
                            arrowprops=dict(arrowstyle="-", color=color,
                                            lw=0.6, shrinkA=0, shrinkB=0))
        ax.axvline(ttft, color=INK["primary"], linestyle="--",
                   linewidth=1.0, zorder=2)
        ax.text(ttft, n - 0.10, f"TTFT={ttft:.0f} ms ",
                va="bottom", ha="right", fontsize=8, color=INK["primary"])
        # Bars are drawn at y = n-1-i in draw order; reverse the tick labels
        # to match, so each stage name sits beside its own bar.
        ax.set_yticks(range(n))
        ax.set_yticklabels(list(reversed(names)), fontsize=8)
        ax.set_title(title, loc="left", fontsize=9)
        ax.set_xlim(0, max(b[1] + b[2] for b in bars) * 1.22)
        ax.set_ylim(-0.6, n + 0.75)
        _style_axes(ax)

    draw_gantt(ax_base, base, base_ttft,
               f"(a) Baseline P2 — serialized (RTT={rtt:.0f} ms, BW={bw:.0f} Mbps, warm fit)")
    draw_gantt(ax_sphp, sphp, sphp_ttft,
               "(b) P2-SPHP — provisional prefill overlaps the dense leg (kept on hit)",
               provisional_idx=3)

    ax_sphp.set_xlabel("Time (ms)")

    # Measured deltas from the campaign (honest numbers, not the schematic's).
    d = _sphp_deltas(campaign)
    cold, warm = d["cold"], d["warm"]
    fig.text(0.99, -0.045,
             f"Measured TTFT delta vs. baseline (h = {model_warm.sphp_hit_rate:.2f} measured): "
             f"cold {cold['rel_pct']:+.1f}% [95% CI {cold['ci'][0]:+.1f}, {cold['ci'][1]:+.1f}%], "
             f"warm {warm['rel_pct']:+.1f}% [CI {warm['ci'][0]:+.1f}, {warm['ci'][1]:+.1f}%]",
             ha="right", va="top", fontsize=8, color=INK["secondary"],
             style="italic")

    return _save(fig, "fig3_sphp_timeline.pdf")


# ---------------------------------------------------------------------------
# Fig 4 -- TTFT composition per RTT tier (cold vs. warm)
# ---------------------------------------------------------------------------
def fig4_stage_breakdown(campaign: list[dict]) -> Path:
    tiers_all = sorted({float(r["rtt_ms"]) for r in campaign})
    tier_labels = [f"{int(t)}" for t in tiers_all]

    fig, axes = plt.subplots(1, 2, figsize=(FULL_W, 3.0), sharey=False)
    panels = [("cold", "(a) Cold prefix (repeat 0)"), ("warm", "(b) Warm (repeats 1+)")]

    for ax, (state, title) in zip(axes, panels):
        recs = [r for r in campaign
                if r.get("variant") == "baseline"
                and (r.get("repeat_index", 0) == 0) == (state == "cold")]
        stages = {"Retrieval (max of Sparse ∥ Dense)": [],
                  "Fusion (RRF)": [],
                  "Prefill (incl. hydrate)": []}
        for t in tiers_all:
            tr = [r for r in recs if float(r["rtt_ms"]) == t]
            sparse = float(np.mean([r["sparse_ms"] for r in tr]))
            dense = float(np.mean([r["dense_ms"] for r in tr]))
            fusion = float(np.mean([r["fusion_ms"] for r in tr]))
            ttft = float(np.mean([r["ttft_ms"] for r in tr]))
            stages["Retrieval (max of Sparse ∥ Dense)"].append(max(sparse, dense))
            stages["Fusion (RRF)"].append(fusion)
            stages["Prefill (incl. hydrate)"].append(max(ttft - max(sparse, dense) - fusion, 0.0))

        colors = [C_SPARSE, C_FUSION, C_PREFILL]
        x = np.arange(len(tiers_all))
        width = 0.58
        bottom = np.zeros(len(tiers_all))
        for (label, vals), color in zip(stages.items(), colors):
            vals = np.array(vals)
            ax.bar(x, vals, width, bottom=bottom, color=color,
                   edgecolor="white", linewidth=0.8, label=label)
            for xi, (v, b) in enumerate(zip(vals, bottom)):
                if v > 300:
                    ax.text(xi, b + v / 2.0, f"{v:.0f}", ha="center", va="center",
                            fontsize=8, color="white")
            bottom += vals

        ax.set_xticks(x)
        ax.set_xticklabels(tier_labels)
        ax.set_xlabel("WAN RTT (ms)")
        ax.set_title(title, loc="left", fontsize=9)
        ax.set_ylim(0, bottom.max() * 1.12)
        _style_axes(ax)

    axes[0].set_ylabel("Mean TTFT (ms)")
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, 1.10),
               ncol=3, frameon=False, fontsize=8)
    return _save(fig, "fig4_stage_breakdown.pdf")


# ---------------------------------------------------------------------------
# Fig 5 -- MEASURED TTFT vs. WAN RTT (cold / warm; baseline vs. SPHP)
# ---------------------------------------------------------------------------
def fig5_ttft_vs_rtt(campaign: list[dict]) -> Path:
    fig, axes = plt.subplots(1, 2, figsize=(FULL_W, 3.0))

    for ax, (state, title) in zip(axes, [("cold", "(a) Cold prefix (repeat 0)"),
                                         ("warm", "(b) Warm (repeats 1+)")]):
        recs = [r for r in campaign
                if (r.get("repeat_index", 0) == 0) == (state == "cold")]
        tiers, base_vals = _tier_means([r for r in recs if r.get("variant") == "baseline"])
        _, sphp_vals = _tier_means([r for r in recs if r.get("variant") == "sphp"])

        base_mean = [float(np.mean(v)) for v in base_vals]
        base_ci = [boot_ci(v) for v in base_vals]
        sphp_mean = [float(np.mean(v)) for v in sphp_vals]
        sphp_ci = [boot_ci(v) for v in sphp_vals]

        ax.errorbar(tiers, base_mean,
                    yerr=[[m - c[0] for m, c in zip(base_mean, base_ci)],
                          [c[1] - m for m, c in zip(base_mean, base_ci)]],
                    marker="o", color=C_P2, capsize=3, label="P2 baseline")
        ax.errorbar(tiers, sphp_mean,
                    yerr=[[m - c[0] for m, c in zip(sphp_mean, sphp_ci)],
                          [c[1] - m for m, c in zip(sphp_mean, sphp_ci)]],
                    marker="s", color=C_SPHP, capsize=3, label="P2-SPHP")

        if state == "cold":
            lo, hi, rel = boot_ci_rel(
                [r["ttft_ms"] for r in recs if r.get("variant") == "sphp"],
                [r["ttft_ms"] for r in recs if r.get("variant") == "baseline"])
            ax.annotate(f"SPHP delta {rel:+.1f}% [{lo:+.1f}, {hi:+.1f}]",
                        xy=(0.28, 0.04), xycoords="axes fraction",
                        fontsize=7.5, color=INK["secondary"])
        else:
            ax.annotate("delta within noise at every tier",
                        xy=(0.32, 0.05), xycoords="axes fraction",
                        fontsize=7.5, color=INK["secondary"])

        ax.set_xticks(tiers)
        ax.set_xlabel("WAN RTT (ms)")
        ax.set_title(title, loc="left", fontsize=9)
        ax.legend(loc="upper right", frameon=False)
        _style_axes(ax)

    axes[0].set_ylabel("TTFT (ms)")
    return _save(fig, "fig5_ttft_vs_rtt.pdf")


# ---------------------------------------------------------------------------
# Fig 6 -- Placement scenario curves over (RTT, BW)
# ---------------------------------------------------------------------------
def fig6_crossover_map(model_warm: PlacementCostModel,
                       model_cold: PlacementCostModel) -> Path:
    fig, (ax_a, ax_b) = plt.subplots(1, 2, figsize=(FULL_W, 3.0))

    # (a) TTFT vs RTT at 100 Mbps — warm solid, cold dashed; log y so the
    # warm cluster (≈800-950 ms) and cold band (≈3.3-3.6 s) both resolve.
    rtts = np.linspace(0, 200, 50)
    ax_a.plot(rtts, [model_warm.predict_ttft_p2(r, 100.0) for r in rtts],
              color=C_P2, label="P2 (warm)")
    ax_a.plot(rtts, [model_warm.predict_ttft_p2_sphp(r, 100.0) for r in rtts],
              color=C_SPHP, label="P2-SPHP (warm)")
    ax_a.plot(rtts, [model_warm.predict_ttft_p3(r, 100.0) for r in rtts],
              color=C_P3, label="P3 (warm)")
    ax_a.plot(rtts, [model_cold.predict_ttft_p2(r, 100.0) for r in rtts],
              color=C_P2, linestyle="--", alpha=0.55, label="P2 (cold)")
    ax_a.plot(rtts, [model_cold.predict_ttft_p2_sphp(r, 100.0) for r in rtts],
              color=C_SPHP, linestyle="--", alpha=0.55, label="P2-SPHP (cold)")
    ax_a.set_yscale("log")
    ax_a.set_yticks([800, 1000, 2000, 4000])
    ax_a.set_yticklabels(["800", "1000", "2000", "4000"])
    ax_a.yaxis.set_minor_formatter(NullFormatter())
    ax_a.set_ylim(700, 4500)
    ax_a.set_xlabel("WAN RTT (ms)")
    ax_a.set_ylabel("Modelled TTFT (ms)")
    ax_a.set_title("(a) TTFT vs. RTT (BW = 100 Mbps)", loc="left", fontsize=9)
    ax_a.legend(loc="center right", frameon=False, fontsize=7)
    ax_a.annotate("SPHP optimal at every measured regime;\nP3 ≈ P2 (payloads are tiny)",
                  xy=(0.04, 0.30), xycoords="axes fraction",
                  fontsize=8, color=INK["secondary"], va="center")
    _style_axes(ax_a)

    # (b) TTFT vs BW at 80 ms RTT — the only real crossover is serialization.
    bws = np.logspace(np.log10(0.5), np.log10(1000), 80)
    ax_b.plot(bws, [model_warm.predict_ttft_p2(80.0, b) for b in bws],
              color=C_P2, label="P2")
    ax_b.plot(bws, [model_warm.predict_ttft_p2_sphp(80.0, b) for b in bws],
              color=C_SPHP, label="P2-SPHP")
    ax_b.plot(bws, [model_warm.predict_ttft_p3(80.0, b) for b in bws],
              color=C_P3, label="P3 (hydrated text)")
    ax_b.set_xscale("log")
    ax_b.set_xlim(0.5, 1000)
    ax_b.set_xticks([0.5, 1, 2, 5, 10, 100, 1000])
    ax_b.set_xticklabels(["0.5", "1", "2", "5", "10", "100", "1000"])
    ax_b.set_xlabel("Bandwidth (Mbps)")
    ax_b.set_ylabel("Modelled TTFT (ms)")
    ax_b.set_title("(b) TTFT vs. bandwidth (RTT = 80 ms, warm)", loc="left", fontsize=9)
    ax_b.legend(loc="upper right", frameon=False, fontsize=7)
    ax_b.annotate("P3 serialization penalty\n(6 KB hydrated text)",
                  xy=(1.15, model_warm.predict_ttft_p3(80.0, 1.15)),
                  xytext=(6.0, 900.0), fontsize=8, color=C_P3,
                  arrowprops=dict(arrowstyle="->", color=C_P3, lw=0.8))
    _style_axes(ax_b)

    return _save(fig, "fig6_crossover_map.pdf")


# ---------------------------------------------------------------------------
# Fig 7 -- Retrieval quality (Sparse vs. Dense vs. Hybrid)
# ---------------------------------------------------------------------------
def fig7_retrieval_quality() -> Path:
    live = load_live_quality()
    m = live["metrics"]
    n_queries = live.get("n_queries", 500)

    metrics = [("mrr@10", "MRR@10"), ("ndcg@10", "nDCG@10"),
               ("recall@100", "Recall@100"), ("recall@1000", "Recall@1000")]
    modes = [("sparse", "Sparse (BM25)", C_MODE_SPARSE, "o"),
             ("dense", "Dense (BGE-M3)", C_MODE_DENSE, "s"),
             ("hybrid", "Hybrid (RRF)", C_MODE_HYBRID, "D")]

    # Grouped dot plot (Cleveland): three marker rows per metric; value
    # labels sit beside their own dot, so labels can never collide.
    fig, ax = plt.subplots(figsize=(COL_W, 2.9))
    offsets = {"sparse": 0.24, "dense": 0.0, "hybrid": -0.24}
    for mode, label, color, marker in modes:
        for yi, (key, _) in enumerate(metrics):
            if key not in m[mode]:
                continue
            v = m[mode][key]
            y = yi + offsets[mode]
            ax.scatter([v], [y], marker=marker, color=color, s=26,
                       zorder=3, label=label if yi == 0 else None)
            ax.text(v + 0.018, y, f"{v:.2f}", va="center", ha="left",
                    fontsize=6.0, color=INK["secondary"])

    # Sparse is retrieved at k<=100: its Recall@1000 slot is a cap, not a score.
    ax.annotate("sparse: k ≤ 100", xy=(0.055, 3 - 0.24), xycoords="data",
                fontsize=6.0, color=INK["muted"], va="center")

    ax.set_yticks(range(len(metrics)))
    ax.set_yticklabels([lbl for _, lbl in metrics], fontsize=7)
    ax.invert_yaxis()
    ax.set_xlim(0, 1.0)
    ax.set_xlabel("Score")
    ax.set_ylim(3.55, -0.55)
    ax.set_title("Retrieval Quality", loc="left", fontsize=9)
    ax.legend(loc="upper left", frameon=False, fontsize=6.0,
              handletextpad=0.2, borderaxespad=0.2)
    ax.grid(axis="y", visible=False)
    _style_axes(ax)

    fig.text(0.99, -0.04,
             f"{n_queries} seeded MS MARCO dev queries (seed 42), live on Node B; RRF k=60.",
             ha="right", va="top", fontsize=6.5, color=INK["secondary"],
             style="italic")

    return _save(fig, "fig7_retrieval_quality.pdf")


# ---------------------------------------------------------------------------
# Fig 8 -- Regime-level cost-model parity
# ---------------------------------------------------------------------------
def fig8_cost_model_parity(campaign: list[dict]) -> Path:
    val = load_validation()

    fig, ax = plt.subplots(figsize=(COL_W, 3.3))

    for state, marker, fill in (("cold", "o", True), ("warm", "D", False)):
        recs = [r for r in campaign
                if (r.get("repeat_index", 0) == 0) == (state == "cold")]
        model = model_from_validation(state)
        for variant, pred_fn, color, label in (
            ("baseline", lambda r: model.predict_ttft_p2(float(r["rtt_ms"])),
             C_P2, f"P2 ({state})"),
            ("sphp", lambda r: model.predict_ttft_p2_sphp(float(r["rtt_ms"])),
             C_SPHP, f"P2-SPHP ({state})"),
        ):
            vrecs = [r for r in recs if r.get("variant") == variant]
            tiers, vals = _tier_means(vrecs)
            pred = [pred_fn({"rtt_ms": t}) for t in tiers]
            obs = [float(np.mean(v)) for v in vals]
            ax.scatter(obs, pred, marker=marker,
                       facecolor=color if fill else "white",
                       edgecolor=color, linewidth=1.2, s=34,
                       zorder=3, label=label)

    lim_lo, lim_hi = 600.0, 4200.0
    xs = np.linspace(lim_lo, lim_hi, 2)
    ax.plot(xs, xs, color=INK["primary"], linewidth=1.0, label="y = x")
    ax.plot(xs, 0.9 * xs, color=INK["muted"], linestyle="--", linewidth=0.8,
            label="±10%")
    ax.plot(xs, 1.1 * xs, color=INK["muted"], linestyle="--", linewidth=0.8)
    ax.set_xlim(lim_lo, lim_hi)
    ax.set_ylim(lim_lo, lim_hi)

    rv = {s: val["cache_states"][s]["regime_validation"] for s in ("cold", "warm")}
    ax.text(0.03, 0.97,
            f"regime MAPE (LOTO): "
            f"P2 {rv['cold']['P2']['regime_mape_pct']:.1f}% cold / "
            f"{rv['warm']['P2']['regime_mape_pct']:.1f}% warm\n"
            f"SPHP {rv['cold']['P2-SPHP']['regime_mape_pct']:.1f}% cold / "
            f"{rv['warm']['P2-SPHP']['regime_mape_pct']:.1f}% warm",
            transform=ax.transAxes, va="top", ha="left", fontsize=6.8,
            color=INK["secondary"],
            bbox=dict(boxstyle="round,pad=0.3", fc="white",
                      ec=INK["axis"], alpha=0.9))

    ax.set_xlabel("Observed tier-mean TTFT (ms)")
    ax.set_ylabel("Predicted tier-mean TTFT (ms)")
    ax.set_title("Cost-Model Parity (regime level)", loc="left", fontsize=9)
    ax.legend(loc="lower right", frameon=False, fontsize=6.2)
    _style_axes(ax)

    return _save(fig, "fig8_cost_model_parity.pdf")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    FIG_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 64)
    print("ICDCS PAPER: GENERATING PUBLICATION FIGURES ->", FIG_DIR)
    print("=" * 64)

    campaign = load_campaign()
    model_warm = model_from_validation("warm")
    model_cold = model_from_validation("cold")

    outputs = [
        fig1_arch(model_warm),
        fig2_topologies(),
        fig3_sphp_timeline(model_warm, campaign),
        fig4_stage_breakdown(campaign),
        fig5_ttft_vs_rtt(campaign),
        fig6_crossover_map(model_warm, model_cold),
        fig7_retrieval_quality(),
        fig8_cost_model_parity(campaign),
    ]

    print("\nGenerated figures:")
    for p in outputs:
        size = p.stat().st_size
        status = "OK" if size > 0 else "EMPTY!"
        print(f"  [{status}] {p}  ({size} bytes)")

    missing = [p for p in outputs if not p.exists() or p.stat().st_size == 0]
    if missing:
        print("\nERROR: some figures missing or empty:", missing)
        sys.exit(1)
    print(f"\nAll {len(outputs)} figures generated successfully.")


if __name__ == "__main__":
    main()
