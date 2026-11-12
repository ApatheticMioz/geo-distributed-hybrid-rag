import matplotlib.pyplot as plt
import matplotlib.patches as patches
from matplotlib.patches import FancyBboxPatch, Rectangle
import os

def generate_publication_fig1():
    # Exact IEEE full textwidth dimensions
    # Width: 7.16 inches (515.4 pt), Height: 2.80 inches (201.6 pt)
    fig_w, fig_h = 7.16, 2.80
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=300)
    ax.set_xlim(0, fig_w)
    ax.set_ylim(0, fig_h)
    ax.axis('off')

    # -------------------------------------------------------------------------
    # COLOR PALETTE (Strict Systems Publication Palette)
    # -------------------------------------------------------------------------
    SLATE_DARK    = "#0F172A"   # Slate-900: Primary frames, titles, flow
    SLATE_MID     = "#334155"   # Slate-700: Subtitles and body labels
    SLATE_MUTED   = "#64748B"   # Slate-500: Annotations, metadata
    SLATE_BORDER  = "#94A3B8"   # Slate-400: Structural borders
    CARD_BORDER   = "#CBD5E1"   # Slate-300: Inner component borders
    BG_LIGHT      = "#F8FAFC"   # Slate-50: Tier backgrounds
    WHITE         = "#FFFFFF"

    # Neural Retrieval Stream (Steel Blue)
    BLUE_DARK     = "#1D4ED8"   # Blue-700
    BLUE_BORDER   = "#3B82F6"   # Blue-500
    BLUE_FILL     = "#EFF6FF"   # Blue-50

    # Speculative Fast Path (Terracotta / Rust)
    RUST_DARK     = "#C2410C"   # Orange-700
    RUST_BORDER   = "#EA580C"   # Orange-600
    RUST_FILL     = "#FFF7ED"   # Orange-50

    # Verification Accent (Forest Green)
    GREEN_DARK    = "#15803D"   # Green-800

    # Helper: draw clean rounded component card
    def draw_card(x, y, w, h, title="", subtitle="", fcolor=WHITE, bcolor=CARD_BORDER,
                  tcolor=SLATE_DARK, scolor=SLATE_MID, lw=0.8, r=0.03, z=3):
        box = FancyBboxPatch(
            (x, y), w, h,
            boxstyle=f"round,pad=0.01,rounding_size={r}",
            facecolor=fcolor, edgecolor=bcolor, linewidth=lw, zorder=z
        )
        ax.add_patch(box)
        if title and subtitle:
            ax.text(x + w/2, y + h*0.66, title, ha='center', va='center',
                    fontsize=6.8, fontweight='bold', color=tcolor, zorder=z+1)
            ax.text(x + w/2, y + h*0.30, subtitle, ha='center', va='center',
                    fontsize=5.6, color=scolor, zorder=z+1)
        elif title:
            ax.text(x + w/2, y + h*0.50, title, ha='center', va='center',
                    fontsize=6.8, fontweight='bold', color=tcolor, zorder=z+1)

    # -------------------------------------------------------------------------
    # 1. PHYSICAL TIERS (CONTAINER CHASSIS)
    # -------------------------------------------------------------------------
    # Tier 1: Remote Edge (Node C)
    # x in [0.12, 1.77], width = 1.65 in, y in [0.40, 2.70], height = 2.30 in
    tier_c = FancyBboxPatch(
        (0.12, 0.40), 1.65, 2.30,
        boxstyle="round,pad=0.02,rounding_size=0.05",
        facecolor=BG_LIGHT, edgecolor=SLATE_BORDER, linewidth=1.0, zorder=2
    )
    ax.add_patch(tier_c)
    ax.text(0.12 + 1.65/2, 2.56, "Remote Edge (Node C)", ha='center', va='center',
            fontsize=8.0, fontweight='bold', color=SLATE_DARK, zorder=3)

    # Outer Container: Co-located Cluster (Node B & Node A in Same Room LAN)
    # x in [3.35, 7.04], width = 3.69 in, y in [0.40, 2.70], height = 2.30 in
    cluster_box = FancyBboxPatch(
        (3.35, 0.40), 3.69, 2.30,
        boxstyle="round,pad=0.02,rounding_size=0.05",
        facecolor="#FAFAFA", edgecolor=SLATE_BORDER, linewidth=0.9, zorder=1
    )
    ax.add_patch(cluster_box)
    ax.text(3.35 + 3.69/2, 2.56, "Co-located Cluster (Local Gigabit LAN, <1 ms)",
            ha='center', va='center', fontsize=7.8, fontweight='bold', color=SLATE_DARK, zorder=3)

    # Tier 2: Neural Retrieval Tier (Node B) - Inside Cluster (Top)
    # x in [3.43, 6.96], width = 3.53 in, y in [1.68, 2.44], height = 0.76 in
    tier_b = FancyBboxPatch(
        (3.43, 1.68), 3.53, 0.76,
        boxstyle="round,pad=0.015,rounding_size=0.04",
        facecolor=BLUE_FILL, edgecolor=BLUE_BORDER, linewidth=0.8, linestyle=":", zorder=2
    )
    ax.add_patch(tier_b)
    ax.text(3.52, 2.32, "Neural Retrieval Tier (Node B)", ha='left', va='center',
            fontsize=7.2, fontweight='bold', color=BLUE_DARK, zorder=3)

    # Tier 3: LLM Generation Tier (Node A) - Inside Cluster (Bottom)
    # x in [3.43, 6.96], width = 3.53 in, y in [0.46, 1.22], height = 0.76 in
    tier_a = FancyBboxPatch(
        (3.43, 0.46), 3.53, 0.76,
        boxstyle="round,pad=0.015,rounding_size=0.04",
        facecolor=WHITE, edgecolor=SLATE_BORDER, linewidth=0.8, linestyle=":", zorder=2
    )
    ax.add_patch(tier_a)
    ax.text(3.52, 1.13, "LLM Generation Tier (Node A)", ha='left', va='center',
            fontsize=7.2, fontweight='bold', color=SLATE_DARK, zorder=3)

    # -------------------------------------------------------------------------
    # 2. INTERNAL COMPONENTS
    # -------------------------------------------------------------------------
    # --- Node C Components (Width = 1.45 in, x in [0.22, 1.67]) ---
    # Client Ingress (aligns with Node B Neural Retrieval)
    draw_card(0.22, 1.76, 1.45, 0.50, "Client Ingress", "HTTP / SSE Gateway",
              fcolor=WHITE, bcolor=SLATE_DARK, tcolor=SLATE_DARK, lw=0.9)

    # Tantivy BM25 Filter (aligns with Speculative Fast Path)
    draw_card(0.22, 1.04, 1.45, 0.50, "Sparse Search", "Tantivy BM25 Index",
              fcolor=RUST_FILL, bcolor=RUST_BORDER, tcolor=RUST_DARK, scolor=RUST_DARK, lw=1.0)

    # Response Sink
    draw_card(0.22, 0.48, 1.45, 0.40, "Response Sink", "Stream Egress Delivery",
              fcolor=WHITE, bcolor=CARD_BORDER, tcolor=SLATE_MID, scolor=SLATE_MUTED)

    # Intra-Node C arrow: Ingress -> BM25
    ax.annotate("", xy=(0.945, 1.54), xytext=(0.945, 1.76),
                arrowprops=dict(arrowstyle="-|>", mutation_scale=8, linewidth=0.8, color=SLATE_DARK, zorder=5))

    # --- Node B Components (3 Pipelined Stages) ---
    # Cards at y = 1.74, height = 0.46 in.
    # Stage B1: Dense Encoder
    draw_card(3.48, 1.74, 1.00, 0.46, "Dense Encoder", "BGE-M3 (1024-d)",
              fcolor=WHITE, bcolor=BLUE_BORDER, tcolor=BLUE_DARK, scolor=SLATE_MID)
    # Stage B2: Vector Search
    draw_card(4.62, 1.74, 1.00, 0.46, "Vector Search", "Qdrant HNSW",
              fcolor=WHITE, bcolor=BLUE_BORDER, tcolor=BLUE_DARK, scolor=SLATE_MID)
    # Stage B3: Rank Fusion (x in [5.76, 6.86], width = 1.10 in)
    draw_card(5.76, 1.74, 1.10, 0.46, "Rank Fusion", "RRF ($k=60$)",
              fcolor=WHITE, bcolor=BLUE_BORDER, tcolor=BLUE_DARK, scolor=SLATE_MID)

    # Arrows between Node B stages
    ax.annotate("", xy=(4.62, 1.97), xytext=(4.48, 1.97),
                arrowprops=dict(arrowstyle="-|>", mutation_scale=7, linewidth=0.8, color=BLUE_BORDER, zorder=5))
    ax.annotate("", xy=(5.76, 1.97), xytext=(5.62, 1.97),
                arrowprops=dict(arrowstyle="-|>", mutation_scale=7, linewidth=0.8, color=BLUE_BORDER, zorder=5))

    # --- Node A Components (3 Stages) ---
    # Cards at y = 0.50, height = 0.56 in.
    # Stage A1: Speculative Prefill (x in [3.48, 4.88], width = 1.40 in)
    w_prefill = 1.40
    draw_card(3.48, 0.50, w_prefill, 0.56, title="", subtitle="",
              fcolor=RUST_FILL, bcolor=RUST_BORDER, lw=1.0)
    ax.text(3.48 + w_prefill/2, 0.94, "Speculative Prefill", ha='center', va='center',
            fontsize=6.8, fontweight='bold', color=RUST_DARK, zorder=5)

    # Segmented KV Cache Memory Array inside Stage A1
    kv_x = 3.56
    kv_w = w_prefill - 0.16  # 1.24 in
    kv_y = 0.74
    kv_h = 0.16
    n_blocks = 5
    bw = kv_w / n_blocks
    for i in range(n_blocks):
        bx = kv_x + i * bw
        if i < 4:
            fc, ec, tc = WHITE, RUST_BORDER, RUST_DARK
            txt = f"$P_{{{i+1}}}$"
        else:
            fc, ec, tc = "#F1F5F9", CARD_BORDER, SLATE_MUTED
            txt = "$\\cdots$"
        ax.add_patch(Rectangle((bx, kv_y - kv_h/2), bw - 0.02, kv_h,
                               facecolor=fc, edgecolor=ec, linewidth=0.6, zorder=5))
        ax.text(bx + bw/2 - 0.01, kv_y, txt, ha='center', va='center',
                fontsize=5.8, fontweight='bold', color=tc, zorder=6)
    ax.text(3.48 + w_prefill/2, 0.57, "Resident KV Cache", ha='center', va='center',
            fontsize=5.4, color=RUST_DARK, zorder=5)

    # Stage A2: Verification Barrier (x in [5.04, 6.04], width = 1.00 in)
    draw_card(5.04, 0.50, 1.00, 0.56, title="", subtitle="",
              fcolor=WHITE, bcolor=SLATE_DARK, lw=0.9)
    ax.text(5.04 + 0.50, 0.94, "Verification", ha='center', va='center',
            fontsize=6.8, fontweight='bold', color=SLATE_DARK, zorder=5)
    ax.text(5.04 + 0.50, 0.74, r"$\mathcal{H} \cap \mathcal{F} \geq \theta$", ha='center', va='center',
            fontsize=6.5, fontweight='bold', color=SLATE_DARK, zorder=5)
    ax.text(5.04 + 0.50, 0.57, "Hit Rate ~90%", ha='center', va='center',
            fontsize=5.4, fontweight='bold', color=GREEN_DARK, zorder=5)

    # Stage A3: Decode Engine (x in [6.18, 6.96], width = 0.78 in)
    draw_card(6.18, 0.50, 0.78, 0.56, title="", subtitle="",
              fcolor=WHITE, bcolor=SLATE_DARK, lw=0.9)
    ax.text(6.18 + 0.39, 0.94, "Decode", ha='center', va='center',
            fontsize=6.8, fontweight='bold', color=SLATE_DARK, zorder=5)
    ax.text(6.18 + 0.39, 0.74, "Qwen-27B", ha='center', va='center',
            fontsize=5.8, color=SLATE_MID, zorder=5)
    ax.text(6.18 + 0.39, 0.57, "35.3 tok/s", ha='center', va='center',
            fontsize=5.4, color=SLATE_MUTED, zorder=5)

    # Intra-Node A arrows
    ax.annotate("", xy=(5.04, 0.74), xytext=(4.88, 0.74),
                arrowprops=dict(arrowstyle="-|>", mutation_scale=8, linewidth=0.9, color=RUST_BORDER, zorder=5))
    ax.annotate("", xy=(6.18, 0.74), xytext=(6.04, 0.74),
                arrowprops=dict(arrowstyle="-|>", mutation_scale=8, linewidth=0.8, color=SLATE_DARK, zorder=5))

    # -------------------------------------------------------------------------
    # 3. INTER-TIER DATAFLOW (AUTOMATICALLY MASKED PILL BADGES)
    # -------------------------------------------------------------------------
    corridor_center = (1.67 + 3.48) / 2  # 2.575 in

    # Highway 1: User Query (Node C Ingress -> Node B Dense Encoder)
    # Continuous blue arrow at y = 1.97 (zorder=4)
    ax.annotate("", xy=(3.48, 1.97), xytext=(1.67, 1.97),
                arrowprops=dict(arrowstyle="-|>", mutation_scale=9, linewidth=1.1, color=BLUE_DARK, zorder=4))
    # Pill badge (zorder=6) automatically masks the line behind it
    ax.text(corridor_center, 1.97, "Query $q$ (Link C)", ha='center', va='center',
            fontsize=6.5, fontweight='bold', color=BLUE_DARK, zorder=6,
            bbox=dict(facecolor=WHITE, edgecolor=BLUE_BORDER, boxstyle='round,pad=0.22', lw=0.7))

    # Highway 2: SPHP Early Hint (Node C BM25 -> Node A Speculative Prefill)
    # Continuous dashed terracotta arrow from (1.67, 1.20) to (3.48, 0.94) (zorder=4)
    ax.annotate("", xy=(3.48, 0.94), xytext=(1.67, 1.20),
                arrowprops=dict(arrowstyle="-|>", mutation_scale=10, linewidth=1.3,
                                linestyle="--", color=RUST_DARK, zorder=4))
    # Compact 2-line pill badge (zorder=6) at midpoint (2.575, 1.07) automatically masks line
    ax.text(corridor_center, 1.07, "Hint $\\mathcal{H}$ (104 B)\n[Fast Path]",
            ha='center', va='center', fontsize=5.8, fontweight='bold', color=RUST_DARK, zorder=6,
            bbox=dict(facecolor=WHITE, edgecolor=RUST_BORDER, boxstyle='round,pad=0.22', lw=0.8))

    # Highway 3: Verified Context F (Node B RRF -> Node A Verification Barrier)
    # Continuous blue vertical arrow from (5.90, 1.74) to (5.90, 1.06) (zorder=4)
    ax.annotate("", xy=(5.90, 1.06), xytext=(5.90, 1.74),
                arrowprops=dict(arrowstyle="-|>", mutation_scale=8, linewidth=1.1, color=BLUE_DARK, zorder=4))
    # Pill badge (zorder=6) centered at (5.90, 1.40) in the wide open 0.46-in gap
    ax.text(5.90, 1.40, "Context $\\mathcal{F}$ (144 B · Link B)",
            ha='center', va='center', fontsize=5.6, fontweight='bold', color=BLUE_DARK, zorder=6,
            bbox=dict(facecolor=WHITE, edgecolor=BLUE_BORDER, boxstyle='round,pad=0.20', lw=0.7))

    # Highway 4: Continuous SSE Token Stream Return (Node A Decode -> Node C Response Sink)
    # Continuous dashed line along bottom (zorder=4)
    ax.plot([6.57, 6.57, 0.945, 0.945], [0.50, 0.20, 0.20, 0.48],
            linestyle="--", color=SLATE_DARK, linewidth=0.9, zorder=4)
    ax.annotate("", xy=(0.945, 0.48), xytext=(0.945, 0.38),
                arrowprops=dict(arrowstyle="-|>", mutation_scale=7, linewidth=0.9, color=SLATE_DARK, zorder=4))

    # Badge: Continuous Token Stream Delivery at (3.74, 0.20) (zorder=6) masks line
    ax.text(3.74, 0.20, "Continuous Token Stream Return (35.3 tok/s)",
            ha='center', va='center', fontsize=6.2, fontweight='bold', color=SLATE_DARK, zorder=6,
            bbox=dict(facecolor=WHITE, edgecolor=CARD_BORDER, boxstyle='round,pad=0.25', lw=0.6))

    out_pdf = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fig1_arch.pdf")
    plt.savefig(out_pdf, format="pdf", bbox_inches='tight', pad_inches=0.02)
    plt.close()
    print("Clean Fig 1 generated at:", out_pdf)

if __name__ == '__main__':
    generate_publication_fig1()
