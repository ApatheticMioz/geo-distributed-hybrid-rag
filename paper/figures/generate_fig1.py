import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.patches import FancyBboxPatch, Rectangle
import os
import sys

# ---------------------------------------------------------------------------
# Palette: single source of truth is experiments/analysis/figstyle.py, the
# same CVD-safe module behind fig5/fig8. Ink-first diagram style (white cards,
# near-black borders/text); hue is reserved for the dataflow arrows, the
# speculative-path card border, and the KV-cache chips.
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "experiments", "analysis"))
import figstyle

INK          = figstyle.INK            # card borders, primary text
INK2         = figstyle.INK_SECONDARY  # subtitles, secondary text
INK_MUTED    = figstyle.INK_MUTED      # container chrome, metadata
GRID         = figstyle.GRID
BASELINE     = figstyle.BASELINE
BLUE         = figstyle.BLUE           # retrieval/query/context flows
ORANGE       = figstyle.ORANGE         # speculative fast path
TINT_ORANGE  = figstyle.TINTS["orange"]
WHITE        = figstyle.SURFACE

# ---------------------------------------------------------------------------
# Layout discipline: every text artist is measured against its box and
# shrunk until it fits with PAD_X clearance on each side. Container titles
# hang from the top border at a fixed TITLE_GAP so glyph-to-border spacing
# is deterministic instead of hand-tuned.
# ---------------------------------------------------------------------------
PAD_X     = 0.045   # in: horizontal clearance between text and its box border
TITLE_GAP = 0.055   # in: vertical clearance from container top border to title glyph top

def compile_tikz():
    tex_path = os.path.join(_HERE, "fig1_arch.tex")
    if os.path.exists(tex_path):
        import shutil
        import subprocess
        pdflatex = shutil.which("pdflatex")
        if not pdflatex and os.path.exists(r"C:\Users\Apath\AppData\Local\Programs\MiKTeX\miktex\bin\x64\pdflatex.exe"):
            pdflatex = r"C:\Users\Apath\AppData\Local\Programs\MiKTeX\miktex\bin\x64\pdflatex.exe"
        if pdflatex:
            try:
                res = subprocess.run([pdflatex, "-interaction=nonstopmode", "fig1_arch.tex"],
                                     cwd=_HERE, capture_output=True, text=True, check=True)
                out_pdf = os.path.join(_HERE, "fig1_arch.pdf")
                if os.path.exists(out_pdf) and os.path.getsize(out_pdf) > 0:
                    print("Publication-grade TikZ Fig 1 generated at:", out_pdf)
                    return True
            except Exception as e:
                print("TikZ pdflatex compilation failed, falling back to matplotlib:", e)
    return False

def generate_publication_fig1():
    if compile_tikz():
        return
    # Exact IEEE full textwidth dimensions
    # Width: 7.16 inches (515.4 pt), Height: 2.80 inches (201.6 pt)
    fig_w, fig_h = 7.16, 2.80
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=300)
    ax.set_xlim(0, fig_w)
    ax.set_ylim(0, fig_h)
    ax.axis('off')

    # --- measured text helpers ------------------------------------------------
    fig.canvas.draw()
    _renderer = fig.canvas.get_renderer()
    _inv = ax.transData.inverted()

    def _width_in(artist):
        bb = artist.get_window_extent(renderer=_renderer)
        p0 = _inv.transform((bb.x0, bb.y0))
        p1 = _inv.transform((bb.x1, bb.y1))
        return abs(p1[0] - p0[0])

    def fit_size(s, max_w, size, **kw):
        """Largest font size <= ``size`` at which text ``s`` fits ``max_w``."""
        probe = ax.text(0, -10, s, fontsize=size, **kw)
        while _width_in(probe) > max_w and size > 4.0:
            size -= 0.2
            probe.set_fontsize(size)
        probe.remove()
        return size

    def draw_text(x, y, s, max_w, size, **kw):
        """Draw text shrunk so its width fits ``max_w`` with side clearance."""
        size = fit_size(s, max_w, size, **kw)
        return ax.text(x, y, s, fontsize=size, **kw)

    def draw_container_title(cx, top, s, max_w, size, ha='center', x=None, **kw):
        """Title hanging TITLE_GAP below a container's top border."""
        return draw_text(cx if ha == 'center' else x, top - TITLE_GAP, s,
                         max_w, size, ha=ha, va='top', **kw)

    # Helper: draw clean rounded component card
    def draw_card(x, y, w, h, title="", subtitle="", fcolor=WHITE, bcolor=INK,
                  tcolor=INK, scolor=INK2, lw=0.9, r=0.03, z=3, tsize=None):
        box = FancyBboxPatch(
            (x, y), w, h,
            boxstyle=f"round,pad=0.01,rounding_size={r}",
            facecolor=fcolor, edgecolor=bcolor, linewidth=lw, zorder=z
        )
        ax.add_patch(box)
        inner_w = w - 2 * PAD_X
        if title and subtitle:
            draw_text(x + w/2, y + h*0.66, title, inner_w, tsize or 6.8, ha='center', va='center',
                      fontweight='bold', color=tcolor, zorder=z+1)
            draw_text(x + w/2, y + h*0.30, subtitle, inner_w, 5.6, ha='center', va='center',
                      color=scolor, zorder=z+1)
        elif title:
            draw_text(x + w/2, y + h*0.50, title, inner_w, tsize or 6.8, ha='center', va='center',
                      fontweight='bold', color=tcolor, zorder=z+1)

    # -------------------------------------------------------------------------
    # 1. PHYSICAL TIERS (CONTAINER CHASSIS)
    # -------------------------------------------------------------------------
    # Tier 1: Remote Edge (Node C)
    # x in [0.12, 1.77], width = 1.65 in, y in [0.40, 2.70], height = 2.30 in
    tier_c = FancyBboxPatch(
        (0.12, 0.40), 1.65, 2.30,
        boxstyle="round,pad=0.02,rounding_size=0.05",
        facecolor=WHITE, edgecolor=INK_MUTED, linewidth=1.0, zorder=2
    )
    ax.add_patch(tier_c)
    draw_container_title(0.12 + 1.65/2, 2.70, "Remote Edge (Node C)",
                         1.65 - 2*PAD_X, 8.0, fontweight='bold', color=INK, zorder=3)

    # Outer Container: Co-located Cluster (Node B & Node A in Same Room LAN)
    # x in [3.35, 7.04], width = 3.69 in, y in [0.40, 2.70], height = 2.30 in
    cluster_box = FancyBboxPatch(
        (3.35, 0.40), 3.69, 2.30,
        boxstyle="round,pad=0.02,rounding_size=0.05",
        facecolor=WHITE, edgecolor=INK_MUTED, linewidth=0.9, zorder=1
    )
    ax.add_patch(cluster_box)
    draw_container_title(3.35 + 3.69/2, 2.70, "Co-located Cluster (Local Gigabit LAN, <1 ms)",
                         3.69 - 2*PAD_X, 7.8, fontweight='bold', color=INK, zorder=3)

    # Tier 2: Neural Retrieval Tier (Node B) - Inside Cluster (Top)
    # x in [3.43, 6.96], width = 3.53 in, y in [1.68, 2.44], height = 0.76 in
    tier_b = FancyBboxPatch(
        (3.43, 1.68), 3.53, 0.76,
        boxstyle="round,pad=0.015,rounding_size=0.04",
        facecolor=WHITE, edgecolor=INK_MUTED, linewidth=0.8, linestyle=":", zorder=2
    )
    ax.add_patch(tier_b)
    draw_container_title(0, 2.44, "Neural Retrieval Tier (Node B)",
                         3.53 - 2*PAD_X, 7.2, ha='left', x=3.43 + PAD_X,
                         fontweight='bold', color=INK, zorder=3)

    # Tier 3: LLM Generation Tier (Node A) - Inside Cluster (Bottom)
    # Top raised to 1.28 so the title band clears the stage cards below.
    # x in [3.43, 6.96], width = 3.53 in, y in [0.46, 1.28], height = 0.82 in
    tier_a = FancyBboxPatch(
        (3.43, 0.46), 3.53, 0.82,
        boxstyle="round,pad=0.015,rounding_size=0.04",
        facecolor=WHITE, edgecolor=INK_MUTED, linewidth=0.8, linestyle=":", zorder=2
    )
    ax.add_patch(tier_a)
    draw_container_title(0, 1.28, "LLM Generation Tier (Node A)",
                         3.53 - 2*PAD_X, 7.2, ha='left', x=3.43 + PAD_X,
                         fontweight='bold', color=INK, zorder=3)

    # -------------------------------------------------------------------------
    # 2. INTERNAL COMPONENTS
    # -------------------------------------------------------------------------
    # --- Node C Components (Width = 1.45 in, x in [0.22, 1.67]) ---
    # Client Ingress (aligns with Node B Neural Retrieval)
    draw_card(0.22, 1.76, 1.45, 0.50, "Client Ingress", "HTTP / SSE Gateway")

    # Tantivy BM25 Filter (origin of the orange speculative fast path)
    draw_card(0.22, 1.04, 1.45, 0.50, "Sparse Search", "Tantivy BM25 Index")

    # Response Sink
    draw_card(0.22, 0.48, 1.45, 0.40, "Response Sink", "Stream Egress Delivery",
              fcolor=WHITE, bcolor=BASELINE, tcolor=INK2, scolor=INK_MUTED, lw=0.8)

    # Intra-Node C arrow: Ingress -> BM25
    ax.annotate("", xy=(0.945, 1.54), xytext=(0.945, 1.76),
                arrowprops=dict(arrowstyle="-|>", mutation_scale=8, linewidth=0.8, color=INK2, zorder=5))

    # --- Node B Components (3 Pipelined Stages) ---
    # Cards at y = 1.74, height = 0.46 in. Titles share one harmonized size
    # so a shrunk label never sits next to a full-size neighbor.
    b_titles = [("Dense Encoder", 1.00), ("Vector Search", 1.00), ("Rank Fusion", 1.10)]
    b_tsize = min(fit_size(t, w - 2*PAD_X, 6.8, fontweight='bold') for t, w in b_titles)
    # Stage B1: Dense Encoder
    draw_card(3.48, 1.74, 1.00, 0.46, "Dense Encoder", "BGE-M3 (1024-d)", tsize=b_tsize)
    # Stage B2: Vector Search
    draw_card(4.62, 1.74, 1.00, 0.46, "Vector Search", "Qdrant HNSW", tsize=b_tsize)
    # Stage B3: Rank Fusion (x in [5.76, 6.86], width = 1.10 in)
    draw_card(5.76, 1.74, 1.10, 0.46, "Rank Fusion", "RRF ($k=60$)", tsize=b_tsize)

    # Arrows between Node B stages
    ax.annotate("", xy=(4.62, 1.97), xytext=(4.48, 1.97),
                arrowprops=dict(arrowstyle="-|>", mutation_scale=7, linewidth=0.8, color=BLUE, zorder=5))
    ax.annotate("", xy=(5.76, 1.97), xytext=(5.62, 1.97),
                arrowprops=dict(arrowstyle="-|>", mutation_scale=7, linewidth=0.8, color=BLUE, zorder=5))

    # --- Node A Components (3 Stages) ---
    # Cards at y = 0.50, height = 0.56 in.
    # Stage A1: Speculative Prefill (x in [3.48, 4.92], width = 1.44 in)
    # Orange border + orange KV chips mark this card as the fast-path target,
    # matching the orange hint arrow that feeds it.
    w_prefill = 1.44
    draw_card(3.48, 0.50, w_prefill, 0.56, title="", subtitle="",
              fcolor=WHITE, bcolor=ORANGE, lw=1.0)
    draw_text(3.48 + w_prefill/2, 0.94, "Hydrate + Spec. Prefill",
              w_prefill - 2*PAD_X, 6.2, ha='center', va='center',
              fontweight='bold', color=ORANGE, zorder=5)

    # Segmented KV Cache Memory Array inside Stage A1
    kv_x = 3.56
    kv_w = w_prefill - 0.16
    kv_y = 0.74
    kv_h = 0.16
    n_blocks = 5
    bw = kv_w / n_blocks
    for i in range(n_blocks):
        bx = kv_x + i * bw
        if i < 4:
            fc, ec, tc = TINT_ORANGE, ORANGE, INK
            txt = f"$P_{{{i+1}}}$"
        else:
            fc, ec, tc = GRID, BASELINE, INK_MUTED
            txt = "$\\cdots$"
        ax.add_patch(Rectangle((bx, kv_y - kv_h/2), bw - 0.02, kv_h,
                               facecolor=fc, edgecolor=ec, linewidth=0.6, zorder=5))
        ax.text(bx + bw/2 - 0.01, kv_y, txt, ha='center', va='center',
                fontsize=5.8, fontweight='bold', color=tc, zorder=6)
    draw_text(3.48 + w_prefill/2, 0.57, "Resident KV Cache",
              w_prefill - 2*PAD_X, 5.4, ha='center', va='center', color=INK2, zorder=5)

    # Stage A2: Verification Barrier (x in [5.04, 6.04], width = 1.00 in)
    draw_card(5.04, 0.50, 1.00, 0.56, title="", subtitle="")
    draw_text(5.04 + 0.50, 0.94, "Verification", 1.00 - 2*PAD_X, 6.8,
              ha='center', va='center', fontweight='bold', color=INK, zorder=5)
    draw_text(5.04 + 0.50, 0.74, r"$\mathcal{H} \cap \mathcal{F} \geq \theta$",
              1.00 - 2*PAD_X, 6.5, ha='center', va='center',
              fontweight='bold', color=INK, zorder=5)
    draw_text(5.04 + 0.50, 0.57, r"$h \approx 0.90$", 1.00 - 2*PAD_X, 5.4,
              ha='center', va='center', fontweight='bold', color=INK2, zorder=5)

    # Stage A3: Decode Engine (x in [6.18, 6.96], width = 0.78 in)
    draw_card(6.18, 0.50, 0.78, 0.56, title="", subtitle="")
    draw_text(6.18 + 0.39, 0.94, "Decode", 0.78 - 2*PAD_X, 6.8,
              ha='center', va='center', fontweight='bold', color=INK, zorder=5)
    draw_text(6.18 + 0.39, 0.74, "Qwen3.8-27B", 0.78 - 2*PAD_X, 5.8,
              ha='center', va='center', color=INK2, zorder=5)
    draw_text(6.18 + 0.39, 0.57, "35.3 tok/s", 0.78 - 2*PAD_X, 5.4,
              ha='center', va='center', color=INK_MUTED, zorder=5)

    # Intra-Node A arrows
    ax.annotate("", xy=(5.04, 0.74), xytext=(4.92, 0.74),
                arrowprops=dict(arrowstyle="-|>", mutation_scale=8, linewidth=0.9, color=ORANGE, zorder=5))
    ax.annotate("", xy=(6.18, 0.74), xytext=(6.04, 0.74),
                arrowprops=dict(arrowstyle="-|>", mutation_scale=8, linewidth=0.8, color=INK2, zorder=5))

    # -------------------------------------------------------------------------
    # 3. INTER-TIER DATAFLOW (AUTOMATICALLY MASKED PILL BADGES)
    # -------------------------------------------------------------------------
    corridor_center = (1.67 + 3.48) / 2  # 2.575 in

    # Highway 1: User Query (Node C Ingress -> Node B Dense Encoder)
    # Continuous blue arrow at y = 1.97 (zorder=4)
    ax.annotate("", xy=(3.48, 1.97), xytext=(1.67, 1.97),
                arrowprops=dict(arrowstyle="-|>", mutation_scale=9, linewidth=1.1, color=BLUE, zorder=4))
    # Pill badge (zorder=6) automatically masks the line behind it
    ax.text(corridor_center, 1.97, "Query $q$ (Link C)", ha='center', va='center',
            fontsize=6.5, fontweight='bold', color=BLUE, zorder=6,
            bbox=dict(facecolor=WHITE, edgecolor=BLUE, boxstyle='round,pad=0.22', lw=0.7))

    # Highway 2: SPHP Early Hint (Node C BM25 -> Node A Speculative Prefill)
    # Continuous dashed orange arrow from (1.67, 1.20) to (3.48, 0.94) (zorder=4)
    ax.annotate("", xy=(3.48, 0.94), xytext=(1.67, 1.20),
                arrowprops=dict(arrowstyle="-|>", mutation_scale=10, linewidth=1.3,
                                linestyle="--", color=ORANGE, zorder=4))
    # Compact 2-line pill badge (zorder=6) at midpoint (2.575, 1.07) automatically masks line
    ax.text(corridor_center, 1.07, "Hint $\\mathcal{H}$ (104 B)\n[Fast Path]",
            ha='center', va='center', fontsize=5.8, fontweight='bold', color=ORANGE, zorder=6,
            bbox=dict(facecolor=WHITE, edgecolor=ORANGE, boxstyle='round,pad=0.22', lw=0.8))

    # Highway 3: Verified Context F (Node B RRF -> Node A Verification Barrier)
    # Continuous blue vertical arrow from (5.90, 1.74) to (5.90, 1.06) (zorder=4)
    ax.annotate("", xy=(5.90, 1.06), xytext=(5.90, 1.74),
                arrowprops=dict(arrowstyle="-|>", mutation_scale=8, linewidth=1.1, color=BLUE, zorder=4))
    # Pill badge (zorder=6) centered at (5.90, 1.40) in the tier gap
    ax.text(5.90, 1.40, "Context $\\mathcal{F}$ (144 B · Link B)",
            ha='center', va='center', fontsize=5.6, fontweight='bold', color=BLUE, zorder=6,
            bbox=dict(facecolor=WHITE, edgecolor=BLUE, boxstyle='round,pad=0.20', lw=0.7))

    # Highway 4: Continuous SSE Token Stream Return (Node A Decode -> Node C Response Sink)
    # Continuous dashed line along bottom (zorder=4)
    ax.plot([6.57, 6.57, 0.945, 0.945], [0.50, 0.20, 0.20, 0.48],
            linestyle="--", color=INK, linewidth=0.9, zorder=4)
    ax.annotate("", xy=(0.945, 0.48), xytext=(0.945, 0.38),
                arrowprops=dict(arrowstyle="-|>", mutation_scale=7, linewidth=0.9, color=INK, zorder=4))

    # Badge: Continuous Token Stream Delivery at (3.74, 0.20) (zorder=6) masks line
    ax.text(3.74, 0.20, "Continuous Token Stream Return (35.3 tok/s)",
            ha='center', va='center', fontsize=6.2, fontweight='bold', color=INK, zorder=6,
            bbox=dict(facecolor=WHITE, edgecolor=BASELINE, boxstyle='round,pad=0.25', lw=0.6))

    out_pdf = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fig1_arch.pdf")
    plt.savefig(out_pdf, format="pdf", bbox_inches='tight', pad_inches=0.02)
    plt.close()
    print("Clean Fig 1 generated at:", out_pdf)

if __name__ == '__main__':
    generate_publication_fig1()
