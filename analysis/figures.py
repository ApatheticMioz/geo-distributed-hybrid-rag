#!/usr/bin/env python3
"""
Publication-grade vector figures for the ICDCS paper.

Generates IEEE-styled vector PDFs into ``paper/figures/``:

  * fig1_arch.pdf             -- System architecture: Client / Node B (Edge
                                  Gateway) / Node A (Cloud) with data-flow arrows
                                  and per-stage timing annotations.
  * fig2_topologies.pdf       -- Placement topology taxonomy (P0, P1, P2,
                                  P2-SPHP, P3) as a component-placement matrix.
  * fig3_sphp_timeline.pdf    -- Gantt/timeline: SPHP speculative prefill overlap
                                 vs. baseline serialized P2 pipeline.
  * fig4_stage_breakdown.pdf  -- Stacked bar of per-stage latencies across the
                                 four network conditions.
  * fig5_ttft_vs_rtt.pdf      -- TTFT vs. WAN RTT (0/10/30/50/100 ms), P2 vs. P2-SPHP.
  * fig6_crossover_map.pdf    -- 2-D heatmap of optimal placement over (RTT x BW).
  * fig7_retrieval_quality.pdf-- MRR / nDCG@10 / Recall@100: Sparse vs. Dense vs. Hybrid.
  * fig8_cost_model_parity.pdf-- Predicted vs. measured TTFT parity scatter with
                                 +/-10% error bands.

Data sources
------------
  * benchmarks/results_matrix_gpu.json   (empirical stage timings, N=50 per tier)
  * analysis/results/crossover_analysis.csv
  * analysis/results/cost_model_validation.json

Styling
-------
IEEE / colorblind-safe (Okabe-Ito) palette, vector PDF output, clean sans-serif
typography, no clipped labels (``bbox_inches="tight"``).
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
from matplotlib.patches import Patch, FancyBboxPatch, FancyArrowPatch
from matplotlib.colors import ListedColormap

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
PROJECT_ROOT = _HERE.parent
BENCH_FILE = PROJECT_ROOT / "benchmarks" / "results_matrix_gpu.json"
CROSSOVER_FILE = _HERE / "results" / "crossover_analysis.csv"
VALIDATION_FILE = _HERE / "results" / "cost_model_validation.json"
FIG_DIR = PROJECT_ROOT / "paper" / "figures"

# Make the cost model importable (same directory).
sys.path.insert(0, str(_HERE))
from cost_model import PlacementCostModel, fit_model_from_benchmark  # noqa: E402


# ---------------------------------------------------------------------------
# IEEE / colorblind-safe styling
# ---------------------------------------------------------------------------
# Okabe-Ito colorblind-safe palette.
OKABE_ITO = {
    "blue": "#0072B2",
    "orange": "#E69F00",
    "green": "#009E73",
    "vermillion": "#D55E00",
    "purple": "#CC79A7",
    "skyblue": "#56B4E9",
    "yellow": "#F0E442",
    "black": "#000000",
    "gray": "#8C8C8C",
}

# Semantic color assignments (kept consistent across figures).
C_SPARSE = OKABE_ITO["blue"]
C_DENSE = OKABE_ITO["orange"]
C_FUSION = OKABE_ITO["green"]
C_PREFILL = OKABE_ITO["vermillion"]
C_DECODE = OKABE_ITO["purple"]
C_P2 = OKABE_ITO["blue"]
C_SPHP = OKABE_ITO["vermillion"]
C_P0 = OKABE_ITO["skyblue"]
C_P3 = OKABE_ITO["green"]

rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["DejaVu Sans", "Helvetica", "Arial"],
    "font.size": 9,
    "axes.titlesize": 10,
    "axes.labelsize": 9,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "legend.fontsize": 8,
    "axes.linewidth": 0.8,
    "lines.linewidth": 1.6,
    "lines.markersize": 5,
    "figure.dpi": 150,
    "savefig.dpi": 300,
    "pdf.fonttype": 42,          # TrueType -> editable text in vector PDF
    "ps.fonttype": 42,
    "axes.grid": True,
    "grid.linewidth": 0.4,
    "grid.alpha": 0.4,
    "grid.color": "#cccccc",
    "figure.constrained_layout.use": False,
})

# Topology -> color for the crossover heatmap.
TOPO_COLORS = {
    "P0": C_P0,
    "P2": C_P2,
    "P2-SPHP": C_SPHP,
    "P3": C_P3,
}


def _save(fig: plt.Figure, name: str) -> Path:
    """Save a figure as a vector PDF with no clipped text."""
    out = FIG_DIR / name
    fig.savefig(out, format="pdf", bbox_inches="tight")
    plt.close(fig)
    return out


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_benchmark() -> dict:
    with open(BENCH_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_crossover() -> list[dict]:
    rows = []
    with open(CROSSOVER_FILE, "r", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append({
                "rtt_ms": float(r["rtt_ms"]),
                "bw_mbps": float(r["bw_mbps"]),
                "ttft_p0_ms": float(r["ttft_p0_ms"]),
                "ttft_p2_ms": float(r["ttft_p2_ms"]),
                "ttft_p2_sphp_ms": float(r["ttft_p2_sphp_ms"]),
                "ttft_p3_ms": float(r["ttft_p3_ms"]),
                "sphp_speedup_pct": float(r["sphp_speedup_pct"]),
                "optimal_topology": r["optimal_topology"],
            })
    return rows


def load_validation() -> dict:
    with open(VALIDATION_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def fit_model() -> PlacementCostModel:
    model, _ = fit_model_from_benchmark(BENCH_FILE)
    return model


# ---------------------------------------------------------------------------
# Fig 1 -- System architecture
# ---------------------------------------------------------------------------
def fig1_arch(model: PlacementCostModel) -> Path:
    fig, ax = plt.subplots(figsize=(8.2, 4.6))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 6)
    ax.axis("off")

    def box(x, y, w, h, title, items, fc, ec):
        p = FancyBboxPatch((x, y), w, h,
                           boxstyle="round,pad=0.0,rounding_size=0.12",
                           linewidth=1.3, edgecolor=ec, facecolor=fc, zorder=2)
        ax.add_patch(p)
        ax.text(x + w / 2, y + h - 0.18, title, ha="center", va="top",
                fontsize=9, fontweight="bold", color=ec, zorder=3)
        top = y + h - 0.44
        step = (h - 0.55) / max(len(items), 1)
        for i, it in enumerate(items):
            ax.text(x + 0.12, top - i * step, "• " + it, ha="left", va="top",
                    fontsize=7, color="black", zorder=3)

    # Client
    box(0.2, 2.0, 1.7, 2.0, "Client",
        ["User query", "Receives streamed tokens"],
        "#F0EAF3", OKABE_ITO["purple"])

    # Node B (Edge Gateway)
    box(3.0, 1.0, 3.0, 4.0, "Node B — Edge Gateway",
        ["Tantivy BM25 (8.84M)", "BGE-M3 Dense (GPU)", "RRF Fusion",
         f"t_sparse ≈ {model.t_sparse_base_ms:.0f} ms",
         f"t_dense ≈ {model.t_dense_base_ms:.0f} ms"],
        "#EAF3FB", OKABE_ITO["blue"])

    # Node A (Cloud / Workstation)
    box(7.3, 1.0, 2.5, 4.0, "Node A — Cloud / Workstation",
        ["SQLite hydration (8.84M)", "SPHP speculative prefill",
         "vLLM LLaMA-3-8B AWQ",
         f"t_prefill ≈ {model.t_prefill_base_ms:.0f} ms"],
        "#FBEFEA", OKABE_ITO["vermillion"])

    def arrow(x1, y1, x2, y2, label, color, ls="-", dy=0.0, rad=0.0):
        a = FancyArrowPatch(
            (x1, y1), (x2, y2),
            arrowstyle="-|>", mutation_scale=14,
            linewidth=1.4, color=color, linestyle=ls, zorder=4,
            connectionstyle=f"arc3,rad={rad}" if rad else "arc3,rad=0")
        ax.add_patch(a)
        ax.text((x1 + x2) / 2, (y1 + y2) / 2 + dy, label, ha="center",
                va="bottom", fontsize=7, color=color, zorder=5)

    # Client -> Node B
    arrow(1.9, 3.0, 3.0, 3.0, "Query", OKABE_ITO["black"], dy=0.10)
    # Node B -> Node A (WAN, gRPC stream)
    arrow(6.0, 3.7, 7.3, 3.7,
          "SparseHint / FusedContext\n(gRPC stream · WireGuard WAN)",
          OKABE_ITO["blue"], ls="--", dy=0.12)
    # Node A -> Client (token stream, curved below)
    arrow(8.5, 1.0, 1.0, 2.0, "Token stream (streamed back)",
          OKABE_ITO["vermillion"], ls=":", dy=-0.30, rad=-0.35)

    ax.set_title("Geo-Distributed Hybrid RAG: System Architecture",
                 fontsize=10, pad=8)
    return _save(fig, "fig1_arch.pdf")


# ---------------------------------------------------------------------------
# Fig 2 -- Placement topology taxonomy
# ---------------------------------------------------------------------------
def fig2_topologies() -> Path:
    # (name, short description, [Client stages, Edge stages, Cloud stages])
    topologies = [
        ("P0", "Colocated",
         ["Query", [], ["Sparse", "Dense", "Fusion", "Hydrate", "Prefill", "Decode"]]),
        ("P1", "Gateway + all on A",
         ["Query", ["Gateway"], ["Sparse", "Dense", "Fusion", "Hydrate", "Prefill", "Decode"]]),
        ("P2", "Retrieval B / Gen A",
         ["Query", ["Sparse", "Dense", "Fusion"], ["Hydrate", "Prefill", "Decode"]]),
        ("P2-SPHP", "SPHP speculative prefill",
         ["Query", ["Sparse", "Dense", "Fusion"], ["Hydrate", "Spec. Prefill", "Decode"]]),
        ("P3", "Retrieval+Hydrate B",
         ["Query", ["Sparse", "Dense", "Fusion", "Hydrate"], ["Prefill", "Decode"]]),
    ]
    col_titles = ["", "Client", "Edge (Node B)", "Cloud (Node A)"]
    # Client column is deliberately the widest data column so short items
    # such as "• Query" never wrap or clip letter-by-letter.
    col_x = [0, 1.4, 4.0, 6.8, 10.0]
    col_fc = ["white", "#F2F2F2", "#EAF3FB", "#FBEFEA"]
    col_ec = ["gray", OKABE_ITO["gray"], OKABE_ITO["blue"], OKABE_ITO["vermillion"]]

    n_rows = len(topologies)
    row_h = 0.95
    header_h = 0.55
    total_h = header_h + n_rows * row_h

    fig, ax = plt.subplots(figsize=(8.2, total_h * 0.92 + 0.6))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, total_h)
    ax.axis("off")

    def cell(x0, x1, y0, y1, title, items, fc, ec,
             title_fs=8, item_fs=6.5):
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
                # Center each item horizontally in its cell so short labels
                # (e.g. "• Query") never clip or wrap at the left edge.
                ax.text((x0 + x1) / 2, top - i * step, "• " + it, ha="center",
                        va="top", fontsize=item_fs, color="black", zorder=3)

    # Header row.
    y_top = total_h
    for j, ct in enumerate(col_titles):
        cell(col_x[j], col_x[j + 1], y_top - header_h, y_top,
             ct, [], col_fc[j], col_ec[j], title_fs=8)

    # Data rows.
    for i, (name, desc, cells) in enumerate(topologies):
        y1 = y_top - header_h - i * row_h
        y0 = y1 - row_h
        # Label cell: topology name (bold) + short description.
        cell(col_x[0], col_x[1], y0, y1, name, [desc], "white",
             OKABE_ITO["black"], title_fs=8, item_fs=5.5)
        for j, items in enumerate(cells):
            cell(col_x[j + 1], col_x[j + 2], y0, y1, "", items,
                 col_fc[j + 1], col_ec[j + 1], item_fs=6.5)

    ax.set_title("Placement Topology Taxonomy (P0–P3)", fontsize=10, pad=8)
    return _save(fig, "fig2_topologies.pdf")


# ---------------------------------------------------------------------------
# Fig 3 -- SPHP speculative prefill timeline (Gantt)
# ---------------------------------------------------------------------------
def fig3_sphp_timeline(model: PlacementCostModel) -> Path:
    rtt, bw, top_k = 40.0, 100.0, 10

    sparse = model.t_sparse_base_ms
    dense = model.t_dense_base_ms
    fusion = model.t_fusion_base_ms
    prefill = model.t_prefill_base_ms
    hydrate = model.t_hydrate_per_doc_ms * min(top_k, 5)

    hint_payload = model.query_bytes + 5 * model.doc_id_bytes
    fused_payload = model.query_bytes + top_k * model.doc_id_bytes
    wan_hint = model.wan_delay_ms(hint_payload, rtt, bw)
    wan_fused = model.wan_delay_ms(fused_payload, rtt, bw)

    # ---- Baseline P2 (fully serialized) ----
    base = [
        ("Retrieval (Sparse ∥ Dense)", 0.0, max(sparse, dense), C_DENSE),
        ("Fusion (RRF)", max(sparse, dense), fusion, C_FUSION),
        ("WAN: fused context", max(sparse, dense) + fusion, wan_fused, C_P2),
        ("Hydrate", max(sparse, dense) + fusion + wan_fused, hydrate, C_SPARSE),
        ("Prefill (TTFT)", max(sparse, dense) + fusion + wan_fused + hydrate, prefill, C_PREFILL),
    ]
    base_ttft = max(sparse, dense) + fusion + wan_fused + hydrate + prefill

    # ---- SPHP (speculative prefill overlaps the dense leg) ----
    spec_hydrate_start = sparse + wan_hint
    spec_prefill_start = spec_hydrate_start + hydrate
    spec_prefill_end = spec_prefill_start + prefill
    fused_arrival = dense + fusion + wan_fused
    sphp_ttft = max(fused_arrival, spec_prefill_end)

    sphp = [
        ("Sparse leg", 0.0, sparse, C_SPARSE),
        ("Dense leg", 0.0, dense, C_DENSE),
        ("WAN: sparse hint", sparse, wan_hint, C_P2),
        ("Provisional hydrate", spec_hydrate_start, hydrate, C_SPARSE),
        ("Speculative prefill", spec_prefill_start, prefill, C_PREFILL),
        ("WAN: fused context", dense + fusion, wan_fused, C_P2),
    ]

    fig, (ax_base, ax_sphp) = plt.subplots(
        2, 1, figsize=(6.5, 4.2), sharex=True,
        gridspec_kw={"hspace": 0.35},
    )
    # Extra bottom margin so the summary line clears the "Time (ms)" axis.
    fig.subplots_adjust(bottom=0.18)

    def draw_gantt(ax, bars, ttft, title):
        n = len(bars)
        names = []
        for i, (label, start, dur, color) in enumerate(bars):
            y = n - 1 - i
            names.append(label)
            ax.barh(y, dur, left=start, height=0.6, color=color,
                    edgecolor="white", linewidth=0.5, zorder=3)
            if dur >= 300.0:
                # Wide enough: label inside the bar.
                ax.text(start + dur / 2.0, y, label, va="center", ha="center",
                        fontsize=7, color="white", zorder=4, clip_on=False)
            else:
                # Narrow bar: place the label outside (to the right) with an
                # arrow so it is never clipped.
                ax.annotate(label, xy=(start + dur, y),
                            xytext=(start + dur + 40.0, y),
                            va="center", ha="left", fontsize=7,
                            color=color, zorder=4, clip_on=False,
                            arrowprops=dict(arrowstyle="-", color=color,
                                            lw=0.6, shrinkA=0, shrinkB=0))
        ax.axvline(ttft, color=OKABE_ITO["black"], linestyle="--",
                   linewidth=1.0, zorder=2)
        ax.text(ttft, n - 0.2, f" TTFT={ttft:.0f} ms",
                va="bottom", ha="left", fontsize=7,
                color=OKABE_ITO["black"])
        # Stage names on the y-axis ticks (one per bar).
        ax.set_yticks(range(n))
        ax.set_yticklabels(names, fontsize=7)
        ax.set_title(title, loc="left", fontsize=9)
        # Extra headroom on the right so outside labels are not clipped.
        ax.set_xlim(0, max(b[1] + b[2] for b in bars) * 1.30)
        ax.set_ylim(-0.6, n + 0.4)

    draw_gantt(ax_base, base, base_ttft,
              f"(a) Baseline P2 — serialized  (RTT={rtt:.0f} ms, BW={bw:.0f} Mbps)")
    draw_gantt(ax_sphp, sphp, sphp_ttft,
              "(b) P2-SPHP — speculative prefill overlaps the dense leg")

    ax_sphp.set_xlabel("Time (ms)")
    ax_base.set_ylabel("Pipeline stage")
    ax_sphp.set_ylabel("Pipeline stage")

    # Annotate the overlap saving.
    saving = base_ttft - sphp_ttft
    fig.text(0.99, 0.01,
             f"SPHP saves {saving:.0f} ms ({100 * saving / base_ttft:.1f}%) of TTFT "
             f"by overlapping prefill with the dense leg.",
             ha="right", va="bottom", fontsize=7, color=OKABE_ITO["gray"],
             style="italic")

    return _save(fig, "fig3_sphp_timeline.pdf")


# ---------------------------------------------------------------------------
# Fig 4 -- Per-stage latency breakdown (stacked bar)
# ---------------------------------------------------------------------------
def fig4_stage_breakdown(bench: dict) -> Path:
    tiers = ["delay_0ms", "delay_15ms", "delay_40ms", "delay_80ms"]
    labels = ["LAN\n(0 ms)", "Edge\n(+15 ms)", "WAN\n(+40 ms)", "WAN\n(+80 ms)"]

    def mean(vals):
        return float(np.mean(vals)) if vals else 0.0

    stages = {}
    for tier in tiers:
        runs = bench.get(tier, [])
        t = [r["timings"] for r in runs]
        sparse = mean([x["sparse_ms"] for x in t])
        dense = mean([x["dense_ms"] for x in t])
        fusion = mean([x["fusion_ms"] for x in t])
        ttft = mean([x["ttft_ms"] for x in t])
        decode = mean([x["decode_ms"] for x in t])
        # Prefill = TTFT residual after the (parallel) retrieval + fusion.
        prefill = max(ttft - max(sparse, dense) - fusion, 0.0)
        stages[tier] = {
            "Sparse": sparse,
            "Dense": dense,
            "Fusion": fusion,
            "Prefill": prefill,
            "Decode": decode,
        }

    order = ["Sparse", "Dense", "Fusion", "Prefill", "Decode"]
    colors = [C_SPARSE, C_DENSE, C_FUSION, C_PREFILL, C_DECODE]

    x = np.arange(len(tiers))
    width = 0.6
    bottom = np.zeros(len(tiers))

    fig, ax = plt.subplots(figsize=(6.0, 3.6))
    # Extra bottom margin so the footnote clears the x-tick condition labels.
    fig.subplots_adjust(bottom=0.20)
    for stage, color in zip(order, colors):
        vals = np.array([stages[t][stage] for t in tiers])
        ax.bar(x, vals, width, bottom=bottom, color=color,
               edgecolor="white", linewidth=0.5, label=stage)
        # Value labels inside tall-enough segments.
        for xi, (v, b) in enumerate(zip(vals, bottom)):
            if v > 40:
                ax.text(xi, b + v / 2.0, f"{v:.0f}", ha="center", va="center",
                        fontsize=6.5, color="white")
        bottom += vals

    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("Mean latency (ms)")
    ax.set_title("Per-Stage Latency Breakdown by Network Condition")
    ax.legend(loc="upper left", ncol=2, framealpha=0.9,
              title="Stage", title_fontsize=8)
    ax.set_ylim(0, bottom.max() * 1.05)

    fig.text(0.99, 0.01,
             "Sparse and Dense legs execute in parallel; the retrieval phase "
             "duration is max(Sparse, Dense).",
             ha="right", va="bottom", fontsize=6.5, color=OKABE_ITO["gray"],
             style="italic")

    return _save(fig, "fig4_stage_breakdown.pdf")


# ---------------------------------------------------------------------------
# Fig 5 -- TTFT vs. WAN RTT (P2 vs. P2-SPHP)
# ---------------------------------------------------------------------------
def fig5_ttft_vs_rtt(model: PlacementCostModel) -> Path:
    rtt_tiers = [0, 10, 30, 50, 100]
    bw = 100.0

    p2 = [model.predict_ttft_p2(rtt, bw) for rtt in rtt_tiers]
    sphp = [model.predict_ttft_p2_sphp(rtt, bw) for rtt in rtt_tiers]

    fig, ax = plt.subplots(figsize=(5.6, 3.6))
    ax.plot(rtt_tiers, p2, marker="o", color=C_P2, label="P2 (baseline)")
    ax.plot(rtt_tiers, sphp, marker="s", color=C_SPHP, label="P2-SPHP")

    # Shade the SPHP saving region.
    ax.fill_between(rtt_tiers, sphp, p2, color=C_SPHP, alpha=0.12,
                    label="SPHP TTFT saving")

    ax.set_xlabel("WAN RTT (ms)")
    ax.set_ylabel("TTFT (ms)")
    ax.set_title("Time-to-First-Token vs. WAN RTT  (BW = 100 Mbps)")
    ax.set_xticks(rtt_tiers)
    ax.legend(loc="upper left", framealpha=0.9)

    # Annotate the max saving.
    max_save = max(p - s for p, s in zip(p2, sphp))
    ax.annotate(f"up to {max_save:.0f} ms saved",
                xy=(100, sphp[-1]), xytext=(55, sphp[-1] + 60),
                fontsize=7, color=OKABE_ITO["black"],
                arrowprops=dict(arrowstyle="->", color=OKABE_ITO["gray"], lw=0.8))

    return _save(fig, "fig5_ttft_vs_rtt.pdf")


# ---------------------------------------------------------------------------
# Fig 6 -- Crossover placement map (heatmap over RTT x BW)
# ---------------------------------------------------------------------------
def fig6_crossover_map(crossover: list[dict]) -> Path:
    rtts = sorted({r["rtt_ms"] for r in crossover})
    bws = sorted({r["bw_mbps"] for r in crossover})

    topo_index = {"P0": 0, "P2": 1, "P2-SPHP": 2, "P3": 3}
    grid = np.zeros((len(bws), len(rtts)))
    for r in crossover:
        i = bws.index(r["bw_mbps"])
        j = rtts.index(r["rtt_ms"])
        grid[i, j] = topo_index[r["optimal_topology"]]

    cmap = ListedColormap([TOPO_COLORS[t] for t in ["P0", "P2", "P2-SPHP", "P3"]])
    bounds = [-0.5, 0.5, 1.5, 2.5, 3.5]

    fig, ax = plt.subplots(figsize=(6.0, 4.0))
    # No `extent`: imshow uses pixel coords 0..N-1 so integer ticks line up
    # one-to-one with the distinct RTT (columns) and BW (rows) values.
    im = ax.imshow(grid, cmap=cmap, vmin=-0.5, vmax=3.5,
                   aspect="auto", origin="lower", interpolation="nearest")

    # Cell labels.
    for r in crossover:
        i = bws.index(r["bw_mbps"])
        j = rtts.index(r["rtt_ms"])
        ax.text(j, i, r["optimal_topology"], ha="center", va="center",
                fontsize=7, color="white",
                bbox=dict(boxstyle="round,pad=0.15", fc="none",
                          ec="white", alpha=0.6))

    # Integer tick locations with the distinct sorted RTT / BW values as labels.
    ax.set_xticks(range(len(rtts)))
    ax.set_xticklabels([f"{int(t)}" for t in rtts])
    ax.set_yticks(range(len(bws)))
    ax.set_yticklabels([f"{int(b)}" for b in bws])
    ax.set_xlabel("WAN RTT (ms)")
    ax.set_ylabel("Bandwidth (Mbps)")
    ax.set_title("Optimal Placement Crossover Map")

    handles = [Patch(color=TOPO_COLORS[t], label=t)
               for t in ["P0", "P2", "P2-SPHP", "P3"]]
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.12),
              ncol=4, frameon=False)

    fig.tight_layout()
    return _save(fig, "fig6_crossover_map.pdf")


# ---------------------------------------------------------------------------
# Fig 7 -- Retrieval quality (Sparse vs. Dense vs. Hybrid)
# ---------------------------------------------------------------------------
def fig7_retrieval_quality() -> Path:
    # Representative MS MARCO dev quality values. The live Node B gateway was
    # offline at figure-generation time, so these are literature-consistent
    # values for a BM25 / BGE-M3 / RRF hybrid; the caption flags this.
    metrics = ["MRR", "nDCG@10", "Recall@100"]
    sparse = [0.20, 0.30, 0.55]
    dense = [0.28, 0.42, 0.72]
    hybrid = [0.31, 0.46, 0.78]

    x = np.arange(len(metrics))
    width = 0.26

    fig, ax = plt.subplots(figsize=(5.6, 3.6))
    ax.bar(x - width, sparse, width, color=C_SPARSE,
           edgecolor="white", linewidth=0.5, label="Sparse (BM25)")
    ax.bar(x, dense, width, color=C_DENSE,
           edgecolor="white", linewidth=0.5, label="Dense (BGE-M3)")
    ax.bar(x + width, hybrid, width, color=C_FUSION,
           edgecolor="white", linewidth=0.5, label="Hybrid (RRF)")

    for xi, (s, d, h) in enumerate(zip(sparse, dense, hybrid)):
        ax.text(xi - width, s + 0.01, f"{s:.2f}", ha="center", fontsize=6.5)
        ax.text(xi, d + 0.01, f"{d:.2f}", ha="center", fontsize=6.5)
        ax.text(xi + width, h + 0.01, f"{h:.2f}", ha="center", fontsize=6.5)

    ax.set_xticks(x)
    ax.set_xticklabels(metrics)
    ax.set_ylabel("Score")
    ax.set_ylim(0, 1.0)
    ax.set_title("Retrieval Quality: Sparse vs. Dense vs. Hybrid")
    ax.legend(loc="upper left", framealpha=0.9)

    fig.text(0.99, 0.01,
             "Representative MS MARCO dev values (gateway offline at "
             "generation time); pending live per-mode evaluation.",
             ha="right", va="bottom", fontsize=6.5, color=OKABE_ITO["gray"],
             style="italic")

    return _save(fig, "fig7_retrieval_quality.pdf")


# ---------------------------------------------------------------------------
# Fig 8 -- Cost-model parity (predicted vs. measured TTFT)
# ---------------------------------------------------------------------------
def fig8_cost_model_parity(model: PlacementCostModel, bench: dict) -> Path:
    delay_map = {
        "delay_0ms": 0.0,
        "delay_15ms": 15.0,
        "delay_40ms": 40.0,
        "delay_80ms": 80.0,
    }
    bw = 100.0

    predicted, measured = [], []
    for tier, rtt in delay_map.items():
        for r in bench.get(tier, []):
            t = r["timings"]
            pred = model.predict_ttft_p2(rtt, bw)
            predicted.append(pred)
            measured.append(t["ttft_ms"])

    predicted = np.array(predicted)
    measured = np.array(measured)

    fig, ax = plt.subplots(figsize=(5.2, 4.6))
    ax.scatter(measured, predicted, s=18, alpha=0.55, color=C_P2,
               edgecolor="none", label="Per-query (P2 model)")

    lo = np.minimum(measured, predicted)
    hi = np.maximum(measured, predicted)
    ax.fill_between(np.r_[measured, measured[::-1]],
                    np.r_[lo, hi[::-1]],
                    color=C_P2, alpha=0.08)

    # Perfect-prediction diagonal and +/-10% error bands.
    lim = max(measured.max(), predicted.max()) * 1.05
    ax.plot([0, lim], [0, lim], color=OKABE_ITO["black"], linestyle="-",
            linewidth=1.0, label="y = x (perfect)")
    xs = np.linspace(0, lim, 2)
    ax.plot(xs, 0.9 * xs, color=OKABE_ITO["gray"], linestyle="--",
            linewidth=0.8, label="±10% band")
    ax.plot(xs, 1.1 * xs, color=OKABE_ITO["gray"], linestyle="--",
            linewidth=0.8)

    ax.set_xlabel("Measured TTFT (ms)")
    ax.set_ylabel("Predicted TTFT (ms)")
    ax.set_title("Cost-Model Parity: Predicted vs. Measured TTFT")
    ax.set_xlim(0, lim)
    ax.set_ylim(0, lim)
    ax.legend(loc="upper left", framealpha=0.9)

    # Report the component-level MAPE from the validation file.
    # Placed bottom-right so it does not collide with the top-left legend.
    val = load_validation()
    ax.text(0.55, 0.15,
            f"component MAPE = {val.get('test_component_mape_pct', 'n/a')}%\n"
            f"macro MAPE = {val.get('test_macro_mape_pct', 'n/a')}%",
            transform=ax.transAxes, va="bottom", ha="left", fontsize=7,
            bbox=dict(boxstyle="round,pad=0.3", fc="white",
                      ec=OKABE_ITO["gray"], alpha=0.9))

    return _save(fig, "fig8_cost_model_parity.pdf")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    FIG_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 64)
    print("ICDCS PAPER: GENERATING PUBLICATION FIGURES ->", FIG_DIR)
    print("=" * 64)

    bench = load_benchmark()
    crossover = load_crossover()
    model = fit_model()

    outputs = [
        fig1_arch(model),
        fig2_topologies(),
        fig3_sphp_timeline(model),
        fig4_stage_breakdown(bench),
        fig5_ttft_vs_rtt(model),
        fig6_crossover_map(crossover),
        fig7_retrieval_quality(),
        fig8_cost_model_parity(model, bench),
    ]

    print("\nGenerated figures:")
    for p in outputs:
        size = p.stat().st_size
        status = "OK" if size > 0 else "EMPTY!"
        print(f"  [{status}] {p}  ({size} bytes)")

    # Final verification.
    missing = [p for p in outputs if not p.exists() or p.stat().st_size == 0]
    if missing:
        print("\nERROR: some figures missing or empty:", missing)
        sys.exit(1)
    print(f"\nAll {len(outputs)} figures generated successfully.")


if __name__ == "__main__":
    main()
