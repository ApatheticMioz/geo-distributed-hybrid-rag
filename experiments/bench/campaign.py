#!/usr/bin/env python3
"""
Final-matrix campaign runner for the Node B hybrid RAG gateway.

Sweeps the paper's final matrix:

    (rtt, loss)  x  mode  x  variant  x  query  x  repeat

where:
  * (rtt, loss) is the WAN regime, shaped via ``scripts/netem/apply.sh``
    (and cleared with ``clear.sh``) so every hop of the gateway -> Node A
    path is shaped, not just an application-level header.  The baseline
    regime ``(rtt=0, loss=0)`` runs unshaped.
  * mode is the retrieval mode (``hybrid`` | ``dense`` | ``sparse``).
  * variant is the SPHP axis: with ``--sphp-axis``, mode=hybrid runs two
    variants per tier (``baseline`` sphp=false and ``sphp`` sphp=true);
    dense/sparse run once (no variant).
  * query is a per-arm disjoint sample.  A pool of n_arms*(N+W) distinct
    queries is drawn from the queries file with a seeded RNG, and arm i is
    handed the disjoint slice pool[i*(N+W):(i+1)*(N+W)].  The first W of each
    slice are that arm's warmups (executed but NOT recorded); the remaining N
    are measured.  Because every arm gets a disjoint slice, no query is ever
    seen by two arms, so no arm's repeat-0 prefixes are pre-cached by an
    earlier arm (repeat 0 stays a true cold reference).
  * repeat is the back-to-back repeat index (0..R-1) per measured query.
    No cache is flushed (the engine has no flush endpoint), so repeat 0 is
    the cold-prefix reference and repeats 1..R-1 are warm.

For every measured query execution the runner:
  * POSTs to the gateway ``/query/benchmark`` endpoint,
  * appends a full-timing JSONL record to ``<out-dir>/campaign_<run_id>.jsonl``.

At the end it writes:
  * ``<out-dir>/summary_<run_id>.csv``  (per (tier, mode, variant) summary)
  * ``<out-dir>/summary_<run_id>.json`` (same summary + run metadata)

The runner is intentionally stdlib-only so it can run from the orchestration
host against the live gateway.  ``--dry-run`` prints the plan only: no netem,
no HTTP, no output files.

Usage (v1 — the paper's final matrix: 4 WAN regimes x SPHP on/off, hybrid mode):
    python3 experiments/bench/campaign.py \
        --gateway-url http://10.8.0.2:8000 \
        --queries experiments/bench/queries/latency_dev480_seed42.txt \
        --rtt-tiers 0 15 40 80 \
        --loss-tiers 0 \
        --modes hybrid \
        --sphp-axis \
        --query-sample 50 --seed 42 \
        --warmups 5 --repeats 3 \
        --out-dir experiments/bench/campaigns

    # Plan only (no netem, no HTTP, no files):
    python3 experiments/bench/campaign.py --dry-run

Usage (Campaign v2 — confirmatory 2-node matrix, per
``docs/campaign_v2_design.md`` @ 1f911ef):
    python3 experiments/bench/campaign.py \
        --pool experiments/bench/queries/campaign_v2_master5000_seed42.txt \
        --dry-run

Usage (Campaign v2 — WS-CM 3-node matrix, design §2.2/§3.2):
    # Plan only (no netem, no HTTP, no files):
    python3 experiments/bench/campaign.py \
        --run three-node \
        --pool experiments/bench/queries/campaign_v2_master5000_seed42.txt \
        --dry-run

    # Real run (defaults in --pool mode implement the design: RTT
    # {0,15,40,80} x loss {0} x {P2, P2-SPHP}, N=120/arm, W=10, R=3,
    # arms consuming lines 1..1040 of the pool, per-tier 11-ping fidelity
    # gates, 0-error tier assertion):
    python3 experiments/bench/campaign.py \
        --pool experiments/bench/queries/campaign_v2_master5000_seed42.txt

``--pool FILE`` (mutually exclusive with ``--queries``) activates v2 mode:
the pool file is verified against its ``FILE.manifest.json`` sidecar
(sha256 + n + line_count must all match — fail fast), arm query slices are
the design's fixed line-ranges of the pool file (contiguous, arm-ordered,
disjoint by construction — the pool file itself is the seeded shuffle),
shaping uses the explicit-link contract (``apply.sh --link-b <RTT> <LOSS>``,
``validate.sh --link-b <RTT>`` — no positional args), and the driver writes
``<out-dir>/campaign_v2_manifest.json`` before any request.

The three-node run (``--run three-node``) implements the design's WS-CM
3-node matrix: 3 per-leg (B, C) cells x {3node-P2, 3node-SPHP-from-C} =
6 arms, N = 80/arm, W = 10, R = 3, consuming pool lines 1371..1910
(design §5.1).  Both the A<->B (``--link-b``) and A<->C (``--link-c``) legs
are shaped per cell, each with its own pre- and post-cell 11-ping fidelity
gate.  Queries enter at Node C (the user-facing gateway).  The RQ2 dropout
run type remains a WS-CM extension point (see ``V2_RUN_SPECS``).

The genuine-WAN run (``--run genuine-wan``) implements the CM-4
characterization leg (design §3.3/§4.3/§5.1, finalized in b239833): the
TWO real cross-site paths the mesh traverses — the 2-node path (client ->
Node B, ``10.8.0.2``) and the 3-node path (client -> Node C, ``10.8.0.3``,
the user-facing gateway) — each with its two placement arms
(2-node = {P2, P2-SPHP}; 3-node = {3node-P2, 3node-SPHP-from-C}) = **4
arms**, N = 50 / W = 5 / R = 1 -> 4 arms x (50 + 5) = **220 pool lines ->
band 2701..2920** (fits with ZERO slack, §5.1).  **Netem OFF**: before the
first request the driver runs ``clear.sh`` then verifies via tc-qdisc
readback (the same readback tooling the fidelity gate uses) that NO
netem/htb shaping remains — the unshaped real path IS the subject of this
leg, so there is no §3.1-style added-RTT/loss fidelity gate; a dirty
readback ABORTS the run before any request.  A 20-request unshaped sanity
pre-flight (free pool tail 3839.., 0-error gate — abort on any error)
confirms the link is healthy.  Each measured record carries a real-path
RTT probe (``rtt_probe_ms``, a TCP-connect round trip to the cell's
gateway service port — the established RTT proxy, since ICMP ping is
blocked in the sandbox).  The deliverable is the **emulation-fidelity
table** (``build_fidelity_table``): a deterministic post-pass over the
measured records reporting per-arm real-path RTT p50/p95 and TTFT
p50/p95, optionally alongside the cost-model predictions for the nearest
shaped regime (the driver records raw stats only; design §3.3 leaves the
cost-model wiring to the analysis layer).  Characterization only — BH
secondary, not in any Holm/BH confirmatory family.

The three-node stress run (``--run three-node-stress``) implements the CM-5
per-leg loss/RTT stress matrix (design §3.4, finalized in b239833): the 27
per-leg float-loss cells — three loss tiers {0.5, 1, 2}%% x three
leg-placements (B leg / C leg / both) x three (B, C) RTT cells
(15,15)/(40,15)/(80,15), with the C leg frozen at 15 ms — crossed with the
two 3-node arms {3node-P2, 3node-SPHP-from-C} = 54 arms, at N = 15 /
W = 2 / R = 1 (design §4.3) -> 54 arms x (15 + 2) = 918 pool lines -> pool
band 2921..3838 (design §5.1 final allocation; the band fits with ZERO
slack and is disjoint from every other registered band).  Descriptive /
BH secondary — no confirmatory claims.  Each cell shapes BOTH legs
independently via ``apply.sh --link-b <RTT_B> <LOSS_B> --link-c
<RTT_C> <LOSS_C>`` (float loss — the fractional-loss netem fix lands in the
sibling commit; the driver depends on that script interface, it does not
re-implement the qdisc logic).  A per-leg 11-ping ±30%% added-RTT fidelity
gate (with the script's loss readback) runs pre- AND post- every cell; the
(0,0) unshaped sanity pre-flight (20 requests from the FREE pool tail
3839.., 0-error gate — abort on any error) confirms the link is healthy
before loss is applied; the shaped cells REPORT their per-cell error rate
WITHOUT aborting (loss-driven errors are the signal, not a campaign
failure).  The summary carries the key deliverable: per-cell SPHP-from-C
hit rate with a Wilson 95%% CI, imported from
``experiments/analysis/stats.py::wilson_ci`` (not re-implemented).

The loss-sweep run (``--run loss-sweep``) implements the amended design's
H4 exploratory sweep (design §3.1, amended 2026-10-14): the 2-node / link-B
matrix **RTT {0, 40} x loss {0.5, 1, 2} = 6 cells** (loss applied on the
A<->B link only), with BOTH headline arms ``P2`` (variant=baseline) and
``P2-SPHP`` (variant=sphp) per cell = **12 arms**, at **full generation**
(hit rate *and* TTFT/E2E recorded per record).  Sizing N = 50 / W = 5 /
R = 1 (design §4.3) -> 12 arms x (50 + 5) = **660 pool lines -> pool band
2041..2700** (the pre-amendment 1041..1370 band is now the reserved/unused
gap, §5.1).  Gate semantics are per-cell (frozen): every cell is SHAPED
(there is no pure-0 baseline cell in this run) — the loss-0 *sanity
pre-flight* (a small 0-error sample per RTT tier, drawn from the free pool
tail) **ABORTS the campaign on any error** (the shaped link must be healthy
before loss is applied), while the loss>0 measured cells **report their
per-cell error rate without aborting** (loss-driven errors are the signal,
not a campaign failure).  The per-cell fidelity gate is the ``validate.sh``
11-ping ±30%% added-RTT window (N/A at RTT = 0, where only the tc-based
fractional-loss readback applies) **plus the tc-based fractional-loss
readback**, run pre- AND post- every cell.  The summary/manifest carry the
key deliverable: the per-cell {rtt, loss} targets + the per-cell 11-ping
median, and the per-cell SPHP hit rate with a Wilson 95%% CI (lazily
imported from ``experiments/analysis/stats.py::wilson_ci``; degrades to
None when the stats engine is not importable on a bare host).
"""

import argparse
import csv
import hashlib
import json
import math
import random
import re
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
# Note: v1 used _HERE.parent here, which silently pointed NETEM_DIR at a
# non-existent experiments/bench/scripts/netem (run_netem would then just
# report "script not found"); _HERE.parent.parent is the actual repo root.
PROJECT_ROOT = _HERE.parent.parent
NETEM_DIR = PROJECT_ROOT / "scripts" / "netem"

# Ensure the repo root is importable so the loss-sweep / three-node-stress
# runs can import the shared statistics engine
# (experiments.analysis.stats::wilson_ci) regardless of how this script is
# launched (as a module or as a file from any CWD).  Must come AFTER
# PROJECT_ROOT is defined.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
APPLY_SH = NETEM_DIR / "apply.sh"
CLEAR_SH = NETEM_DIR / "clear.sh"
VALIDATE_SH = NETEM_DIR / "validate.sh"

DESIGN_DOC = "docs/campaign_v2_design.md"
DESIGN_COMMIT = "1f911ef"

# The Wilson 95% CI for the loss-sweep per-cell SPHP hit-rate deliverable is
# IMPORTED from the analysis statistics engine (design §7: use stats.py's
# exact functions; do not reimplement).  ``experiments.analysis.stats``
# needs numpy/scipy at import time, and this driver is stdlib-only by
# design, so the import is deferred to first use (the pure functions below
# degrade gracefully to CI=None if the dependency is absent on a bare
# orchestration host — the driver itself still runs).
_wilson_ci: Optional[Callable[[int, int, float], Tuple[float, float]]] = None


def _get_wilson_ci() -> Optional[Callable[[int, int, float], Tuple[float, float]]]:
    """Lazily import ``stats.wilson_ci`` (z=1.96 default = 95%)."""
    global _wilson_ci
    if _wilson_ci is None:
        try:
            from experiments.analysis.stats import wilson_ci
        except ImportError:
            print("WARNING: experiments/analysis/stats.py is not importable "
                  "(numpy/scipy missing on this host); SPHP hit-rate Wilson "
                  "95% CIs will be reported as null.", file=sys.stderr)
            return None
        _wilson_ci = wilson_ci
    return _wilson_ci

VALID_MODES = ("hybrid", "sparse", "dense")

# Timing fields echoed by /query/benchmark (flattened into each JSONL record).
TIMING_FIELDS = (
    "sparse_ms", "dense_ms", "fusion_ms", "ttft_ms",
    "decode_ms", "total_ms", "simulated_wan_ms",
)
# Top-level response fields flattened into each JSONL record.
RESPONSE_FIELDS = ("token_count", "decode_tps")
# SPHP fields (present only when the sphp axis is exercised).
SPHP_FIELDS = ("sphp_hit", "sphp_overlap", "wasted_prefill_ms")

# ---------------------------------------------------------------------------
# v2: registered run types (design §5.1 line-range spec)
# ---------------------------------------------------------------------------
# Design-frozen pool line-ranges (1-based, inclusive start).  The end of each
# run's range is derived from the run's arms x (N + W).
#
#   "confirmatory":  lines 1..1040   (8 arms x (120+10) = 1040)  [implemented]
#   "loss-sweep":    lines 2041..2700 (12 arms x (50+5) = 660)   [implemented]
#                      + per-RTT-tier loss-0 sanity samples from free tail 3839..
#   "three-node":    lines 1371..1910 (6 arms x (80+10) = 540)   [implemented]
#   "three-node-stress": lines 2921..3838 (54 arms x (15+2) = 918)    [implemented]
#                      + (0,0) unshaped sanity pre-flight from free tail 3839..
#                      (the CM-5 band fits with zero slack — b239833 §5.1)
#   "dropout":       {"pool_line_start": 1911},  # 2 arms x (60+5)  = 130  [WS-CM, not yet registered]
#   "genuine_wan":   {"pool_line_start": 2041},  # 2 arms x (100+5) = 210  [WS-CM, not yet registered]
#
# The three-node run is the design's WS-CM 3-node matrix (§2.2/§3.2/§4.3/§5.1):
# 3 per-leg (B, C) cells x 2 placement arms.  Design-frozen parameters:
#   * cells (RTT_B, RTT_C) = {(15,15),(40,15),(80,15)} — B carries the
#     dense/generation legs, C the user-facing sparse leg; loss 0 on both
#     legs (all 3-node cells are shaped);
#   * arms per cell = {3node-P2 (mode=hybrid, variant=baseline),
#     3node-SPHP-from-C (mode=hybrid, variant=sphp)}; the 3node-SPHP-from-B
#     counterpart and the D-B/D-C dropout arms are WS-CM workstream scope
#     (design §2.2) and are not part of the 6-arm 3-node matrix;
#   * N = 80/arm, W = 10/arm, R = 3 (design §4.3 / §4.1).
# The 2-node confirmatory core keeps its own defaults (N=120, W=10, R=3,
# rtt tiers [0,15,40,80]); each run type resolves its own defaults.
V2_RUN_SPECS: Dict[str, Dict[str, Any]] = {
    "confirmatory": {
        "pool_line_start": 1,
        "label": "v2 confirmatory core (2-node)",
        "gateway_ip": "10.8.0.2",
        # (rtt, loss) regimes swept on link-b; rtt 0 = unshaped baseline.
        "cells": [(0, 0), (15, 0), (40, 0), (80, 0)],
        "variants": ["baseline", "sphp"],
        "n": 120, "warmups": 10, "repeats": 3,
    },
    # Amended-design H4 exploratory sweep (design §3.1/§4.3, amended
    # 2026-10-14): the 2-node / link-B matrix RTT {0, 40} x loss {0.5, 1, 2}
    # = 6 MEASURED cells (loss applied on the A<->B link only), BOTH headline
    # arms {P2 (baseline), P2-SPHP (sphp)} per cell = 12 arms, at FULL
    # generation (the old retrieval-only / P2-only reading is superseded).
    # N = 50 / W = 5 / R = 1 (design §4.3) -> 12 arms x (50 + 5) = 660 pool
    # lines -> pool band 2041..2700 (design §5.1 final allocation; disjoint
    # from confirmatory 1..1040, the reserved 1041..1370 gap, three-node
    # 1371..1910, and dropout 1911..2040).  The loss-0 sanity pre-flight
    # (one small 0-error sample per RTT tier, from the free pool tail)
    # ABORTS on any error; the loss>0 cells REPORT per-cell error rate
    # without aborting (loss-driven errors are the signal).
    "loss-sweep": {
        "pool_line_start": 2041,
        "label": "v2 loss sweep (2-node, amended design — RTT{0,40} x loss{0.5,1,2})",
        "gateway_ip": "10.8.0.2",
        # RTT tiers swept (loss is the swept variable within each tier).
        "rtt_tiers": [0, 40],
        # (rtt, loss) MEASURED cells; loss is a FLOAT percent (0.5).
        # Cell-major: the two RTT tiers, each with its three loss tiers.
        "cells": [(0, 0.5), (0, 1.0), (0, 2.0),
                  (40, 0.5), (40, 1.0), (40, 2.0)],
        # Per-RTT-tier loss-0 sanity pre-flight: a small 0-error sample from
        # the FREE pool tail (3839.., the design §5.1 spare) that never
        # touches the measured 2041..2700 band.  One block per RTT tier
        # (0 and 40); any error ABORTS the campaign before loss is applied.
        "sanity_n": 10, "sanity_warmups": 0, "sanity_repeats": 1,
        "sanity_pool_start": 3839,
        "variants": ["baseline", "sphp"],
        "n": 50, "warmups": 5, "repeats": 1,
    },
    "three-node": {
        "pool_line_start": 1371,
        "label": "WS-CM 3-node matrix",
        # Queries enter at the Node C gateway (user-facing tier; design §2.2).
        "gateway_ip": "10.8.0.3",
        # (rtt_b, rtt_c) per-leg cells (design §3.2); loss is 0 on both legs
        # for every 3-node cell (all cells are shaped).
        "cells": [(15, 15), (40, 15), (80, 15)],
        # The 2 arms of the design's 3-node matrix: 3node-P2 and
        # 3node-SPHP-from-C (the SPHP hint originates on the user-facing
        # Node C).  The 3node-SPHP-from-B counterpart is a WS-CM paired
        # contrast, not part of this matrix (design §2.2).
        "variants": ["baseline", "sphp"],
        "n": 80, "warmups": 10, "repeats": 3,
    },
    # CM-5 three-node per-leg loss/RTT stress matrix (final design §3.4,
    # finalized in b239833; descriptive / BH secondary — no confirmatory
    # claims).  The three per-leg float-loss tiers (0.5, 1, 2)%%
    # (B leg / C leg / both legs — the design's per-leg cell conventions)
    # crossed with the 3-node (B, C) RTT cells (15,15)/(40,15)/(80,15):
    # 3 RTT x 3 tiers x 3 leg-placements = 27 (B, C, lossB, lossC) cells
    # x 2 arms (3node-P2, 3node-SPHP-from-C) = 54 arms.  The C leg stays
    # at 15 ms (the user-facing sparse leg, design §3.2/§3.4); the B leg
    # carries the swept RTT.  N = 15/arm, W = 2, R = 1 (design §4.3)
    # — 54 arms x (15+2) = 54 x 17 = 918 pool lines -> 2921..3838 (the
    # FINAL §5.1 allocation; the band fits with ZERO slack and is
    # disjoint from confirmatory 1..1040, the reserved 1041..1370 gap,
    # three-node 1371..1910, dropout 1911..2040, loss-sweep 2041..2700,
    # and CM-4 genuine-WAN 2701..2920).  The (0,0) unshaped SANITY
    # pre-flight (20 requests from the FREE pool tail 3839..) ABORTS on
    # any error (the link must be healthy before loss is applied).  The
    # measured cells REPORT their per-cell error rate WITHOUT aborting
    # (loss-driven errors are the signal, not a campaign failure).  The
    # summary carries the key deliverable: per-cell SPHP-from-C hit rate
    # with a Wilson 95% CI (imported from stats.py, not reimplemented).
    "three-node-stress": {
        "pool_line_start": 2921,
        "label": "CM-5 3-node per-leg loss/RTT stress matrix",
        # Queries enter at the Node C gateway (user-facing tier; design §2.2).
        "gateway_ip": "10.8.0.3",
        # (rtt_b, rtt_c, loss_b, loss_c) per-leg cells; loss is a FLOAT
        # percent (0.5).  C is held at 15 ms (the user-facing sparse leg,
        # design §3.2); the B leg carries the swept RTT.  Each loss tier
        # (0.5, 1, 2)%% is applied to the B leg, the C leg, or BOTH legs
        # (the design's per-leg cell conventions).
        "cells": [
            (15, 15, 0.5, 0.0),
            (15, 15, 0.0, 0.5),
            (15, 15, 0.5, 0.5),
            (15, 15, 1.0, 0.0),
            (15, 15, 0.0, 1.0),
            (15, 15, 1.0, 1.0),
            (15, 15, 2.0, 0.0),
            (15, 15, 0.0, 2.0),
            (15, 15, 2.0, 2.0),
            (40, 15, 0.5, 0.0),
            (40, 15, 0.0, 0.5),
            (40, 15, 0.5, 0.5),
            (40, 15, 1.0, 0.0),
            (40, 15, 0.0, 1.0),
            (40, 15, 1.0, 1.0),
            (40, 15, 2.0, 0.0),
            (40, 15, 0.0, 2.0),
            (40, 15, 2.0, 2.0),
            (80, 15, 0.5, 0.0),
            (80, 15, 0.0, 0.5),
            (80, 15, 0.5, 0.5),
            (80, 15, 1.0, 0.0),
            (80, 15, 0.0, 1.0),
            (80, 15, 1.0, 1.0),
            (80, 15, 2.0, 0.0),
            (80, 15, 0.0, 2.0),
            (80, 15, 2.0, 2.0),
        ],
        "variants": ["baseline", "sphp"],
        "n": 15, "warmups": 2, "repeats": 1,
        # The (0,0) unshaped sanity gate: a 20-request 0-error pre-flight
        # sample from the FREE pool tail (3839.., the design §5.1 spare —
        # the same free-tail pattern the loss-sweep sanity pre-flight uses;
        # never touches the measured 2921..3838 band; the master pool has
        # 5,000 lines).  Any error aborts before loss is applied.
        "sanity_n": 20, "sanity_warmups": 0, "sanity_repeats": 1,
        "sanity_pool_start": 3839,
    },
    # CM-4 genuine-WAN characterization leg (final design §3.3/§4.3/§5.1,
    # finalized in b239833).  NOT a shaping run: netem is OFF for the whole
    # leg — the unshaped real cross-site path IS the subject of this
    # characterization (so there is no §3.1-style added-RTT / loss fidelity
    # gate here; the driver instead verifies a PRISTINE readback before the
    # first request and aborts if any shaping remains).  Two real paths the
    # mesh traverses, each run with its two placement arms (design §3.3,
    # amended 2026-10-14 — the pre-2026-10-14 reading was a single leg):
    #   * 2-node path: client -> Node B (10.8.0.2), arms {P2, P2-SPHP};
    #   * 3-node path: client -> Node C (10.8.0.3, the user-facing gateway),
    #     arms {3node-P2, 3node-SPHP-from-C}.
    # = 2 cells x 2 arms = 4 arms, N = 50 / W = 5 / R = 1 -> 4 x (50 + 5) =
    # 220 pool lines -> pool band 2701..2920 (design §5.1; fits with ZERO
    # slack and is disjoint from every other registered band).  A 20-request
    # unshaped sanity pre-flight (0-error gate) is drawn from the FREE pool
    # tail 3839.. (never touches the measured band).  Each measured record
    # carries a real-path RTT probe (rtt_probe_ms, a TCP connect to the
    # cell's gateway service port — the established RTT proxy, since ICMP
    # ping is blocked in the sandbox).  Characterization / descriptive only
    # (BH secondary) — never part of the Holm/BH confirmatory families; the
    # deliverable is the emulation-fidelity table (build_fidelity_table).
    "genuine-wan": {
        "pool_line_start": 2701,
        # First cell's gateway (Node B) — used only to seed the default
        # --gateway-url in resolve_mode_defaults; the runner queries each
        # cell's own gateway_ip below.
        "gateway_ip": "10.8.0.2",
        "label": "CM-4 genuine-WAN characterization (netem OFF, real path)",
        # Two real paths the mesh traverses; each is (cell_name, gateway_ip,
        # [arm_label, arm_label]).  The 2-node path queries the Node B
        # gateway; the 3-node path queries the Node C (user-facing) gateway
        # (design §2.2 / §3.3).  Arm labels follow the design's §2.2 keys
        # (P2 / P2-SPHP for 2-node; 3node-P2 / 3node-SPHP-from-C for 3-node).
        "cells": [
            ("2-node", "10.8.0.2", ["P2", "P2-SPHP"]),
            ("3-node", "10.8.0.3", ["3node-P2", "3node-SPHP-from-C"]),
        ],
        # Each arm's variant (mode is hybrid for every genuine-WAN arm;
        # baseline -> P2 / 3node-P2, sphp -> P2-SPHP / 3node-SPHP-from-C).
        # The variant list parallels cells (2 cells x 2 variants) so the
        # pool-slice selector lays out one arm per 55-line block.
        "variants": ["baseline", "sphp"],
        "n": 50, "warmups": 5, "repeats": 1,
        # The unshaped sanity pre-flight: a 20-request 0-error sample from
        # the FREE pool tail (3839.., the design §5.1 spare) confirming the
        # real link is healthy before the measured arms run.  Never touches
        # the measured 2701..2920 band (the master pool has 5,000 lines).
        "sanity_n": 20, "sanity_warmups": 0, "sanity_repeats": 1,
        "sanity_pool_start": 3839,
    },
}

V1_DEFAULT_QUERIES = "experiments/bench/queries/latency_dev480_seed42.txt"

# Design §5.1: the v2 pool file is the 5,000-line seeded master pool
# (all v2 run types share it; each run type consumes its own line-range
# slice).  Used as the default when `--pool` is not given for a v2 run type.
V2_DEFAULT_POOL = "experiments/bench/queries/campaign_v2_master5000_seed42.txt"


class ManifestError(Exception):
    """Pool file and its manifest sidecar disagree (or the sidecar is absent)."""


class CampaignAborted(Exception):
    """Fidelity-gate or 0-error-tier failure: stop the campaign, keep data."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sha256_file(path: str) -> str:
    """Streaming sha256 of a file (hex digest)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_queries(path: str) -> List[str]:
    """Load a one-query-per-line text file, skipping blank lines."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"queries file not found: {path}")
    queries: List[str] = []
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if line.strip():
                queries.append(line)
    return queries


def resolve_queries_path(path: str) -> str:
    """Resolve a queries path against CWD, falling back to the project root."""
    p = Path(path)
    if p.is_file():
        return str(p)
    alt = PROJECT_ROOT / path
    if alt.is_file():
        return str(alt)
    return path  # let load_queries raise a clear error


# ---------------------------------------------------------------------------
# v2: pool manifest verification (design §5.1)
# ---------------------------------------------------------------------------
def verify_pool_manifest(pool_path: str) -> Dict[str, Any]:
    """Verify a v2 pool file against its ``<pool>.manifest.json`` sidecar.

    Checks (all must pass; the first failure raises ``ManifestError``):
      1. the sidecar exists and is valid JSON with schema
         ``pdc.query_pool_manifest/v1`` and an ``output`` object;
      2. sha256(pool file) == manifest ``output.sha256``;
      3. manifest ``n`` == manifest ``output.line_count``;
      4. actual non-blank line count of the pool file == ``n``.

    Returns the parsed manifest dict (with ``"_verified_sha256"`` added).
    """
    p = Path(pool_path)
    man_path = p.with_name(p.name + ".manifest.json")
    if not man_path.is_file():
        raise ManifestError(
            f"pool manifest sidecar not found: {man_path} "
            f"(required for --pool usage; generate it with make_query_pool.py)")
    try:
        man = json.loads(man_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ManifestError(f"pool manifest is not valid JSON: {man_path}: {e}")
    if man.get("schema") != "pdc.query_pool_manifest/v1":
        raise ManifestError(
            f"pool manifest schema mismatch: {man.get('schema')!r} != "
            f"'pdc.query_pool_manifest/v1' ({man_path})")
    out = man.get("output")
    if not isinstance(out, dict):
        raise ManifestError(f"pool manifest missing 'output' object: {man_path}")

    actual_sha = sha256_file(str(p))
    if out.get("sha256") != actual_sha:
        raise ManifestError(
            f"pool sha256 mismatch for {p}: file={actual_sha} "
            f"manifest={out.get('sha256')} ({man_path})")

    n = man.get("n")
    line_count = out.get("line_count")
    if not isinstance(n, int) or not isinstance(line_count, int):
        raise ManifestError(f"pool manifest n/line_count must be ints: {man_path}")
    if n != line_count:
        raise ManifestError(
            f"pool manifest internally inconsistent: n={n} != line_count={line_count} "
            f"({man_path})")
    actual_lines = len(load_queries(str(p)))
    if actual_lines != n:
        raise ManifestError(
            f"pool line count mismatch for {p}: file has {actual_lines} "
            f"non-blank lines, manifest says n={n} ({man_path})")

    man["_verified_sha256"] = actual_sha
    return man


# ---------------------------------------------------------------------------
# v2: line-range slice selection (design §5.1 — fixed, arm-ordered ranges)
# ---------------------------------------------------------------------------
def _check_pool_range(
    all_queries: List[str],
    line_start: int,
    line_end: int,
    n_arms: int,
    n: int,
    warmups: int,
) -> List[str]:
    """Validate a (1-based, inclusive) pool line range and return its block.

    Shared by the 2-node and 3-node slice selectors so both run types carry
    identical fail-fast guards:
      * the range holds exactly ``n_arms * (n + warmups)`` lines,
      * the range is within the pool file's bounds,
      * the block holds no duplicate queries.

    Returns the ``line_end - line_start + 1`` line block (the pool file is
    the seeded shuffle, so it is consumed as-is, no re-shuffle).
    """
    per_arm = n + warmups
    required = line_end - line_start + 1
    if required != n_arms * per_arm:
        raise ValueError(
            f"line range [{line_start}, {line_end}] holds {required} lines but "
            f"{n_arms} arms x (N={n} + W={warmups}) = {n_arms * per_arm} are "
            f"required (recompute the range from the design spec)")
    available = len(all_queries)
    if line_start < 1 or line_end > available:
        raise ValueError(
            f"pool line range [{line_start}, {line_end}] out of bounds: "
            f"the pool file has {available} non-blank lines")

    block = all_queries[line_start - 1:line_end]
    if len(set(block)) != len(block):
        raise ValueError(
            f"duplicate queries inside pool range [{line_start}, {line_end}]: "
            f"the pool file must hold distinct queries (re-run the generator)")
    return block


def v2_line_range(run_name: str, n_arms: int, n: int, warmups: int) -> Tuple[int, int]:
    """Return the (1-based, inclusive) pool line range for a v2 run type.

    ``start`` is frozen per run type in ``V2_RUN_SPECS`` (design §5.1);
    ``end = start + n_arms * (n + warmups) - 1``.
    """
    spec = V2_RUN_SPECS.get(run_name)
    if spec is None:
        raise ValueError(
            f"unknown v2 run type {run_name!r}; registered run types: "
            f"{sorted(V2_RUN_SPECS)}. (The dropout run type is a WS-CM "
            f"extension point, not implemented in this driver.)")
    start = spec["pool_line_start"]
    end = start + n_arms * (n + warmups) - 1
    return start, end


def v2_sanity_line_ranges(run_name: str, rtt_tiers: List[int],
                          sanity_n: int) -> List[Tuple[int, int]]:
    """Pool line ranges for a run's per-RTT-tier loss-0 SANITY samples.

    Each RTT tier gets its own small pre-flight block (``sanity_n`` lines)
    drawn from the free pool tail starting at ``spec['sanity_pool_start']``,
    laid out contiguously, so it never touches the run's measured line
    window.  Returns one inclusive (1-based) (start, end) range per RTT
    tier.
    """
    spec = V2_RUN_SPECS.get(run_name)
    if spec is None or "sanity_pool_start" not in spec:
        raise ValueError(f"run type {run_name!r} has no sanity sample range")
    ranges: List[Tuple[int, int]] = []
    cursor = spec["sanity_pool_start"]
    for _ in rtt_tiers:
        ranges.append((cursor, cursor + sanity_n - 1))
        cursor += sanity_n
    return ranges


def assert_pool_ranges_disjoint(ranges: Dict[str, Tuple[int, int]]) -> None:
    """Assert a set of (1-based, inclusive) named pool line-ranges are
    pairwise disjoint (the design §5.1 disjointness contract across all
    registered run types).  Raises ``ValueError`` naming the first collision.
    """
    items = list(ranges.items())
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            (na, a1, a2) = (items[i][0], items[i][1][0], items[i][1][1])
            (nb, b1, b2) = (items[j][0], items[j][1][0], items[j][1][1])
            if not (a2 < b1 or b2 < a1):
                raise ValueError(
                    f"pool line-ranges collide: {na} [{a1}..{a2}] overlaps "
                    f"{nb} [{b1}..{b2}]")


def select_pool_slices(
    all_queries: List[str],
    arms: List[Tuple[int, int, str, str]],
    n: int,
    warmups: int,
    line_start: int,
    line_end: int,
) -> List[Dict[str, Any]]:
    """Assign each arm a DISJOINT contiguous slice of the pool's line range.

    The pool file is itself the seeded shuffle (generator's job); the driver
    consumes it in fixed, arm-ordered line ranges per the design §5.1
    spec, so no further shuffling happens here.  Arm ``i`` gets pool lines
    ``[line_start + i*(N+W) .. line_start + (i+1)*(N+W) - 1]`` (1-based); the
    first ``warmups`` lines of each slice are that arm's warmups (executed
    but not recorded), the remaining ``n`` are its measured sample.

    Fails fast (``ValueError``) if the range exceeds the file, if the slice
    contents contain duplicates, or if any two slices intersect (they cannot
    by construction — the disjointness assertion below is a belt-and-braces
    check feeding the manifest's disjointness field).

    Returns one dict per arm (in arm order) with the same keys as
    ``build_arm_query_plan`` plus nothing renamed.
    """
    block = _check_pool_range(all_queries, line_start, line_end,
                              len(arms), n, warmups)
    n_arms = len(arms)
    per_arm = n + warmups
    plan: List[Dict[str, Any]] = []
    for i, (rtt, loss, mode, variant) in enumerate(arms):
        start = (line_start - 1) + i * per_arm
        end = start + per_arm
        slice_ = block[i * per_arm:(i + 1) * per_arm]
        plan.append({
            "rtt_ms": rtt,
            "loss_pct": loss,
            "mode": mode,
            "variant": variant,
            "warmups": slice_[:warmups],
            "measured": slice_[warmups:end - start],
            "pool_start": start,
            "pool_end": end,
        })

    assert slices_disjoint(plan), "internal error: slices are not disjoint"
    return plan


def slices_disjoint(plan: List[Dict[str, Any]]) -> bool:
    """Pairwise disjointness assertion over an arm plan's query slices
    (warmups + measured, per design §5.1)."""
    sets = [set(a["warmups"]) | set(a["measured"]) for a in plan]
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            if sets[i] & sets[j]:
                return False
    return True


# ---------------------------------------------------------------------------
# v1: seeded shuffled-pool slice selection (unchanged)
# ---------------------------------------------------------------------------
def build_arm_query_plan(
    all_queries: List[str],
    arms: List[Tuple[int, int, str, str]],
    n: int,
    warmups: int,
    seed: int,
) -> List[Dict[str, Any]]:
    """Assign each arm a DISJOINT slice of a seeded query pool.

    A pool of ``len(arms) * (n + warmups)`` distinct queries is drawn from
    ``all_queries`` with ``random.Random(seed)`` (a seeded shuffle, so the
    assignment is reproducible across reruns).  Arm ``i`` is handed the
    disjoint slice ``pool[i*(n+warmups) : (i+1)*(n+warmups)]``; the first
    ``warmups`` of that slice are the arm's warmups (executed but not
    recorded) and the remaining ``n`` are the measured sample.

    Because every arm receives a disjoint slice, no query is ever seen by two
    arms, so no arm's repeat-0 prefixes are pre-cached by an earlier arm.

    Fails fast (raises ``ValueError``) if the query file holds fewer than
    ``len(arms) * (n + warmups)`` distinct queries.

    Returns one dict per arm (in arm order) with keys:
      ``rtt_ms, loss_pct, mode, variant, warmups, measured,
      pool_start, pool_end``
    where ``warmups``/``measured`` are the query lists and ``pool_start``/
    ``pool_end`` are the half-open index range into the shuffled pool the arm
    consumes (for dry-run auditability).
    """
    n_arms = len(arms)
    per_arm = n + warmups
    required = n_arms * per_arm
    available = len(all_queries)
    if available < required:
        raise ValueError(
            f"not enough distinct queries for per-arm disjoint sampling: "
            f"required {required} (n_arms={n_arms} x (N={n} + W={warmups})), "
            f"but the queries file has only {available}. "
            f"Add at least {required - available} more distinct queries to the "
            f"file, or lower --query-sample/--warmups."
        )

    rng = random.Random(seed)
    pool = list(all_queries)
    rng.shuffle(pool)

    plan: List[Dict[str, Any]] = []
    for i, (rtt, loss, mode, variant) in enumerate(arms):
        start = i * per_arm
        end = start + per_arm
        slice_ = pool[start:end]
        plan.append({
            "rtt_ms": rtt,
            "loss_pct": loss,
            "mode": mode,
            "variant": variant,
            "warmups": slice_[:warmups],
            "measured": slice_[warmups:end],
            "pool_start": start,
            "pool_end": end,
        })
    return plan


def percentile(values: List[float], p: float) -> float:
    """Linear-interpolation percentile (p in 0..100)."""
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    if n == 1:
        return float(s[0])
    idx = (p / 100.0) * (n - 1)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return float(s[lo])
    frac = idx - lo
    return float(s[lo] * (1.0 - frac) + s[hi] * frac)


def mean_std(values: List[float]) -> Tuple[float, float]:
    """Return (mean, population std) for a list of values."""
    if not values:
        return 0.0, 0.0
    n = len(values)
    m = sum(values) / n
    if n < 2:
        return m, 0.0
    var = sum((x - m) ** 2 for x in values) / n
    return m, math.sqrt(var)


# ---------------------------------------------------------------------------
# netem
# ---------------------------------------------------------------------------
def run_netem(script: Path, *args: str,
              timeout: int = 60) -> Tuple[bool, str]:
    """Run a netem shell script; return (ok, combined_output)."""
    if not script.is_file():
        return False, f"script not found: {script}"
    cmd = ["bash", str(script)] + [str(a) for a in args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        combined = "\n".join(x for x in (out, err) if x)
        return proc.returncode == 0, combined
    except Exception as e:  # timeout, missing bash, etc.
        return False, f"exception: {e}"


def _fmt_loss(loss: float) -> str:
    """Render a loss percent for the netem CLI: integer tiers as bare
    integers (apply.sh's current contract), fractional tiers as e.g.
    '0.5'."""
    if float(loss).is_integer():
        return str(int(loss))
    return str(loss)


def apply_netem_link_b(rtt: int, loss: float, dry_run: bool) -> bool:
    """Apply the 2-node (link-B) shaping regime via the explicit-link
    contract: ``apply.sh --link-b <RTT> <LOSS>`` (no positional args — the
    current apply.sh forbids them).

    ``loss`` may be a fractional percent (e.g. 0.5) for the amended-design
    loss sweep; integer-valued tiers are rendered without a decimal point
    (``0`` rather than ``0.0``) to stay compatible with the existing
    integer-only ``apply.sh`` validator.  Skipped for dry-run or the
    unshaped baseline (rtt=0 and loss=0).  Returns True only if the qdisc
    was actually applied.
    """
    if dry_run or (rtt == 0 and loss == 0):
        return False
    ok, out = run_netem(APPLY_SH, "--link-b", rtt, _fmt_loss(loss))
    if ok:
        print(f"    [netem] applied --link-b rtt={rtt}ms loss={_fmt_loss(loss)}%")
    else:
        print(f"    [netem] WARNING: apply.sh failed (rtt={rtt} loss={_fmt_loss(loss)}%): {out}",
              file=sys.stderr)
    return ok


def clear_netem(dry_run: bool) -> None:
    """Remove any active shaping qdisc (no-op for dry-run)."""
    if dry_run:
        return
    ok, out = run_netem(CLEAR_SH)
    if ok:
        print("    [netem] cleared")
    else:
        print(f"    [netem] WARNING: clear.sh failed: {out}", file=sys.stderr)


def apply_netem_3node(rtt_b: int, loss_b: float, rtt_c: int, loss_c: float,
                      dry_run: bool) -> bool:
    """Apply the 3-node per-leg shaping regime (design §3.2).

    ``apply.sh --link-b <RTT_B> <LOSS_B> --link-c <RTT_C> <LOSS_C>`` shapes
    the A<->B and A<->C legs independently in one call (the explicit-link
    contract; C<->B traffic relays through A).  ``rtt_c=0``/``loss_c=0``
    means the C leg is left on the unshaped default class.  Loss values may
    be fractional percents (e.g. 0.5) for the three-node-stress run; they
    are rendered via ``_fmt_loss`` (integer-valued tiers as bare integers)
    to stay compatible with the sibling-commit fractional-loss ``apply.sh``.
    Skipped for dry-run.  Returns True only if apply.sh succeeded.
    """
    if dry_run:
        return False
    fb, fc = _fmt_loss(loss_b), _fmt_loss(loss_c)
    ok, out = run_netem(APPLY_SH,
                        "--link-b", rtt_b, fb,
                        "--link-c", rtt_c, fc)
    if ok:
        print(f"    [netem] applied --link-b rtt={rtt_b}ms loss={fb}% "
              f"--link-c rtt={rtt_c}ms loss={fc}%")
    else:
        print(f"    [netem] WARNING: apply.sh failed "
              f"(--link-b {rtt_b} {fb} --link-c {rtt_c} {fc}): {out}",
              file=sys.stderr)
    return ok


def run_fidelity_gate_3node(rtt_b: int, loss_b: float, rtt_c: int,
                            loss_c: float, when: str,
                            dry_run: bool) -> List[Dict[str, Any]]:
    """Per-leg 11-ping fidelity gate for a 3-node cell (design §3.2/§6).

    Validates EACH shaped leg independently — ``validate.sh --link-b
    <RTT_B> [LOSS_B]`` and ``validate.sh --link-c <RTT_C> [LOSS_C]`` — so
    the ±30% added-RTT window is enforced per leg, not across a merged ping
    set.  A 0-RTT leg (unshaped) is recorded as ``skipped-baseline``.  When a
    loss value is supplied the sibling-commit ``validate.sh`` additionally
    enforces the per-leg loss readback (loss/2 per direction); the readback
    outcome is parsed from the script output into ``loss_gate`` and recorded
    on each shaped-leg record (the driver depends on the script interface —
    it does not re-implement the qdisc logic).  ``when`` is "pre" (before the
    cell's arms) or "post" (at cell close).  Returns one record per shaped
    leg, in [B, C] order.
    """
    if dry_run:
        return [{"link": "b", "rtt_ms": rtt_b, "loss_pct": loss_b, "when": when,
                 "status": "planned" if rtt_b > 0 else "skipped-baseline"},
                {"link": "c", "rtt_ms": rtt_c, "loss_pct": loss_c, "when": when,
                 "status": "planned" if rtt_c > 0 else "skipped-baseline"}]
    out: List[Dict[str, Any]] = []
    for link, rtt, loss in (("b", rtt_b, loss_b), ("c", rtt_c, loss_c)):
        if rtt <= 0:
            out.append({"link": link, "rtt_ms": rtt, "loss_pct": loss,
                        "when": when, "status": "skipped-baseline",
                        "baseline_median_ms": None,
                        "shaped_median_ms": None, "added_rtt_ms": None,
                        "loss_gate": None, "output_tail": ""})
            continue
        # The fractional-loss netem fix (sibling commit) makes validate.sh
        # run the loss readback when a LOSS_PCT is passed; depend on it.
        validate_args = [f"--link-{link}", str(rtt)]
        if loss:
            validate_args.append(_fmt_loss(loss))
        ok, gate_out = run_netem(VALIDATE_SH, *validate_args, timeout=300)
        parsed = parse_fidelity_gate_output(gate_out)
        out.append({"link": link,
                    "rtt_ms": rtt,
                    "loss_pct": loss,
                    "when": when,
                    "status": "PASS" if ok else "FAIL",
                    "loss_gate": _loss_gate_from_output(gate_out),
                    **parsed,
                    "output_tail": (gate_out or "")[-500:]})
    return out


# ---------------------------------------------------------------------------
# v2: fidelity gate (design §6 — 11-ping median, ±30% added-RTT window)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# v2 loss-sweep: loss readback (tc qdisc state of the shaped link)
# ---------------------------------------------------------------------------
def readback_loss_pct(dry_run: bool) -> Optional[float]:
    """Read back the applied per-direction loss fraction from the tc
    qdisc state (the loss-sweep's loss-readback fidelity check).

    Runs ``tc -s qdisc show`` (no sudo; the driver process can read tc) and
    extracts the netem ``loss X%`` leaf for the shaped interface.  Returns
    the per-direction loss percentage as a float, or None if it could not
    be read.  The netem script halves the full-round-trip target per
    direction, so the readback is the per-direction value; the caller
    compares it against ``target/2``.
    """
    if dry_run:
        return None
    try:
        proc = subprocess.run(
            ["tc", "-s", "qdisc", "show"],
            capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            return None
        m = re.search(r"loss\s+([0-9]+(?:\.[0-9]+)?)\s*%", proc.stdout)
        if not m:
            return None
        return float(m.group(1))
    except Exception:
        return None


def stratum_for_repeat(repeat_index: int) -> str:
    """Design §5.2: repeat 0 = cold-prefix stratum, repeats 1..R-1 = warm."""
    return "cold" if repeat_index == 0 else "warm"


def parse_fidelity_gate_output(out: str) -> Dict[str, Optional[float]]:
    """Extract the medians from ``validate.sh`` output (pure; '' -> zeros)."""
    def _g(pat: str) -> Optional[float]:
        m = re.search(pat, out)
        return float(m.group(1)) if m else None
    return {
        "baseline_median_ms": _g(r"Baseline median RTT\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*ms"),
        "shaped_median_ms": _g(r"Shaped median RTT\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*ms"),
        "added_rtt_ms": _g(r"Added RTT \(Observed\):\s*([0-9]+(?:\.[0-9]+)?)\s*ms"),
    }


def run_fidelity_gate(rtt: int, when: str, dry_run: bool,
                      loss: float = 0) -> Dict[str, Any]:
    """Run the per-cell 11-ping + fractional-loss fidelity gate for a
    2-node link-B regime.

    For ``rtt > 0``: ``validate.sh --link-b <RTT> [LOSS]`` clears, measures
    the baseline median, re-applies the shaping, measures the shaped
    median, enforces the ±30% window on the added RTT, and (when ``loss``
    is non-zero) additionally performs the tc-based fractional-loss
    readback (loss/2 per direction, ±0.05 pp).  The script's trap clears
    all netem on exit — the caller re-applies afterwards.

    For ``rtt == 0`` with ``loss > 0`` (the loss-only cells): ``validate.sh``
    requires RTT >= 1, so the gate instead applies ``--link-b 0 <loss>`` and
    reads back the tc qdisc state directly (``readback_loss_pct``), checking
    the per-direction loss matches target/2 within 0.05 pp.

    ``when`` is "pre" (before the cell's arms) or "post" (at cell close).
    """
    if dry_run:
        # rtt > 0: preserve the historical exact shape (no loss_pct key) so
        # downstream consumers / tests that compare the full dict are
        # unaffected by the loss-only extension.
        if rtt > 0:
            return {"rtt_ms": rtt, "when": when,
                    "status": "planned"}
        # rtt == 0: the only gate that can run is the loss readback; a
        # pure-0 baseline has no gate at all.
        if loss > 0:
            return {"rtt_ms": rtt, "loss_pct": loss, "when": when,
                    "status": "planned-rtt0-loss"}
        return {"rtt_ms": rtt, "loss_pct": loss, "when": when,
                "status": "skipped-baseline"}
    if rtt <= 0:
        if loss <= 0:
            return {"rtt_ms": rtt, "loss_pct": loss, "when": when,
                    "status": "skipped-baseline",
                    "baseline_median_ms": None, "shaped_median_ms": None,
                    "added_rtt_ms": None, "loss_gate": None,
                    "output_tail": ""}
        # rtt-0 / loss>0: apply then read back the tc qdisc state directly.
        apply_netem_link_b(rtt, loss, dry_run=False)
        rb = readback_loss_pct(dry_run=False)
        expected_half = loss / 2
        ok = (rb is not None and abs(rb - expected_half) <= 0.05)
        return {"rtt_ms": rtt, "loss_pct": loss, "when": when,
                "status": "PASS" if ok else "FAIL",
                "baseline_median_ms": None, "shaped_median_ms": None,
                "added_rtt_ms": None, "loss_gate": "PASS" if ok else "FAIL",
                "output_tail": (f"tc readback={rb}% expected~{expected_half}%")[-500:]}
    validate_args = [f"--link-b", str(rtt)]
    if loss > 0:
        validate_args.append(_fmt_loss(loss))
    ok, out = run_netem(VALIDATE_SH, *validate_args, timeout=300)
    parsed = parse_fidelity_gate_output(out)
    rec: Dict[str, Any] = {
        "rtt_ms": rtt,
        "loss_pct": loss,
        "when": when,
        "status": "PASS" if ok else "FAIL",
        **parsed,
        "loss_gate": _loss_gate_from_output(out),
        "output_tail": (out or "")[-500:],
    }
    return rec


# ---------------------------------------------------------------------------
# v2: campaign manifest (design §5.1 — written before any request)
# ---------------------------------------------------------------------------
def build_campaign_manifest(
    run_id: str,
    run_name: str,
    pool_path: str,
    manifest: Dict[str, Any],
    line_start: int,
    line_end: int,
    plan: List[Dict[str, Any]],
    n: int,
    warmups: int,
    repeats: int,
    seed: int,
) -> Dict[str, Any]:
    """Build the ``campaign_v2_manifest.json`` document (design §5.1)."""
    disjoint = slices_disjoint(plan)
    return {
        "schema": "pdc.campaign_manifest/v2",
        "design_doc": DESIGN_DOC,
        "design_commit": DESIGN_COMMIT,
        "run_id": run_id,
        "generated_at_utc": _now_iso(),
        "run": run_name,
        "pool": {
            "path": pool_path,
            "sha256": manifest.get("_verified_sha256"),
            "manifest_path": str(Path(pool_path).with_name(
                Path(pool_path).name + ".manifest.json")),
            "n": manifest.get("n"),
            "line_start": line_start,
            "line_end": line_end,
        },
        "seed": seed,
        "n_per_arm": n,
        "warmups_per_arm": warmups,
        "repeats_per_query": repeats,
        "arms": [
            {
                "rtt_ms": a["rtt_ms"],
                "loss_pct": a["loss_pct"],
                "mode": a["mode"],
                "variant": a["variant"],
                "pool_start": a["pool_start"] + 1,   # 1-based line numbers
                "pool_end": a["pool_end"],
                "warmups": a["warmups"],
                "measured": a["measured"],
            } for a in plan
        ],
        "disjointness": {
            "asserted": disjoint,
            "n_slices": len(plan),
            "pairwise_intersection_size": 0 if disjoint else -1,
        },
    }


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def http_post_json(url: str, payload: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    """POST JSON to ``url`` and return the parsed JSON response."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check_health(gateway: str, timeout: int = 15) -> bool:
    url = gateway.rstrip("/") + "/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Final-matrix campaign runner for the Node B hybrid RAG "
                    "gateway (v1 matrix + Campaign v2 confirmatory mode via "
                    "--pool).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--gateway-url", default="http://10.8.0.2:8000",
                    help="Node B gateway base URL")
    ap.add_argument("--queries", default=V1_DEFAULT_QUERIES,
                    help="v1 mode: path to a one-query-per-line text file "
                         "(mutually exclusive with --pool)")
    ap.add_argument("--pool", default=None, metavar="FILE",
                    help="v2 mode (Campaign v2, design docs/campaign_v2_design.md): "
                         "a query-pool file with a .manifest.json sidecar "
                         "(sha256 + n verified at startup; arm slices are the "
                         "design's fixed line-ranges, not a seeded shuffle)")
    ap.add_argument("--run", default=None, choices=sorted(V2_RUN_SPECS),
                    help="v2 run type (activates v2 mode when given, with or "
                         "without --pool): 'confirmatory' (2-node confirmatory "
                         "core), 'three-node' (WS-CM 3-node matrix), "
                         "'loss-sweep', 'three-node-stress', or "
                         "'genuine-wan' (CM-4, netem OFF). 'dropout' is a "
                         "WS-CM extension point, not registered. Default: v1 "
                         "mode unless --pool is given (then "
                         "'confirmatory').")
    ap.add_argument("--rtt-tiers", nargs="+", type=int,
                    default=[0, 15, 40, 80],
                    help="Full-round-trip RTT tiers (ms) to sweep (paper tiers)")
    ap.add_argument("--loss-tiers", nargs="+", type=int, default=None,
                    help="Packet-loss tiers (%%) to sweep, integer-valued "
                         "(default: v1 [0, 1]; v2 [0] — the confirmatory core "
                         "is loss-free). Use --loss-tiers-float for "
                         "fractional percents (e.g. the loss-sweep's 0.5%%).")
    ap.add_argument("--loss-tiers-float", nargs="+", type=float, default=None,
                    dest="loss_tiers_float",
                    help="Packet-loss tiers (%%) as floats — accepts "
                         "fractional percents (0.5 / 1 / 2) used by the "
                         "amended-design loss sweep. Mutually exclusive with "
                         "--loss-tiers. Values are validated for NaN/inf and "
                         "range [0, 100].")
    ap.add_argument("--modes", nargs="+", default=None,
                    choices=VALID_MODES,
                    help="Retrieval modes to sweep "
                         "(default: v1 [hybrid dense sparse]; v2 [hybrid])")
    ap.add_argument("--sphp-axis", action="store_true",
                    default=argparse.SUPPRESS,
                    help="For mode=hybrid, run two variants per tier "
                         "(sphp=false baseline and sphp=true); dense/sparse "
                         "run once. (Default: off for v1, on for v2 — the "
                         "confirmatory core IS the P2 vs P2-SPHP axis; "
                         "pass --sphp-axis explicitly in v1 mode to force on)")
    ap.add_argument("--query-sample", type=int, default=None,
                    help="Number of measured queries per arm, N "
                         "(default: v1 50; v2 120 per the frozen powered N)")
    ap.add_argument("--seed", type=int, default=42,
                    help="RNG seed (v1 pool shuffle; recorded in the v2 "
                         "manifest — the v2 pool file itself is pre-seeded "
                         "by the generator)")
    ap.add_argument("--repeats", type=int, default=None,
                    help="Back-to-back repeats per measured query, R; "
                         "repeat 0 is the cold-prefix stratum "
                         "(default: v1 3; v2 from the run spec)")
    ap.add_argument("--warmups", type=int, default=None,
                    help="Warmup queries per arm, W; executed but not "
                         "recorded (default: v1 5; v2 10 per design §5.2)")
    ap.add_argument("--top-k", type=int, default=10,
                    help="top_k sent to the gateway")
    ap.add_argument("--timeout", type=int, default=120,
                    help="Per-request HTTP timeout (seconds)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the plan only: no netem, no HTTP, no output files")
    ap.add_argument("--resume", action="store_true",
                    help="v2: resume an interrupted run — open the existing "
                         "campaign_<run-id>.jsonl in append mode, skip (arm, "
                         "query, repeat) triples already measured, and re-run "
                         "warmups. Without --run-id, a single "
                         "campaign_*.jsonl in the out-dir is auto-detected "
                         "(ambiguous if more than one).")
    ap.add_argument("--run-id", default=None, metavar="ID",
                    help="v2: fixed run id (campaign_<ID>.jsonl) instead of a "
                         "fresh timestamped one; combine with --resume to "
                         "target a specific interrupted file.")
    ap.add_argument("--out-dir", default="experiments/bench/campaigns",
                    help="Directory for JSONL/CSV/JSON outputs")
    return ap


def is_v2(args: argparse.Namespace) -> bool:
    """v2 (Campaign v2) mode is activated by --pool OR --run.

    ``--pool FILE`` (the historical activation) or an explicit ``--run``
    (``--run confirmatory`` / ``--run three-node``) both select v2 mode.
    ``--run`` without ``--pool`` uses the design §5.1 default pool file.
    """
    return args.pool is not None or args.run is not None


def resolve_mode_defaults(args: argparse.Namespace) -> None:
    """Resolve the per-mode sentinel defaults in place.

    v1: modes=[hybrid,dense,sparse], sphp_axis=False, loss=[0,1],
        N=50, W=5 (the historical CLI surface, unchanged).
    v2 (--pool): modes=[hybrid], sphp_axis=True, loss=[0], N=120, W=10
        (the frozen confirmatory core of docs/campaign_v2_design.md).
    An explicitly provided value always wins.
    """
    if is_v2(args):
        spec = V2_RUN_SPECS.get(args.run, V2_RUN_SPECS["confirmatory"])
        if args.modes is None:
            args.modes = ["hybrid"]
        # Amended-design loss sweep (2026-10-14): BOTH RTT {0, 40} and
        # loss {0.5, 1, 2} are swept.  The loss tiers are FLOAT percents;
        # an explicit --loss-tiers-float always wins (NaN-free + range-
        # validated).  RTT defaults to the spec's two tiers.
        if args.run == "loss-sweep":
            if args.rtt_tiers is None or args.rtt_tiers == [0, 15, 40, 80]:
                args.rtt_tiers = list(spec["rtt_tiers"])
            if args.loss_tiers_float is not None:
                args.loss_tiers = _validate_loss_tiers(args.loss_tiers_float)
            elif args.loss_tiers is None or args.loss_tiers == [0]:
                seen = []
                for _, l in spec["cells"]:
                    if l not in seen:
                        seen.append(l)
                args.loss_tiers = seen
        # store_true with default=SUPPRESS: the attribute is absent unless
        # the flag was passed.  v2 default: axis ON (the confirmatory core
        # is the P2 vs P2-SPHP comparison; the 3-node matrix is likewise the
        # 3node-P2 vs 3node-SPHP-from-C axis).
        if not getattr(args, "sphp_axis", False):
            args.sphp_axis = True
        if args.loss_tiers is None:
            args.loss_tiers = [0]
        # Per-run frozen sample sizes (design §4): confirmatory N=120/W=10;
        # three-node N=80/W=10.  An explicit CLI value always wins.
        if args.query_sample is None:
            args.query_sample = spec["n"]
        if args.warmups is None:
            args.warmups = spec["warmups"]
        if args.repeats is None:
            args.repeats = spec["repeats"]
        # Gateway: the 2-node core queries the Node B gateway; the 3-node
        # run queries the Node C (user-facing) gateway (design §2.2).
        if args.gateway_url == "http://10.8.0.2:8000":
            args.gateway_url = f"http://{spec['gateway_ip']}:8000"
    else:
        if args.modes is None:
            args.modes = ["hybrid", "dense", "sparse"]
        # v1 default: axis OFF (unchanged CLI surface) unless the flag was
        # explicitly passed.
        args.sphp_axis = bool(getattr(args, "sphp_axis", False))
        if args.loss_tiers is None:
            args.loss_tiers = [0, 1]
        if args.query_sample is None:
            args.query_sample = 50
        if args.warmups is None:
            args.warmups = 5
        if args.repeats is None:
            args.repeats = 3


# ---------------------------------------------------------------------------
# Variant expansion
# ---------------------------------------------------------------------------
def variants_for_mode(mode: str, sphp_axis: bool) -> List[str]:
    """Return the list of variant labels for a mode.

    hybrid + sphp_axis -> ["baseline", "sphp"]; otherwise -> ["baseline"].
    """
    if mode == "hybrid" and sphp_axis:
        return ["baseline", "sphp"]
    return ["baseline"]


def sphp_flag_for_variant(variant: str) -> bool:
    return variant == "sphp"


def arm_label(mode: str, variant: str) -> str:
    """Design arm key for a (mode, variant): hybrid/baseline -> P2,
    hybrid/sphp -> P2-SPHP, else the mode name."""
    if mode == "hybrid":
        return "P2" if variant == "baseline" else "P2-SPHP"
    return mode


# ---------------------------------------------------------------------------
# Loss-tier validation (amended design: fractional percents, e.g. 0.5%)
# ---------------------------------------------------------------------------
def _validate_loss_tiers(values: List[float]) -> List[float]:
    """Validate a list of float loss percent tiers (NaN-free, 0..100).

    Raises ``ValueError`` on NaN/inf or out-of-range values.  Returns the
    list unchanged (floats).  The amended-design loss sweep uses fractional
    tiers (0.5%), so the driver validates at the boundary rather than
    relying on argparse's type=int coercion.
    """
    out: List[float] = []
    for v in values:
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            raise ValueError(f"loss tier {v!r} must be a finite number")
        if v < 0.0 or v > 100.0:
            raise ValueError(f"loss tier {v!r} out of range [0, 100]")
        out.append(float(v))
    return out


def build_arm_list(args: argparse.Namespace) -> List[Tuple[int, int, str, str]]:
    """Return the full ordered arm list: (rtt, loss, mode, variant).

    Order is regime-major (rtt x loss), then mode, then variant — the same
    order the runner executes arms in (design §6: "4 RTT regimes
    sequentially, P2 → P2-SPHP per regime").  Used both to size the per-arm
    query pool and to drive the dry-run plan so the two always agree.
    """
    arms: List[Tuple[int, int, str, str]] = []
    for rtt in args.rtt_tiers:
        for loss in args.loss_tiers:
            for mode in args.modes:
                for variant in variants_for_mode(mode, args.sphp_axis):
                    arms.append((rtt, loss, mode, variant))
    return arms


# ---------------------------------------------------------------------------
# v2: three-node run (design §2.2/§3.2/§4.3/§5.1 — WS-CM matrix)
# ---------------------------------------------------------------------------
def three_node_arm_label(variant: str) -> str:
    """Design §2.2 3-node arm key for a variant label.

    baseline -> ``3node-P2`` (3-node baseline: sparse on C);
    sphp -> ``3node-SPHP-from-C`` (provisional sparse hint originates on the
    user-facing Node C).  The ``3node-SPHP-from-B`` counterpart is a separate
    WS-CM paired contrast, not part of the 6-arm matrix.
    """
    return "3node-P2" if variant == "baseline" else "3node-SPHP-from-C"


def build_3node_arm_list(spec: Dict[str, Any]) -> List[Tuple[int, int, str]]:
    """Return the ordered 3-node matrix arm list: ``(rtt_b, rtt_c, variant)``.

    Order is cell-major (the design's (B, C) cells in §3.2), then the two
    placement arms (baseline -> sphp).  Both legs are shaped in every 3-node
    cell (loss is 0 on both); there is no unshaped 3-node control (the
    2-node rtt-0 baseline is the control, design §3.2).
    """
    cells = spec["cells"]
    if len(cells[0]) == 4:
        # A 4-tuple spec is the three-node-stress run; the plain 3-node
        # matrix uses (rtt_b, rtt_c) 2-tuples (loss 0 on both legs).
        cells = [(c[0], c[1]) for c in cells]
    arms: List[Tuple[int, int, str]] = []
    for rtt_b, rtt_c in cells:
        for variant in spec["variants"]:
            arms.append((rtt_b, rtt_c, variant))
    return arms


def select_pool_slices_3node(
    all_queries: List[str],
    cells: List[Tuple[int, int]],
    variants: List[str],
    n: int,
    warmups: int,
    line_start: int,
    line_end: int,
) -> List[Dict[str, Any]]:
    """Assign each 3-node arm a DISJOINT contiguous pool line slice.

    Arms are laid out cell-major, variant-second (matching
    ``build_3node_arm_list``) so the fixed line-range of design §5.1 maps
    one arm per block.  Arm ``i`` gets lines
    ``[line_start + i*(N+W) .. line_start + (i+1)*(N+W) - 1]``; the first
    ``warmups`` are warmups (executed, not recorded), the rest measured.

    Fails fast via ``_check_pool_range`` (range size / bounds / duplicates)
    and asserts pairwise disjointness.  Returns one dict per arm with keys
    ``rtt_b, rtt_c, loss_b, loss_c, mode, variant, warmups, measured,
    pool_start, pool_end``.
    """
    arms = [(rtt_b, rtt_c, var)
            for rtt_b, rtt_c in cells for var in variants]
    block = _check_pool_range(all_queries, line_start, line_end,
                              len(arms), n, warmups)
    per_arm = n + warmups
    plan: List[Dict[str, Any]] = []
    for i, (rtt_b, rtt_c, variant) in enumerate(arms):
        start = (line_start - 1) + i * per_arm
        end = start + per_arm
        slice_ = block[i * per_arm:(i + 1) * per_arm]
        plan.append({
            "rtt_b": rtt_b,
            "rtt_c": rtt_c,
            "loss_b": 0,
            "loss_c": 0,
            "mode": "hybrid",
            "variant": variant,
            "warmups": slice_[:warmups],
            "measured": slice_[warmups:end - start],
            "pool_start": start,
            "pool_end": end,
        })
    # Disjointness assertion over the 3-node arm slices (warmups + measured).
    sets = [set(a["warmups"]) | set(a["measured"]) for a in plan]
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            assert not (sets[i] & sets[j]), "internal error: 3-node slices overlap"
    return plan


def build_3node_manifest(
    run_id: str,
    pool_path: str,
    manifest: Dict[str, Any],
    line_start: int,
    line_end: int,
    plan: List[Dict[str, Any]],
    n: int,
    warmups: int,
    repeats: int,
    seed: int,
) -> Dict[str, Any]:
    """Build the ``campaign_v2_manifest.json`` document for the 3-node run
    (design §5.1).  Same manifest discipline as the confirmatory run; the
    per-arm shape records both per-leg (B, C) targets plus the arm's
    design §2.2 key."""
    sets = [set(a["warmups"]) | set(a["measured"]) for a in plan]
    disjoint = True
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            if sets[i] & sets[j]:
                disjoint = False
                break
    return {
        "schema": "pdc.campaign_manifest/v2",
        "design_doc": DESIGN_DOC,
        "design_commit": DESIGN_COMMIT,
        "run_id": run_id,
        "generated_at_utc": _now_iso(),
        "run": "three-node",
        "pool": {
            "path": pool_path,
            "sha256": manifest.get("_verified_sha256"),
            "manifest_path": str(Path(pool_path).with_name(
                Path(pool_path).name + ".manifest.json")),
            "n": manifest.get("n"),
            "line_start": line_start,
            "line_end": line_end,
        },
        "seed": seed,
        "n_per_arm": n,
        "warmups_per_arm": warmups,
        "repeats_per_query": repeats,
        "arms": [
            {
                "arm": three_node_arm_label(a["variant"]),
                "rtt_b": a["rtt_b"],
                "loss_b": a["loss_b"],
                "rtt_c": a["rtt_c"],
                "loss_c": a["loss_c"],
                "mode": a["mode"],
                "variant": a["variant"],
                "netem_link_b_applied": {"rtt_ms": a["rtt_b"],
                                         "loss_pct": a["loss_b"]},
                "netem_link_c_applied": {"rtt_ms": a["rtt_c"],
                                         "loss_pct": a["loss_c"]},
                "pool_start": a["pool_start"] + 1,   # 1-based line numbers
                "pool_end": a["pool_end"],
                "warmups": a["warmups"],
                "measured": a["measured"],
            } for a in plan
        ],
        "disjointness": {
            "asserted": disjoint,
            "n_slices": len(plan),
            "pairwise_intersection_size": 0 if disjoint else -1,
        },
    }


# ---------------------------------------------------------------------------
# Plan / dry-run
# ---------------------------------------------------------------------------
def print_plan_v2_3node(
    args: argparse.Namespace,
    all_queries: List[str],
    arm_plan: List[Dict[str, Any]],
    manifest: Dict[str, Any],
    line_start: int,
    line_end: int,
) -> None:
    """Dry-run plan for the v2 three-node run (no netem, no HTTP, no files)."""
    spec = V2_RUN_SPECS["three-node"]
    cells = spec["cells"]
    n_arms = len(arm_plan)
    per_arm = args.query_sample + args.warmups
    n_measured_per_arm = len(arm_plan[0]["measured"]) * args.repeats
    n_warmups_per_arm = len(arm_plan[0]["warmups"])
    n_total_per_arm = n_warmups_per_arm + n_measured_per_arm
    n_measured = n_arms * n_measured_per_arm
    n_warmups = n_arms * n_warmups_per_arm
    n_total = n_arms * n_total_per_arm
    sets = [set(a["warmups"]) | set(a["measured"]) for a in arm_plan]
    disjoint = all(not (sets[i] & sets[j])
                   for i in range(len(sets))
                   for j in range(i + 1, len(sets)))

    print("=" * 78)
    print("CAMPAIGN V2 — WS-CM 3-NODE MATRIX — DRY RUN")
    print("=" * 78)
    print(f"  design doc   : {DESIGN_DOC} (commit {DESIGN_COMMIT})")
    print(f"  run type     : three-node  ({spec['label']})")
    print(f"  gateway      : {args.gateway_url} (Node C — user-facing tier)")
    print(f"  pool file    : {args.pool}  ({len(all_queries)} lines, "
          f"sha256={manifest.get('_verified_sha256')})")
    print(f"  pool manifest: VERIFIED OK (sha256 + n={manifest.get('n')} "
          f"+ line_count all match)")
    print(f"  pool lines   : {line_start}..{line_end} of the pool file "
          f"(design §5.1; 6 arms x {per_arm} = {n_arms * per_arm} lines)")
    print(f"  per-leg cells: (B, C) in ms = {cells}  "
          f"(B = dense/generation, C = user-facing sparse; loss 0 on both)")
    print(f"  arms         : {n_arms} = {len(cells)} cells x "
          f"{{3node-P2, 3node-SPHP-from-C}} "
          f"(mode=hybrid, sphp axis on)")
    print(f"  per arm      : W={args.warmups} warmups + N={args.query_sample} "
          f"x R={args.repeats} = {n_total_per_arm} requests "
          f"({n_measured_per_arm} measured + {n_warmups_per_arm} warmups)")
    print(f"  TOTAL        : {n_total} requests "
          f"({n_measured} measured + {n_warmups} warmups)")
    print(f"  strata       : repeat 0 = cold-prefix, repeats 1..R-1 = warm "
          f"(no cache flush exists; §5.2)")
    print(f"  fidelity gate: pre- AND post- each cell, PER LEG: "
          f"validate.sh --link-b <rtt_b> and --link-c <rtt_c> "
          f"(11-ping median, ±30% added-RTT window per leg); "
          f"FAIL aborts the campaign")
    print(f"  error gate   : ≤1% error rate per arm (WS-CM 3-node, design §6.4; "
          f"the 2-node core stays at 0)")
    print(f"  campaign manifest: would be written to "
          f"{args.out_dir}/campaign_v2_manifest.json before any request")
    print("-" * 78)
    print("  Execution plan (cells sequentially; arms 3node-P2 -> "
          "3node-SPHP-from-C per cell):")
    for rtt_b, rtt_c in cells:
        tag = (f"shaped: apply --link-b {rtt_b} 0 --link-c {rtt_c} 0; "
               f"per-leg gate pre+post")
        cell_arms = [a for a in arm_plan
                     if a["rtt_b"] == rtt_b and a["rtt_c"] == rtt_c]
        n_cell_total = sum(len(a["warmups"]) + len(a["measured"]) * args.repeats
                           for a in cell_arms)
        n_cell_measured = sum(len(a["measured"]) * args.repeats
                              for a in cell_arms)
        print(f"    cell (B={rtt_b}ms, C={rtt_c}ms)  [{tag}]")
        for variant in spec["variants"]:
            arm = next(a for a in cell_arms if a["variant"] == variant)
            a_total = (len(arm["warmups"])
                       + len(arm["measured"]) * args.repeats)
            print(f"        arm {three_node_arm_label(variant):<18} "
                  f"(mode={arm['mode']} variant={variant})  "
                  f"-> pool lines {arm['pool_start'] + 1}..{arm['pool_end']}  "
                  f"({len(arm['warmups'])}W + {len(arm['measured'])}x{args.repeats}R "
                  f"= {a_total} req)")
        print(f"        cell subtotal: {n_cell_total} requests "
              f"({n_cell_measured} measured)")
    print("-" * 78)
    print(f"  manifest disjointness: {len(arm_plan)} arm slices, pairwise "
          f"intersection == {'0 (asserted OK)' if disjoint else 'NON-EMPTY (FAIL)'}")
    print("-" * 78)
    print("  DRY RUN complete. No network calls, no netem, no files were made.")


# ---------------------------------------------------------------------------
# Plan / dry-run
# ---------------------------------------------------------------------------
def print_plan(args: argparse.Namespace, all_queries: List[str],
               arm_plan: List[Dict[str, Any]]) -> None:
    n_regimes = len(args.rtt_tiers) * len(args.loss_tiers)
    n_variants_per_mode = {
        m: len(variants_for_mode(m, args.sphp_axis)) for m in args.modes
    }
    n_variants_per_regime = sum(n_variants_per_mode[m] for m in args.modes)
    n_arms = len(arm_plan)
    per_arm = args.query_sample + args.warmups
    n_measured_per_arm = len(arm_plan[0]["measured"]) * args.repeats
    n_warmups_per_arm = len(arm_plan[0]["warmups"])
    n_total_per_arm = n_warmups_per_arm + n_measured_per_arm
    n_measured = n_arms * n_measured_per_arm
    n_warmups = n_arms * n_warmups_per_arm
    n_total = n_arms * n_total_per_arm
    n_shaped = sum(1 for r in args.rtt_tiers for l in args.loss_tiers
                   if not (r == 0 and l == 0))

    print("=" * 78)
    print("FINAL-MATRIX CAMPAIGN RUNNER (Node B hybrid RAG gateway) — DRY RUN")
    print("=" * 78)
    print(f"  gateway      : {args.gateway_url}")
    print(f"  queries file : {args.queries} ({len(all_queries)} queries total)")
    print(f"  query sample : N={args.query_sample} measured + W={args.warmups} "
          f"per arm (seed={args.seed})")
    print(f"  query pool   : {n_arms} arms x {per_arm} = {n_arms * per_arm} "
          f"distinct queries (disjoint per arm)")
    print(f"  rtt tiers    : {args.rtt_tiers} ms")
    print(f"  loss tiers   : {args.loss_tiers} %")
    print(f"  modes        : {args.modes}")
    print(f"  sphp axis    : {args.sphp_axis}")
    print(f"  repeats      : {args.repeats} per measured query")
    print(f"  top_k        : {args.top_k}")
    print(f"  out dir      : {args.out_dir}")
    print(f"  regimes      : {n_regimes} (rtt x loss)")
    print(f"  variants/regime: {n_variants_per_regime} "
          f"({', '.join(f'{m}={n_variants_per_mode[m]}' for m in args.modes)})")
    print(f"  arms         : {n_arms} (regime x mode x variant)")
    print(f"  measured req : {n_measured}  (arms x {len(arm_plan[0]['measured'])} x R)")
    print(f"  warmup req   : {n_warmups}  (arms x {len(arm_plan[0]['warmups'])})")
    print(f"  total req    : {n_total}  (arms x (W + N x R))")
    print(f"  shaped regimes (netem apply/clear): {n_shaped}")
    print("-" * 78)
    print("  Plan (netem would be applied/cleared for shaped regimes only):")
    print("  Each arm consumes a DISJOINT slice of the shuffled query pool, so")
    print("  no query is shared across arms (repeat 0 stays cold per arm).")
    for rtt in args.rtt_tiers:
        for loss in args.loss_tiers:
            shaped = not (rtt == 0 and loss == 0)
            tag = "shaped" if shaped else "baseline"
            print(f"    regime rtt={rtt:>3}ms loss={loss}%  [{tag}]")
            for mode in args.modes:
                for variant in variants_for_mode(mode, args.sphp_axis):
                    arm = next(a for a in arm_plan
                               if (a["rtt_ms"], a["loss_pct"], a["mode"],
                                   a["variant"]) == (rtt, loss, mode, variant))
                    print(f"        mode={mode:<7} variant={variant:<8}  "
                          f"-> queries[{arm['pool_start']}:{arm['pool_end']}]  "
                          f"({len(arm['warmups'])} warmups + "
                          f"{len(arm['measured'])} x {args.repeats} measured)")
    print("-" * 78)
    print("  DRY RUN complete. No network calls, no netem, no files were made.")


def print_plan_v2(args: argparse.Namespace, all_queries: List[str],
                  arm_plan: List[Dict[str, Any]], manifest: Dict[str, Any],
                  line_start: int, line_end: int) -> None:
    """Dry-run plan for the v2 confirmatory mode (no netem, no HTTP, no files)."""
    n_arms = len(arm_plan)
    per_arm = args.query_sample + args.warmups
    n_measured_per_arm = len(arm_plan[0]["measured"]) * args.repeats
    n_warmups_per_arm = len(arm_plan[0]["warmups"])
    n_total_per_arm = n_warmups_per_arm + n_measured_per_arm
    n_measured = n_arms * n_measured_per_arm
    n_warmups = n_arms * n_warmups_per_arm
    n_total = n_arms * n_total_per_arm
    disjoint = slices_disjoint(arm_plan)

    print("=" * 78)
    print("CAMPAIGN V2 — CONFIRMATORY 2-NODE MATRIX — DRY RUN")
    print("=" * 78)
    print(f"  design doc   : {DESIGN_DOC} (commit {DESIGN_COMMIT})")
    print(f"  run type     : {args.run}  ({V2_RUN_SPECS[args.run]['label']})")
    print(f"  gateway      : {args.gateway_url}")
    print(f"  pool file    : {args.pool}  ({len(all_queries)} lines, "
          f"sha256={manifest.get('_verified_sha256')})")
    print(f"  pool manifest: VERIFIED OK (sha256 + n={manifest.get('n')} "
          f"+ line_count all match)")
    print(f"  pool lines   : {line_start}..{line_end} of the pool file "
          f"(design §5.1 line-range spec)")
    print(f"  rtt tiers    : {args.rtt_tiers} ms (full round-trip)")
    print(f"  loss tiers   : {args.loss_tiers} %")
    print(f"  arms         : {n_arms} = RTT x {{P2, P2-SPHP}} "
          f"(mode=hybrid, sphp axis on)")
    print(f"  per arm      : W={args.warmups} warmups + N={args.query_sample} "
          f"x R={args.repeats} = {n_total_per_arm} requests "
          f"({n_measured_per_arm} measured + {n_warmups_per_arm} warmups)")
    print(f"  per tier     : {len(args.modes) * (2 if args.sphp_axis else 1)} arms "
          f"-> {n_total_per_arm * len(args.modes) * (2 if args.sphp_axis else 1)} "
          f"requests per RTT tier")
    print(f"  TOTAL        : {n_total} requests "
          f"({n_measured} measured + {n_warmups} warmups)")
    print(f"  strata       : repeat 0 = cold-prefix, repeats 1..R-1 = warm "
          f"(no cache flush exists; §5.2)")
    print(f"  fidelity gate: pre- AND post- each shaped tier: "
          f"validate.sh --link-b <rtt> (11-ping median, ±30% added-RTT window); "
          f"FAIL aborts the campaign; baseline tier (rtt=0) has no gate")
    print(f"  0-error gate : any n_errors > 0 at a tier close fails the "
          f"campaign (design §6.4; 2-node core is 0, not ≤1%)")
    print(f"  campaign manifest: would be written to "
          f"{args.out_dir}/campaign_v2_manifest.json before any request")
    print("-" * 78)
    print("  Execution plan (regimes sequentially; arms P2 -> P2-SPHP per tier):")
    for rtt in args.rtt_tiers:
        for loss in args.loss_tiers:
            shaped = not (rtt == 0 and loss == 0)
            tag = "shaped: apply --link-b " \
                  f"{rtt} {loss}; gate pre+post" if shaped else "baseline: no netem, no gate"
            tier_arms = [a for a in arm_plan
                         if a["rtt_ms"] == rtt and a["loss_pct"] == loss]
            n_tier_total = sum(len(a["warmups"]) + len(a["measured"]) * args.repeats
                               for a in tier_arms)
            n_tier_measured = sum(len(a["measured"]) * args.repeats
                                  for a in tier_arms)
            print(f"    regime rtt={rtt:>3}ms loss={loss}%  [{tag}]")
            for mode in args.modes:
                for variant in variants_for_mode(mode, args.sphp_axis):
                    arm = next(a for a in tier_arms
                               if a["mode"] == mode and a["variant"] == variant)
                    a_total = len(arm["warmups"]) + len(arm["measured"]) * args.repeats
                    print(f"        arm {arm_label(mode, variant):<9} "
                          f"(mode={mode} variant={variant})  "
                          f"-> pool lines {arm['pool_start'] + 1}..{arm['pool_end']}  "
                          f"({len(arm['warmups'])}W + {len(arm['measured'])}x{args.repeats}R "
                          f"= {a_total} req)")
            print(f"        tier subtotal: {n_tier_total} requests "
                  f"({n_tier_measured} measured)")
    print("-" * 78)
    print(f"  manifest disjointness: {len(arm_plan)} arm slices, pairwise "
          f"intersection == {'0 (asserted OK)' if disjoint else 'NON-EMPTY (FAIL)'}")
    print("-" * 78)
    print("  DRY RUN complete. No network calls, no netem, no files were made.")


# ---------------------------------------------------------------------------
# Record building
# ---------------------------------------------------------------------------
def build_record(
    run_id: str,
    rtt: int,
    loss: int,
    mode: str,
    variant: str,
    repeat_index: int,
    query: str,
    res: Dict[str, Any],
    netem_applied: bool,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Flatten a /query/benchmark response into a single JSONL record.

    The record is a SUPERSET of the v1 schema (no v1 field is renamed).
    ``extra`` carries the v2 additions (design §"record-field additions"):
    stratum, rtt_ping_median_ms, netem_link_c_applied, netem_link_b_applied,
    pool manifest hash — all optional, None-valued when not applicable.
    """
    timings = res.get("timings", {}) or {}
    rec: Dict[str, Any] = {
        "run_id": run_id,
        "timestamp": _now_iso(),
        "query_id": res.get("query_id"),
        "query": query,
        "rtt_ms": rtt,
        "loss_pct": loss,
        "mode": mode,
        "variant": variant,
        "repeat_index": repeat_index,
        "top_k": res.get("top_k"),
        "netem_applied": netem_applied,
    }
    # All timing fields from the response.
    for f in TIMING_FIELDS:
        rec[f] = timings.get(f)
    # Top-level response fields.
    for f in RESPONSE_FIELDS:
        rec[f] = res.get(f)
    # SPHP fields (present only when the sphp axis is exercised).
    for f in SPHP_FIELDS:
        if f in res:
            rec[f] = res.get(f)
        elif f in timings:
            rec[f] = timings.get(f)
    if extra:
        rec.update(extra)
    return rec


def v2_record_extra(
    mode: str,
    repeat_index: int,
    rtt: int,
    loss: int,
    gate: Dict[str, Any],
    pool_path: str,
    manifest: Dict[str, Any],
) -> Dict[str, Any]:
    """The v2 record-field additions (design §3.2/§5; all additive).

    - stratum: 'cold' (repeat 0) / 'warm' (repeats 1..R-1), design §5.2;
    - rtt_ping_median_ms: the pre-tier gate's shaped 11-ping median (None
      for the baseline tier — design §6 "per regime transition");
    - netem_link_b_applied / netem_link_c_applied: per-leg targets; link C
      is None for the 2-node core (reserved for WS-CM 3-node arms, §3.2).
    - pool provenance: pool name + verified sha256 (the manifest hash).
    """
    netem_b = None
    if not (rtt == 0 and loss == 0):
        netem_b = {"rtt_ms": rtt, "loss_pct": loss}
    return {
        "stratum": stratum_for_repeat(repeat_index),
        "rtt_ping_median_ms": gate.get("shaped_median_ms"),
        "netem_link_b_applied": netem_b,
        "netem_link_c_applied": None,  # WS-CM 3-node extension point
        "pool_file": Path(pool_path).name,
        "pool_manifest_sha256": manifest.get("_verified_sha256"),
    }


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
def summarize_arm(recs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute the per-(tier, mode, variant) summary row from measured records."""
    ok_recs = [r for r in recs if "error" not in r]
    ttft_vals = [r.get("ttft_ms") or 0.0 for r in ok_recs]
    total_vals = [r.get("total_ms") or 0.0 for r in ok_recs]

    ttft_mean, ttft_std = mean_std(ttft_vals)
    total_mean, _ = mean_std(total_vals)

    # Cold-prefix (repeat 0) vs warm (repeats 1..R-1) TTFT.
    ttft_repeat0 = [r.get("ttft_ms") or 0.0 for r in ok_recs
                    if r.get("repeat_index") == 0]
    ttft_warm = [r.get("ttft_ms") or 0.0 for r in ok_recs
                 if r.get("repeat_index", 0) >= 1]
    ttft_repeat0_mean, _ = mean_std(ttft_repeat0)
    ttft_warm_mean, _ = mean_std(ttft_warm)

    # SPHP hit rate and mean overlap (only meaningful for the sphp variant).
    sphp_hits = [r.get("sphp_hit") for r in ok_recs
                 if r.get("sphp_hit") is not None]
    sphp_hit_rate = (sum(1 for h in sphp_hits if h) / len(sphp_hits)) \
        if sphp_hits else None
    overlaps = [r.get("sphp_overlap") for r in ok_recs
                if r.get("sphp_overlap") is not None]
    mean_overlap = (sum(overlaps) / len(overlaps)) if overlaps else None

    return {
        "n": len(ok_recs),
        "n_errors": len(recs) - len(ok_recs),
        "ttft_mean": round(ttft_mean, 2),
        "ttft_p50": round(percentile(ttft_vals, 50), 2),
        "ttft_p95": round(percentile(ttft_vals, 95), 2),
        "ttft_std": round(ttft_std, 2),
        "total_mean": round(total_mean, 2),
        "total_p95": round(percentile(total_vals, 95), 2),
        "ttft_repeat0_mean": round(ttft_repeat0_mean, 2),
        "ttft_warm_mean": round(ttft_warm_mean, 2),
        "sphp_hit_rate": round(sphp_hit_rate, 4)
        if sphp_hit_rate is not None else None,
        "mean_overlap": round(mean_overlap, 4)
        if mean_overlap is not None else None,
    }


def write_summary_outputs(
    out_dir: Path,
    run_id: str,
    summary_rows: List[Dict[str, Any]],
    summary_doc: Dict[str, Any],
) -> Tuple[Path, Path]:
    """Write the summary CSV + JSON; return the paths."""
    csv_path = out_dir / f"summary_{run_id}.csv"
    json_path = out_dir / f"summary_{run_id}.json"
    if summary_rows:
        fieldnames = list(summary_rows[0].keys())
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(summary_rows)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary_doc, f, indent=2)
    return csv_path, json_path


# ---------------------------------------------------------------------------
# v2: resumable runs (--resume)
# ---------------------------------------------------------------------------
def load_jsonl_records(path) -> Tuple[List[Dict[str, Any]], int]:
    """Read a JSONL file into a list of parsed record dicts.

    Tolerates a TRUNCATED last line (a crash mid-write leaves a partial
    JSON object on the final line): that line is skipped and a warning is
    emitted, and all complete lines before it are returned.  Blank lines are
    ignored.  ``path`` may be a ``str`` or ``Path``.

    Returns ``(records, n_truncated)`` where ``n_truncated`` is the number of
    truncated/incomplete lines skipped (0 for a clean file).
    """
    p = Path(path)
    if not p.is_file():
        return [], 0
    records: List[Dict[str, Any]] = []
    n_truncated = 0
    with open(p, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                # Incomplete JSON: a crash mid-write.  Tolerate + warn; a
                # mid-write record is by definition not a complete record.
                n_truncated += 1
                print(f"WARNING: skipping truncated/corrupt JSONL line in {p} "
                      f"(first 80 chars: {line[:80]!r})", file=sys.stderr)
    return records, n_truncated


def record_identity(rec: Dict[str, Any]) -> Tuple[str, str, int]:
    """The identity of a measured (arm, query, repeat) record for resume.

    Returns ``(f"{rtt}ms-{loss}%-{mode}-{variant}", query, repeat_index)``.

    The arm key is rendered from the record's own fields (``rtt_ms``/
    ``loss_pct``/``mode``/``variant``) so it matches ``arm_key`` used by the
    runner, and the query is the record's ``query`` TEXT (always present and
    unique per arm by the disjoint-slice construction).  The query text is
    used rather than ``query_id`` because ``query_id`` is ``None`` on error
    records (the gateway never responded), which would make two error records
    of the same arm+repeat indistinguishable.
    """
    arm = f"{rec.get('rtt_ms')}ms-{rec.get('loss_pct')}%-{rec.get('mode')}-{rec.get('variant')}"
    return (arm, rec.get("query"), rec.get("repeat_index"))


def extract_completed_triples(
    records: List[Dict[str, Any]],
) -> Set[Tuple[str, str, int]]:
    """Build the set of (arm_key, query, repeat_index) triples already
    present in a JSONL (i.e. already measured — warmups are never recorded)."""
    return {record_identity(r) for r in records
            if r.get("query") is not None}


def find_resumable_jsonl(
    out_dir, run_id: Optional[str] = None,
) -> Tuple[Optional[Path], Optional[str]]:
    """Resolve which ``campaign_<run-id>.jsonl`` a resume should target.

    If ``run_id`` is given, return ``<out-dir>/campaign_<run-id>.jsonl``
    (regardless of whether it exists yet).  Otherwise auto-detect the
    existing ``campaign_*.jsonl`` files in ``out_dir``: return the single one
    if exactly one, ``(None, None)`` if none, and ``(None, "ambiguous")`` if
    more than one (the caller aborts — refusing to guess which file to
    append to).
    """
    d = Path(out_dir)
    if run_id is not None:
        return d / f"campaign_{run_id}.jsonl", None
    if not d.is_dir():
        return None, None
    existing = sorted(d.glob("campaign_*.jsonl"))
    if not existing:
        return None, None
    if len(existing) > 1:
        return None, "ambiguous"
    return existing[0], None


# ---------------------------------------------------------------------------
# v2 run
# ---------------------------------------------------------------------------
def run_campaign_v2(
    args: argparse.Namespace,
    all_queries: List[str],
    manifest: Dict[str, Any],
    arm_plan: List[Dict[str, Any]],
    line_start: int,
    line_end: int,
) -> None:
    """Execute the v2 confirmatory 2-node matrix (design §6 procedure).

    Per shaped RTT tier: pre-tier fidelity gate (fail fast on FAIL),
    apply --link-b, per-arm /health check + warmups + measured repeats
    (JSONL with v2 record-field additions), tier close (0-error assertion,
    post-tier gate, clear).  Writes <out-dir>/campaign_v2_manifest.json
    before any request.  Exit 1 (with partial artifacts kept) on gate or
    0-error failure — data up to the failure is valid.
    """
    pool_path = str(args.pool)
    endpoint = args.gateway_url.rstrip("/") + "/query/benchmark"

    # Health check (fail fast if the gateway is down).
    if not check_health(args.gateway_url, timeout=15):
        print(f"ERROR: gateway health check failed at {args.gateway_url}/health",
              file=sys.stderr)
        sys.exit(1)
    print(f"Gateway healthy: {args.gateway_url}")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # --- Resolve the run id / target JSONL -------------------------------
    #   * explicit --run-id pins it (targets a specific file, e.g. to resume
    #     a particular interrupted run);
    #   * --resume without --run-id auto-detects the single existing
    #     campaign_*.jsonl in the out-dir (abort if ambiguous, fresh if none);
    #   * otherwise a fresh timestamped run id (the historical behaviour).
    resume = bool(getattr(args, "resume", False))
    explicit_run_id = getattr(args, "run_id", None)
    if explicit_run_id is not None:
        run_id = explicit_run_id
    elif resume:
        target, ambig = find_resumable_jsonl(out_dir, None)
        if ambig == "ambiguous":
            existing = sorted(out_dir.glob("campaign_*.jsonl"))
            print(f"ERROR: --resume without --run-id is ambiguous: "
                  f"{len(existing)} campaign_*.jsonl files in {out_dir}: "
                  f"{[str(q) for q in existing]}. Pass --run-id to pick one.",
                  file=sys.stderr)
            sys.exit(1)
        if target is None:
            run_id = _run_id()
            print("NOTE: --resume but no existing campaign_*.jsonl found in "
                  f"{out_dir}; starting a fresh run.")
        else:
            run_id = target.stem.replace("campaign_", "")
            print(f"Resuming existing JSONL: {target}")
    else:
        run_id = _run_id()

    jsonl_path = out_dir / f"campaign_{run_id}.jsonl"

    # Guard against accidentally re-running over a finished/previous result:
    # if the target file already exists and we are NOT resuming, abort.
    if jsonl_path.exists() and not resume:
        print(f"ERROR: {jsonl_path} already exists. Re-running would overwrite "
              f"an existing result file. Pass --resume to append and skip "
              f"already-measured records, or omit --run-id for a fresh run.",
              file=sys.stderr)
        sys.exit(1)

    # Design §5.1: the campaign manifest is written BEFORE any request.
    manifest_path = out_dir / "campaign_v2_manifest.json"
    manifest_doc = build_campaign_manifest(
        run_id, args.run, pool_path, manifest, line_start, line_end,
        arm_plan, args.query_sample, args.warmups, args.repeats, args.seed)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_doc, f, indent=2)
    print(f"Manifest written: {manifest_path} "
          f"(disjointness asserted: {manifest_doc['disjointness']['asserted']})")

    # Clean slate before any shaping.
    clear_netem(dry_run=False)

    # --- Resume: seed completed-triple set + in-memory accumulators -------
    # The end-of-run summary writers (CSV/JSON) summarize the IN-MEMORY
    # ``arms_recs`` (they do not re-read the JSONL), so on a resume we seed
    # ``arms_recs``/``n_ok``/``n_err`` from the existing file's records.  This
    # makes the summary identical whether the JSONL was written in one
    # session or across a resume.  Warmups are never recorded, so they are
    # never in the skip set and are always re-run (cheap/idempotent).
    skip_triples: Set[Tuple[str, str, int]] = set()
    arms_recs: Dict[Tuple[int, int, str, str], List[Dict[str, Any]]] = {}
    gates_log: List[Dict[str, Any]] = []
    n_ok = 0
    n_err = 0
    n_warmup_ok = 0
    n_warmup_err = 0
    exit_code = 0
    abort_reason: Optional[str] = None
    if resume:
        existing_recs, n_trunc = load_jsonl_records(jsonl_path)
        skip_triples = extract_completed_triples(existing_recs)
        for r in existing_recs:
            arm_key = (r.get("rtt_ms"), r.get("loss_pct"),
                       r.get("mode"), r.get("variant"))
            arms_recs.setdefault(arm_key, []).append(r)
            if "error" in r:
                n_err += 1
            else:
                n_ok += 1
        if existing_recs or n_trunc:
            print(f"Resume: {len(existing_recs)} records already present in "
                  f"{jsonl_path.name} ({len(skip_triples)} completed triples); "
                  f"{n_trunc} truncated line(s) skipped.")
        else:
            print(f"Resume: {jsonl_path.name} is empty; starting fresh.")

    def _finalize() -> None:
        """Write summary outputs (partial data stays valid) and exit."""
        summary_rows: List[Dict[str, Any]] = []
        for (rtt, loss, mode, variant), recs in arms_recs.items():
            row = summarize_arm(recs)
            row = {"rtt_ms": rtt, "loss_pct": loss, "mode": mode,
                   "variant": variant, **row}
            summary_rows.append(row)
        summary_rows.sort(key=lambda r: (r["rtt_ms"], r["loss_pct"],
                                         r["mode"], r["variant"]))
        summary_doc = {
            "schema": "pdc.campaign_summary/v2",
            "design_doc": DESIGN_DOC,
            "design_commit": DESIGN_COMMIT,
            "run": args.run,
            "run_id": run_id,
            "generated_at": _now_iso(),
            "status": "completed" if abort_reason is None else "aborted",
            "abort_reason": abort_reason,
            "gateway_url": args.gateway_url,
            "pool": manifest_doc["pool"],
            "line_range": [line_start, line_end],
            "seed": args.seed,
            "query_sample": args.query_sample,
            "warmups": args.warmups,
            "repeats": args.repeats,
            "rtt_tiers": args.rtt_tiers,
            "loss_tiers": args.loss_tiers,
            "modes": args.modes,
            "sphp_axis": args.sphp_axis,
            "top_k": args.top_k,
            "fidelity_gates": gates_log,
            "n_ok": n_ok,
            "n_errors": n_err,
            "n_warmup_ok": n_warmup_ok,
            "n_warmup_errors": n_warmup_err,
            "summary": summary_rows,
        }
        csv_path, json_path = write_summary_outputs(
            out_dir, run_id, summary_rows, summary_doc)
        print(f"\nJSONL   : {jsonl_path}")
        print(f"CSV     : {csv_path}")
        print(f"JSON    : {json_path}")
        if abort_reason is not None:
            print(f"CAMPAIGN ABORTED: {abort_reason}", file=sys.stderr)
        print("Done.")
        sys.exit(exit_code)

    # Append on resume (records already written are preserved and re-read at
    # finalize time); plain "w" otherwise (fresh run, guarded above against
    # clobbering an existing file).
    jsonl_f = open(jsonl_path, "a" if resume else "w", encoding="utf-8")
    try:
        for rtt in args.rtt_tiers:
            for loss in args.loss_tiers:
                shaped = not (rtt == 0 and loss == 0)
                print(f"\n=== regime rtt={rtt}ms loss={loss}% "
                      f"[{'shaped' if shaped else 'baseline'}] ===")

                # --- pre-tier fidelity gate (design §6) ---
                pre_gate = run_fidelity_gate(rtt, "pre", dry_run=False)
                gates_log.append(pre_gate)
                if rtt > 0:
                    print(f"    [gate pre]  rtt={rtt}: {pre_gate['status']} "
                          f"(baseline={pre_gate.get('baseline_median_ms')} ms, "
                          f"shaped={pre_gate.get('shaped_median_ms')} ms, "
                          f"added={pre_gate.get('added_rtt_ms')} ms)")
                    if pre_gate["status"] != "PASS":
                        clear_netem(dry_run=False)
                        abort_reason = (f"pre-tier fidelity gate FAIL at "
                                        f"rtt={rtt}ms: {pre_gate.get('output_tail','').strip()[-200:]}")
                        exit_code = 1
                        _finalize()
                else:
                    print("    [gate pre]  rtt=0: skipped-baseline (no shaping, no gate)")

                netem_applied = apply_netem_link_b(rtt, loss, dry_run=False)

                # --- arms (P2 -> P2-SPHP per regime) ---
                for mode in args.modes:
                    for variant in variants_for_mode(mode, args.sphp_axis):
                        if not check_health(args.gateway_url, timeout=15):
                            clear_netem(dry_run=False)
                            abort_reason = (f"gateway /health failed before arm "
                                            f"{arm_label(mode, variant)} at rtt={rtt}ms")
                            exit_code = 1
                            _finalize()
                        sphp = sphp_flag_for_variant(variant)
                        arm_key = (rtt, loss, mode, variant)
                        arm = next(a for a in arm_plan
                                   if (a["rtt_ms"], a["loss_pct"], a["mode"],
                                       a["variant"]) == arm_key)
                        warmup_queries = arm["warmups"]
                        measured_queries = arm["measured"]
                        extra = v2_record_extra(
                            mode, 0, rtt, loss, pre_gate, pool_path, manifest)
                        print(f"  arm {arm_label(mode, variant):<9} "
                              f"(mode={mode} variant={variant} sphp={sphp})  "
                              f"pool lines {arm['pool_start'] + 1}..{arm['pool_end']}")

                        # Warmups: executed but NOT recorded (disjoint slice).
                        for w, wq in enumerate(warmup_queries):
                            try:
                                http_post_json(endpoint, {
                                    "query": wq,
                                    "top_k": args.top_k,
                                    "mode": mode,
                                    "sphp": sphp,
                                }, timeout=args.timeout)
                                n_warmup_ok += 1
                            except Exception as e:
                                n_warmup_err += 1
                                print(f"    [warmup] {mode}/{variant} w={w} "
                                      f"failed: {e}", file=sys.stderr)

                        # Measured: N queries x R repeats back-to-back.
                        # On resume, a (arm, query, repeat) triple already
                        # present in the JSONL is already measured -> skip it
                        # (never re-POST, never re-write).  Warmups above are
                        # NOT gated: they are cheap/idempotent and are always
                        # re-run.
                        n_arm_skipped = 0
                        for query in measured_queries:
                            for rep in range(args.repeats):
                                triple = (
                                    f"{rtt}ms-{loss}%-{mode}-{variant}",
                                    query, rep)
                                if triple in skip_triples:
                                    n_arm_skipped += 1
                                    continue
                                try:
                                    res = http_post_json(endpoint, {
                                        "query": query,
                                        "top_k": args.top_k,
                                        "mode": mode,
                                        "sphp": sphp,
                                    }, timeout=args.timeout)
                                    rec = build_record(
                                        run_id, rtt, loss, mode, variant, rep,
                                        query, res, netem_applied,
                                        extra=v2_record_extra(
                                            mode, rep, rtt, loss, pre_gate,
                                            pool_path, manifest))
                                    n_ok += 1
                                except Exception as e:
                                    rec: Dict[str, Any] = {
                                        "run_id": run_id,
                                        "timestamp": _now_iso(),
                                        "query_id": None,
                                        "query": query,
                                        "rtt_ms": rtt,
                                        "loss_pct": loss,
                                        "mode": mode,
                                        "variant": variant,
                                        "repeat_index": rep,
                                        "top_k": args.top_k,
                                        "netem_applied": netem_applied,
                                        **v2_record_extra(
                                            mode, rep, rtt, loss, pre_gate,
                                            pool_path, manifest),
                                    }
                                    rec["error"] = str(e)
                                    n_err += 1
                                skip_triples.add(triple)
                                jsonl_f.write(json.dumps(rec) + "\n")
                                jsonl_f.flush()
                                arms_recs.setdefault(arm_key, []).append(rec)
                        if n_arm_skipped:
                            print(f"    [resume] {arm_label(mode, variant)}: "
                                  f"skipped {n_arm_skipped} already-measured "
                                  f"(arm, query, repeat) record(s)")

                # --- tier close: 0-error assertion (design §6.4) ---
                tier_err = sum(
                    len([r for r in recs if "error" in r])
                    for (t, l, _, _), recs in arms_recs.items()
                    if t == rtt and l == loss)
                if tier_err > 0:
                    post_gate = run_fidelity_gate(rtt, "post", dry_run=False)
                    gates_log.append(post_gate)
                    clear_netem(dry_run=False)
                    abort_reason = (f"0-error tier assertion FAILED at rtt={rtt}ms "
                                    f"({tier_err} error records; the 2-node core "
                                    f"requires 0 — design §6.4; re-run the tier)")
                    exit_code = 1
                    _finalize()

                # --- post-tier fidelity gate (design §6; clears netem on exit) ---
                post_gate = run_fidelity_gate(rtt, "post", dry_run=False)
                gates_log.append(post_gate)
                if rtt > 0:
                    print(f"    [gate post] rtt={rtt}: {post_gate['status']} "
                          f"(shaped={post_gate.get('shaped_median_ms')} ms, "
                          f"added={post_gate.get('added_rtt_ms')} ms)")
                    if post_gate["status"] != "PASS":
                        clear_netem(dry_run=False)
                        abort_reason = (f"post-tier fidelity gate FAIL at rtt={rtt}ms: "
                                        f"{post_gate.get('output_tail','').strip()[-200:]}")
                        exit_code = 1
                        _finalize()
                else:
                    print("    [gate post] rtt=0: skipped-baseline (no shaping, no gate)")

                clear_netem(dry_run=False)
    finally:
        jsonl_f.close()

    _finalize()
    _finalize()


# ---------------------------------------------------------------------------
# v2: three-node record fields & runner (design §2.2/§3.2/§5)
# ---------------------------------------------------------------------------
def v3node_record_extra(
    repeat_index: int,
    rtt_b: int,
    loss_b: int,
    rtt_c: int,
    loss_c: int,
    pre_gates: List[Dict[str, Any]],
    pool_path: str,
    manifest: Dict[str, Any],
) -> Dict[str, Any]:
    """The 3-node record-field additions (all additive).

    - stratum: 'cold' (repeat 0) / 'warm' (repeats 1..R-1), design §5.2;
    - rtt_ping_median_ms: the pre-cell LINK-B shaped 11-ping median (the
      dense/generation leg that dominates TTFT); None when the leg is
      unshaped;
    - netem_link_b_applied / netem_link_c_applied: per-leg targets (design
      §3.2 — the record gains ``netem_link_c_applied`` + per-leg targets);
    - pool provenance: pool name + verified sha256.
    """
    b_gate = next((g for g in pre_gates if g.get("link") == "b"), None)
    netem_b = {"rtt_ms": rtt_b, "loss_pct": loss_b}
    netem_c = {"rtt_ms": rtt_c, "loss_pct": loss_c}
    return {
        "stratum": stratum_for_repeat(repeat_index),
        "rtt_ping_median_ms": (b_gate or {}).get("shaped_median_ms"),
        "netem_link_b_applied": netem_b,
        "netem_link_c_applied": netem_c,
        "pool_file": Path(pool_path).name,
        "pool_manifest_sha256": manifest.get("_verified_sha256"),
    }


def run_campaign_v3node(
    args: argparse.Namespace,
    all_queries: List[str],
    manifest: Dict[str, Any],
    arm_plan: List[Dict[str, Any]],
    line_start: int,
    line_end: int,
) -> None:
    """Execute the WS-CM 3-node matrix (design §2.2/§3.2/§6 procedure).

    Per (B, C) cell: pre-cell per-leg fidelity gate (``validate.sh
    --link-b`` + ``validate.sh --link-c``, each FAIL aborts), then
    ``apply.sh --link-b … --link-c …``, then the two arms
    (3node-P2 -> 3node-SPHP-from-C) against the Node C gateway, then a
    post-cell per-leg gate, then a ≤1% per-arm error gate (design §6.4 —
    the 3-node arms relax from the 2-node 0-error to ≤1%), then clear.
    Writes ``<out-dir>/campaign_v2_manifest.json`` before any request.
    Exits 1 (with partial artifacts kept) on any gate or error-gate failure.

    This run type is the WS-CM workstream slice; it does not implement the
    v2-core ``--resume`` machinery (a single-shot per-cell batch).
    """
    spec = V2_RUN_SPECS["three-node"]
    cells = spec["cells"]
    pool_path = str(args.pool)
    endpoint = args.gateway_url.rstrip("/") + "/query/benchmark"

    # Health check at the Node C gateway (fail fast if it is down).
    if not check_health(args.gateway_url, timeout=15):
        print(f"ERROR: gateway health check failed at {args.gateway_url}/health",
              file=sys.stderr)
        sys.exit(1)
    print(f"Gateway healthy: {args.gateway_url} (Node C)")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    run_id = _run_id()
    jsonl_path = out_dir / f"campaign_{run_id}.jsonl"
    if jsonl_path.exists():
        print(f"ERROR: {jsonl_path} already exists; omit --run-id for a "
              f"fresh run.", file=sys.stderr)
        sys.exit(1)

    # Design §5.1: the campaign manifest is written BEFORE any request.
    manifest_path = out_dir / "campaign_v2_manifest.json"
    manifest_doc = build_3node_manifest(
        run_id, pool_path, manifest, line_start, line_end,
        arm_plan, args.query_sample, args.warmups, args.repeats, args.seed)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_doc, f, indent=2)
    print(f"Manifest written: {manifest_path} "
          f"(disjointness asserted: {manifest_doc['disjointness']['asserted']})")

    # Clean slate before any shaping.
    clear_netem(dry_run=False)

    arms_recs: Dict[Tuple[int, int, str, str], List[Dict[str, Any]]] = {}
    gates_log: List[Dict[str, Any]] = []
    n_ok = 0
    n_err = 0
    n_warmup_ok = 0
    n_warmup_err = 0
    exit_code = 0
    abort_reason: Optional[str] = None

    def _finalize() -> None:
        summary_rows: List[Dict[str, Any]] = []
        for (rtt_b, rtt_c, mode, variant), recs in arms_recs.items():
            row = summarize_arm(recs)
            row = {"rtt_b": rtt_b, "rtt_c": rtt_c, "mode": mode,
                   "variant": variant, **row}
            summary_rows.append(row)
        summary_rows.sort(key=lambda r: (r["rtt_b"], r["rtt_c"],
                                         r["mode"], r["variant"]))
        summary_doc = {
            "schema": "pdc.campaign_summary/v2",
            "design_doc": DESIGN_DOC,
            "design_commit": DESIGN_COMMIT,
            "run": "three-node",
            "run_id": run_id,
            "generated_at": _now_iso(),
            "status": "completed" if abort_reason is None else "aborted",
            "abort_reason": abort_reason,
            "gateway_url": args.gateway_url,
            "pool": manifest_doc["pool"],
            "line_range": [line_start, line_end],
            "seed": args.seed,
            "query_sample": args.query_sample,
            "warmups": args.warmups,
            "repeats": args.repeats,
            "cells": [list(c) for c in cells],
            "variants": spec["variants"],
            "top_k": args.top_k,
            "fidelity_gates": gates_log,
            "n_ok": n_ok,
            "n_errors": n_err,
            "n_warmup_ok": n_warmup_ok,
            "n_warmup_errors": n_warmup_err,
            "summary": summary_rows,
        }
        csv_path, json_path = write_summary_outputs(
            out_dir, run_id, summary_rows, summary_doc)
        print(f"\nJSONL   : {jsonl_path}")
        print(f"CSV     : {csv_path}")
        print(f"JSON    : {json_path}")
        if abort_reason is not None:
            print(f"CAMPAIGN ABORTED: {abort_reason}", file=sys.stderr)
        print("Done.")
        sys.exit(exit_code)

    jsonl_f = open(jsonl_path, "w", encoding="utf-8")
    try:
        for rtt_b, rtt_c in cells:
            print(f"\n=== cell (B={rtt_b}ms, C={rtt_c}ms) [shaped] ===")

            # --- pre-cell per-leg fidelity gate (design §3.2/§6) ---
            pre_gates = run_fidelity_gate_3node(rtt_b, 0, rtt_c, 0, "pre",
                                                dry_run=False)
            gates_log.extend(pre_gates)
            for g in pre_gates:
                print(f"    [gate pre]  link-{g['link']} rtt={g['rtt_ms']}ms: "
                      f"{g['status']} "
                      f"(baseline={g.get('baseline_median_ms')} ms, "
                      f"shaped={g.get('shaped_median_ms')} ms, "
                      f"added={g.get('added_rtt_ms')} ms)")
            if any(g["status"] != "PASS" for g in pre_gates):
                clear_netem(dry_run=False)
                bad = next(g for g in pre_gates if g["status"] != "PASS")
                abort_reason = (f"pre-cell fidelity gate FAIL on link-"
                                f"{bad['link']} at (B={rtt_b}, C={rtt_c}): "
                                f"{bad.get('output_tail','').strip()[-200:]}")
                exit_code = 1
                _finalize()

            # validate.sh clears netem on exit; re-apply both legs.
            netem_applied = apply_netem_3node(rtt_b, 0, rtt_c, 0,
                                              dry_run=False)

            # --- arms (3node-P2 -> 3node-SPHP-from-C per cell) ---
            for variant in spec["variants"]:
                if not check_health(args.gateway_url, timeout=15):
                    clear_netem(dry_run=False)
                    abort_reason = (f"gateway /health failed before arm "
                                    f"{three_node_arm_label(variant)} at "
                                    f"(B={rtt_b}, C={rtt_c})")
                    exit_code = 1
                    _finalize()
                sphp = sphp_flag_for_variant(variant)
                arm_key = (rtt_b, rtt_c, "hybrid", variant)
                arm = next(a for a in arm_plan
                           if a["rtt_b"] == rtt_b and a["rtt_c"] == rtt_c
                           and a["variant"] == variant)
                print(f"  arm {three_node_arm_label(variant):<18} "
                      f"(mode={arm['mode']} variant={variant} sphp={sphp})  "
                      f"pool lines {arm['pool_start'] + 1}..{arm['pool_end']}")

                # Warmups: executed but NOT recorded (disjoint slice).
                for w, wq in enumerate(arm["warmups"]):
                    try:
                        http_post_json(endpoint, {
                            "query": wq, "top_k": args.top_k,
                            "mode": "hybrid", "sphp": sphp,
                        }, timeout=args.timeout)
                        n_warmup_ok += 1
                    except Exception as e:
                        n_warmup_err += 1
                        print(f"    [warmup] {variant} w={w} failed: {e}",
                              file=sys.stderr)

                # Measured: N queries x R repeats back-to-back.
                for query in arm["measured"]:
                    for rep in range(args.repeats):
                        extra = v3node_record_extra(
                            rep, rtt_b, 0, rtt_c, 0, pre_gates,
                            pool_path, manifest)
                        try:
                            res = http_post_json(endpoint, {
                                "query": query, "top_k": args.top_k,
                                "mode": "hybrid", "sphp": sphp,
                            }, timeout=args.timeout)
                            rec = build_record(
                                run_id, rtt_b, 0, "hybrid", variant, rep,
                                query, res, netem_applied, extra=extra)
                            n_ok += 1
                        except Exception as e:
                            rec: Dict[str, Any] = {
                                "run_id": run_id,
                                "timestamp": _now_iso(),
                                "query_id": None,
                                "query": query,
                                "rtt_ms": rtt_b,
                                "loss_pct": 0,
                                "mode": "hybrid",
                                "variant": variant,
                                "repeat_index": rep,
                                "top_k": args.top_k,
                                "netem_applied": netem_applied,
                                **extra,
                            }
                            rec["error"] = str(e)
                            n_err += 1
                        jsonl_f.write(json.dumps(rec) + "\n")
                        jsonl_f.flush()
                        arms_recs.setdefault(arm_key, []).append(rec)

            # --- cell close: ≤1% per-arm error gate (design §6.4) ---
            cell_arms = [k for k in arms_recs
                         if k[0] == rtt_b and k[1] == rtt_c]
            for arm_key in cell_arms:
                recs = arms_recs[arm_key]
                errs = sum(1 for r in recs if "error" in r)
                total = len(recs)
                if total and (errs / total) > 0.01:
                    post_gates = run_fidelity_gate_3node(
                        rtt_b, 0, rtt_c, 0, "post", dry_run=False)
                    gates_log.extend(post_gates)
                    clear_netem(dry_run=False)
                    abort_reason = (f"≤1% error gate FAILED on arm "
                                    f"{three_node_arm_label(arm_key[3])} at "
                                    f"(B={rtt_b}, C={rtt_c}): {errs}/{total} "
                                    f"errors ({100.0 * errs / total:.1f}%)")
                    exit_code = 1
                    _finalize()

            # --- post-cell per-leg fidelity gate (design §6; clears netem) ---
            post_gates = run_fidelity_gate_3node(rtt_b, 0, rtt_c, 0, "post",
                                                 dry_run=False)
            gates_log.extend(post_gates)
            for g in post_gates:
                print(f"    [gate post] link-{g['link']} rtt={g['rtt_ms']}ms: "
                      f"{g['status']} "
                      f"(shaped={g.get('shaped_median_ms')} ms, "
                      f"added={g.get('added_rtt_ms')} ms)")
            if any(g["status"] != "PASS" for g in post_gates):
                clear_netem(dry_run=False)
                bad = next(g for g in post_gates if g["status"] != "PASS")
                abort_reason = (f"post-cell fidelity gate FAIL on link-"
                                f"{bad['link']} at (B={rtt_b}, C={rtt_c}): "
                                f"{bad.get('output_tail','').strip()[-200:]}")
                exit_code = 1
                _finalize()

            clear_netem(dry_run=False)
    finally:
        jsonl_f.close()

    _finalize()


# ---------------------------------------------------------------------------
# v2: three-node-stress run (CM-5 per-leg loss/RTT stress matrix, final
# design §3.4, finalized in b239833).  Distinct from the plain `three-node`
# matrix: each (B, C, lossB, lossC) cell is shaped with float per-leg loss,
# and the summary carries the SPHP-from-C hit rate with a Wilson 95% CI.
# ---------------------------------------------------------------------------
def _loss_gate_from_output(gate_out: str) -> Optional[str]:
    """Parse the sibling-commit ``validate.sh`` loss-readback gate outcome
    from a gate-run output string.

    Returns ``"PASS"`` / ``"FAIL"`` for the ``Loss gate:`` readback line
    (loss/2 per direction, within 0.05pp) or ``None`` when the script did
    not emit one (no LOSS_PCT was passed, so the script skipped the
    readback).  The driver depends on the script's interface here — it does
    not re-implement the qdisc logic.
    """
    if not gate_out or "Loss gate:" not in gate_out:
        return None
    if "Loss gate: PASS" in gate_out:
        return "PASS"
    if "Loss gate: FAIL" in gate_out:
        return "FAIL"
    return None


def build_stress_arm_list(spec: Dict[str, Any]) -> List[Tuple[int, int, float, float, str]]:
    """Return the ordered three-node-stress arm list:
    ``(rtt_b, rtt_c, loss_b, loss_c, variant)``.

    Order is cell-major (the spec's 27 (B, C, lossB, lossC) cells), then the
    two placement arms (baseline -> sphp).  27 cells x 2 arms = 54 arms.
    The C leg is held at 15 ms (the user-facing sparse leg, design §3.4);
    the B leg carries the swept RTT; each loss value is a float percent
    (0.5/1/2) applied to its leg independently (B / C / both).
    """
    arms: List[Tuple[int, int, float, float, str]] = []
    for rtt_b, rtt_c, loss_b, loss_c in spec["cells"]:
        for variant in spec["variants"]:
            arms.append((rtt_b, rtt_c, loss_b, loss_c, variant))
    return arms


def select_pool_slices_stress(
    all_queries: List[str],
    cells: List[Tuple[int, int, float, float]],
    variants: List[str],
    n: int,
    warmups: int,
    line_start: int,
    line_end: int,
) -> List[Dict[str, Any]]:
    """Assign each three-node-stress arm a DISJOINT contiguous pool line
    slice.

    Arms are laid out cell-major, variant-second (matching
    ``build_stress_arm_list``) so the fixed line-range (design §5.1
    allocation) maps one arm per block.  Arm ``i`` gets lines
    ``[line_start + i*(N+W) .. line_start + (i+1)*(N+W) - 1]`` (1-based);
    the first ``warmups`` are warmups (executed, not recorded), the rest
    measured.

    Fails fast via ``_check_pool_range`` (range size / bounds / duplicates)
    and asserts pairwise disjointness over all arm slices (warmups +
    measured).  Returns one dict per arm with keys ``rtt_b, rtt_c, loss_b,
    loss_c, mode, variant, arm_label, warmups, measured, pool_start,
    pool_end``.
    """
    arms = [(rtt_b, rtt_c, loss_b, loss_c, var)
            for rtt_b, rtt_c, loss_b, loss_c in cells
            for var in variants]
    block = _check_pool_range(all_queries, line_start, line_end,
                              len(arms), n, warmups)
    per_arm = n + warmups
    plan: List[Dict[str, Any]] = []
    for i, (rtt_b, rtt_c, loss_b, loss_c, variant) in enumerate(arms):
        start = (line_start - 1) + i * per_arm
        end = start + per_arm
        slice_ = block[i * per_arm:(i + 1) * per_arm]
        plan.append({
            "rtt_b": rtt_b,
            "rtt_c": rtt_c,
            "loss_b": loss_b,
            "loss_c": loss_c,
            "mode": "hybrid",
            "variant": variant,
            "arm_label": three_node_arm_label(variant),
            "warmups": slice_[:warmups],
            "measured": slice_[warmups:end - start],
            "pool_start": start,
            "pool_end": end,
        })
    # Disjointness assertion over all arm slices (warmups + measured).
    sets = [set(a["warmups"]) | set(a["measured"]) for a in plan]
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            assert not (sets[i] & sets[j]), (
                "internal error: three-node-stress slices overlap")
    return plan


def build_stress_manifest(
    run_id: str,
    pool_path: str,
    manifest: Dict[str, Any],
    line_start: int,
    line_end: int,
    plan: List[Dict[str, Any]],
    n: int,
    warmups: int,
    repeats: int,
    seed: int,
) -> Dict[str, Any]:
    """Build the ``campaign_v2_manifest.json`` document for the
    three-node-stress run (design §5.1).  Same manifest discipline as the
    3-node / confirmatory runs; the per-arm shape records BOTH per-leg (B,
    C) (rtt, loss) targets plus the arm's design §2.2 key.
    """
    sets = [set(a["warmups"]) | set(a["measured"]) for a in plan]
    disjoint = True
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            if sets[i] & sets[j]:
                disjoint = False
                break
    return {
        "schema": "pdc.campaign_manifest/v2",
        "design_doc": DESIGN_DOC,
        "design_commit": DESIGN_COMMIT,
        "run_id": run_id,
        "generated_at_utc": _now_iso(),
        "run": "three-node-stress",
        "pool": {
            "path": pool_path,
            "sha256": manifest.get("_verified_sha256"),
            "manifest_path": str(Path(pool_path).with_name(
                Path(pool_path).name + ".manifest.json")),
            "n": manifest.get("n"),
            "line_start": line_start,
            "line_end": line_end,
        },
        "seed": seed,
        "n_per_arm": n,
        "warmups_per_arm": warmups,
        "repeats_per_query": repeats,
        "cells": [list(c) for c in V2_RUN_SPECS["three-node-stress"]["cells"]],
        "variants": V2_RUN_SPECS["three-node-stress"]["variants"],
        "sanity": {
            "pool_start": V2_RUN_SPECS["three-node-stress"]["sanity_pool_start"],
            "n": V2_RUN_SPECS["three-node-stress"]["sanity_n"],
            "repeats": V2_RUN_SPECS["three-node-stress"]["sanity_repeats"],
        },
        "arms": [
            {
                "arm": a["arm_label"],
                "rtt_b": a["rtt_b"],
                "loss_b": a["loss_b"],
                "rtt_c": a["rtt_c"],
                "loss_c": a["loss_c"],
                "mode": a["mode"],
                "variant": a["variant"],
                "netem_link_b_applied": {"rtt_ms": a["rtt_b"],
                                         "loss_pct": a["loss_b"]},
                "netem_link_c_applied": {"rtt_ms": a["rtt_c"],
                                         "loss_pct": a["loss_c"]},
                "pool_start": a["pool_start"] + 1,   # 1-based line numbers
                "pool_end": a["pool_end"],
                "warmups": a["warmups"],
                "measured": a["measured"],
            } for a in plan
        ],
        "disjointness": {
            "asserted": disjoint,
            "n_slices": len(plan),
            "pairwise_intersection_size": 0 if disjoint else -1,
        },
    }


def sphp_hit_rate_wilson(
    recs: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Per-cell SPHP-from-C hit rate with a Wilson 95% CI (the CM-5 key
    deliverable).

    The Wilson CI is IMPORTED from ``experiments/analysis/stats.py``
    (``wilson_ci(k, n, z=1.96)``) — not re-implemented here.  Returns a dict
    with ``n_hits``, ``n`` (records with a non-None ``sphp_hit``), ``rate``
    (point estimate), and ``ci_95`` = [lo, hi] (or ``None`` if the stats
    engine is not importable on this host or there are no hits).
    """
    hits = [r.get("sphp_hit") for r in recs
            if r.get("sphp_hit") is not None and "error" not in r]
    n = len(hits)
    k = sum(1 for h in hits if h)
    if n == 0:
        return {"n_hits": 0, "n": 0, "rate": None, "ci_95": None}
    rate = k / n
    wilson = _get_wilson_ci()
    if wilson is None:
        ci = None
    else:
        lo, hi = wilson(k, n)
        ci = [round(lo, 4), round(hi, 4)]
    return {"n_hits": k, "n": n,
            "rate": round(rate, 4), "ci_95": ci}


def print_plan_v3node_stress(
    args: argparse.Namespace,
    all_queries: List[str],
    arm_plan: List[Dict[str, Any]],
    manifest: Dict[str, Any],
    line_start: int,
    line_end: int,
) -> None:
    """Dry-run plan for the three-node-stress run (no netem, no HTTP, no
    files)."""
    spec = V2_RUN_SPECS["three-node-stress"]
    cells = spec["cells"]
    n_arms = len(arm_plan)
    per_arm = args.query_sample + args.warmups
    n_measured_per_arm = len(arm_plan[0]["measured"]) * args.repeats
    n_warmups_per_arm = len(arm_plan[0]["warmups"])
    n_total_per_arm = n_warmups_per_arm + n_measured_per_arm
    n_measured = n_arms * n_measured_per_arm
    n_warmups = n_arms * n_warmups_per_arm
    n_total = n_arms * n_total_per_arm
    n_sanity = spec["sanity_n"] * spec["sanity_repeats"]
    n_total_all = n_total + n_sanity
    sets = [set(a["warmups"]) | set(a["measured"]) for a in arm_plan]
    disjoint = all(not (sets[i] & sets[j])
                   for i in range(len(sets))
                   for j in range(i + 1, len(sets)))

    print("=" * 78)
    print("CAMPAIGN V2 — CM-5 3-NODE PER-LEG LOSS/RTT STRESS MATRIX — DRY RUN")
    print("=" * 78)
    print(f"  design doc   : {DESIGN_DOC} (commit {DESIGN_COMMIT})")
    print(f"  run type     : three-node-stress  ({spec['label']})")
    print(f"  gateway      : {args.gateway_url} (Node C — user-facing tier)")
    print(f"  pool file    : {args.pool}  ({len(all_queries)} lines, "
          f"sha256={manifest.get('_verified_sha256')})")
    print(f"  pool manifest: VERIFIED OK (sha256 + n={manifest.get('n')} "
          f"+ line_count all match)")
    print(f"  pool lines   : {line_start}..{line_end} of the pool file "
          f"(design §5.1 allocation; {n_arms} arms x {per_arm} = "
          f"{n_arms * per_arm} lines)")
    print(f"  cells        : {len(cells)} (B, C, lossB, lossC) per-leg float "
          f"loss cells (loss {0.5}/{1}/{2}%: B leg / C leg / both) x "
          f"3-node RTT cells (B,C) in {{(15,15),(40,15),(80,15)}}; C held at "
          f"15 ms (user-facing sparse leg)")
    print(f"  arms         : {n_arms} = {len(cells)} cells x "
          f"{{3node-P2, 3node-SPHP-from-C}} "
          f"(mode=hybrid, sphp axis on)")
    print(f"  per arm      : W={args.warmups} warmups + N={args.query_sample} "
          f"x R={args.repeats} = {n_total_per_arm} requests "
          f"({n_measured_per_arm} measured + {n_warmups_per_arm} warmups)")
    print(f"  sanity       : (0,0) unshaped pre-flight ({n_sanity} requests "
          f"from the free pool tail {spec['sanity_pool_start']}..; 0-error "
          f"gate — any error ABORTS before loss is applied)")
    print(f"  error gate   : shaped cells REPORT per-cell error rate WITHOUT "
          f"aborting (loss-driven errors are the signal, not a campaign "
          f"failure)")
    print(f"  TOTAL        : {n_total} band requests "
          f"({n_measured} measured + {n_warmups} warmups) + {n_sanity} sanity "
          f"= {n_total_all} total")
    print(f"  strata       : repeat 0 = cold-prefix, repeats 1..R-1 = warm "
          f"(no cache flush exists; §5.2)")
    print(f"  fidelity gate: pre- AND post- each cell, PER LEG: "
          f"validate.sh --link-b <rtt_b> <loss_b> and --link-c <rtt_c> "
          f"<loss_c> (11-ping median, ±30% added-RTT window per leg, PLUS "
          f"the sibling-commit fractional-loss readback); FAIL aborts the "
          f"campaign")
    print(f"  key deliverable: per-cell SPHP-from-C hit rate with a Wilson "
          f"95% CI (imported from experiments/analysis/stats.py::wilson_ci, "
          f"not reimplemented) in the summary")
    print(f"  campaign manifest: would be written to "
          f"{args.out_dir}/campaign_v2_manifest.json before any request")
    print("-" * 78)
    print("  Execution plan (cells sequentially; arms 3node-P2 -> "
          "3node-SPHP-from-C per cell):")
    for rtt_b, rtt_c, loss_b, loss_c in cells:
        fb, fc = _fmt_loss(loss_b), _fmt_loss(loss_c)
        tag = (f"shaped: apply --link-b {rtt_b} {fb} --link-c {rtt_c} {fc}; "
               f"per-leg gate pre+post (with loss readback); clear after")
        cell_arms = [a for a in arm_plan
                     if a["rtt_b"] == rtt_b and a["rtt_c"] == rtt_c
                     and a["loss_b"] == loss_b and a["loss_c"] == loss_c]
        n_cell_total = sum(len(a["warmups"]) + len(a["measured"]) * args.repeats
                           for a in cell_arms)
        n_cell_measured = sum(len(a["measured"]) * args.repeats
                              for a in cell_arms)
        print(f"    cell (B={rtt_b}ms/{fb}%, C={rtt_c}ms/{fc}%)  [{tag}]")
        for variant in spec["variants"]:
            arm = next(a for a in cell_arms if a["variant"] == variant)
            a_total = (len(arm["warmups"])
                       + len(arm["measured"]) * args.repeats)
            print(f"        arm {arm['arm_label']:<18} "
                  f"(mode={arm['mode']} variant={variant})  "
                  f"-> pool lines {arm['pool_start'] + 1}..{arm['pool_end']}  "
                  f"({len(arm['warmups'])}W + {len(arm['measured'])}x"
                  f"{args.repeats}R = {a_total} req)")
        print(f"        cell subtotal: {n_cell_total} requests "
              f"({n_cell_measured} measured)")
    print("-" * 78)
    print(f"  manifest disjointness: {len(arm_plan)} arm slices, pairwise "
          f"intersection == {'0 (asserted OK)' if disjoint else 'NON-EMPTY (FAIL)'}")
    print("-" * 78)
    print("  DRY RUN complete. No network calls, no netem, no files were made.")


def run_campaign_v3node_stress(
    args: argparse.Namespace,
    all_queries: List[str],
    manifest: Dict[str, Any],
    arm_plan: List[Dict[str, Any]],
    line_start: int,
    line_end: int,
) -> None:
    """Execute the CM-5 three-node-stress matrix (final design §3.4/§4.3,
    finalized in b239833).

    Pre-flight: a (0,0) unshaped sanity sample (20 requests from the free
    pool tail) with a 0-error gate — any error aborts (the link must be
    healthy before loss is applied).  Then, per (B, C, lossB, lossC) cell: a
    per-leg pre-cell fidelity gate (``validate.sh --link-b <rtt> <loss>`` +
    ``--link-c <rtt> <loss>``, each with the sibling-commit fractional-loss
    readback — a FAIL aborts), then ``apply.sh --link-b <rtt_b> <loss_b>
    --link-c <rtt_c> <loss_c>`` (float loss), then the two arms
    (3node-P2 -> 3node-SPHP-from-C) against the Node C gateway, then a
    per-leg post-cell gate, then a per-cell error-rate REPORT (no abort —
    loss-driven errors are the signal), then ``clear.sh``.  The summary
    carries the key deliverable: per-cell SPHP-from-C hit rate with a
    Wilson 95% CI (imported from stats.py, not reimplemented).
    Writes ``<out-dir>/campaign_v2_manifest.json`` before any request.
    Exits 1 (with partial artifacts kept) on a sanity or fidelity-gate
    failure.
    """
    spec = V2_RUN_SPECS["three-node-stress"]
    cells = spec["cells"]
    pool_path = str(args.pool)
    endpoint = args.gateway_url.rstrip("/") + "/query/benchmark"

    # Health check at the Node C gateway (fail fast if it is down).
    if not check_health(args.gateway_url, timeout=15):
        print(f"ERROR: gateway health check failed at {args.gateway_url}/health",
              file=sys.stderr)
        sys.exit(1)
    print(f"Gateway healthy: {args.gateway_url} (Node C)")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    run_id = _run_id()
    jsonl_path = out_dir / f"campaign_{run_id}.jsonl"
    if jsonl_path.exists():
        print(f"ERROR: {jsonl_path} already exists; omit --run-id for a "
              f"fresh run.", file=sys.stderr)
        sys.exit(1)

    # Design §5.1: the campaign manifest is written BEFORE any request.
    manifest_path = out_dir / "campaign_v2_manifest.json"
    manifest_doc = build_stress_manifest(
        run_id, pool_path, manifest, line_start, line_end,
        arm_plan, args.query_sample, args.warmups, args.repeats, args.seed)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_doc, f, indent=2)
    print(f"Manifest written: {manifest_path} "
          f"(disjointness asserted: {manifest_doc['disjointness']['asserted']})")

    # Clean slate before any shaping.
    clear_netem(dry_run=False)

    arms_recs: Dict[Tuple[int, int, float, float, str, str],
                    List[Dict[str, Any]]] = {}
    gates_log: List[Dict[str, Any]] = []
    error_reports: List[Dict[str, Any]] = []
    n_ok = 0
    n_err = 0
    n_warmup_ok = 0
    n_warmup_err = 0
    exit_code = 0
    abort_reason: Optional[str] = None

    def _finalize() -> None:
        summary_rows: List[Dict[str, Any]] = []
        for (rtt_b, rtt_c, loss_b, loss_c, mode, variant), recs in arms_recs.items():
            row = summarize_arm(recs)
            # The key deliverable: SPHP-from-C hit rate + Wilson 95% CI
            # (imported from stats.py, not reimplemented).
            row["sphp_hit_rate_wilson"] = sphp_hit_rate_wilson(recs)
            row = {"rtt_b": rtt_b, "rtt_c": rtt_c,
                   "loss_b": loss_b, "loss_c": loss_c,
                   "mode": mode, "variant": variant,
                   "arm": three_node_arm_label(variant),
                   "error_rate": (len([r for r in recs if "error" in r])
                                  / len(recs)) if recs else None,
                   **row}
            summary_rows.append(row)
        summary_rows.sort(key=lambda r: (r["rtt_b"], r["rtt_c"],
                                         r["loss_b"], r["loss_c"],
                                         r["mode"], r["variant"]))
        summary_doc = {
            "schema": "pdc.campaign_summary/v2",
            "design_doc": DESIGN_DOC,
            "design_commit": DESIGN_COMMIT,
            "run": "three-node-stress",
            "run_id": run_id,
            "generated_at": _now_iso(),
            "status": "completed" if abort_reason is None else "aborted",
            "abort_reason": abort_reason,
            "gateway_url": args.gateway_url,
            "pool": manifest_doc["pool"],
            "line_range": [line_start, line_end],
            "seed": args.seed,
            "query_sample": args.query_sample,
            "warmups": args.warmups,
            "repeats": args.repeats,
            "cells": [list(c) for c in cells],
            "variants": spec["variants"],
            "top_k": args.top_k,
            "fidelity_gates": gates_log,
            "per_cell_error_reports": error_reports,
            "n_ok": n_ok,
            "n_errors": n_err,
            "n_warmup_ok": n_warmup_ok,
            "n_warmup_errors": n_warmup_err,
            "summary": summary_rows,
        }
        csv_path, json_path = write_summary_outputs(
            out_dir, run_id, summary_rows, summary_doc)
        print(f"\nJSONL   : {jsonl_path}")
        print(f"CSV     : {csv_path}")
        print(f"JSON    : {json_path}")
        if abort_reason is not None:
            print(f"CAMPAIGN ABORTED: {abort_reason}", file=sys.stderr)
        print("Done.")
        sys.exit(exit_code)

    jsonl_f = open(jsonl_path, "w", encoding="utf-8")
    try:
        # -----------------------------------------------------------------
        # (0,0) unshaped sanity sample: 0-error gate (any error ABORTS —
        # the link must be healthy before loss is applied).
        # -----------------------------------------------------------------
        print("\n=== (0,0) unshaped sanity pre-flight "
              f"(N={spec['sanity_n']}, 0-error gate) ===")
        sanity_pool_start = spec["sanity_pool_start"]
        sanity_n = spec["sanity_n"]
        sanity_reps = spec["sanity_repeats"]
        # The sanity sample is drawn from the free pool tail (a small
        # disjoint block, same pattern as the loss-sweep sanity pre-flight)
        # — it never touches the measured 2921..3838 band.
        sanity_queries = all_queries[sanity_pool_start - 1:
                                     sanity_pool_start - 1 + sanity_n]
        sanity_ok = 0
        sanity_err = 0
        for q in sanity_queries:
            for rep in range(sanity_reps):
                try:
                    res = http_post_json(endpoint, {
                        "query": q, "top_k": args.top_k,
                        "mode": "hybrid", "sphp": False,
                    }, timeout=args.timeout)
                    rec = build_record(
                        run_id, 0, 0, "hybrid", "baseline", rep, q, res,
                        netem_applied=False,
                        extra={
                            "stratum": stratum_for_repeat(rep),
                            "rtt_ping_median_ms": None,
                            "netem_link_b_applied": None,
                            "netem_link_c_applied": None,
                            "pool_file": Path(pool_path).name,
                            "pool_manifest_sha256":
                                manifest.get("_verified_sha256"),
                            "sanity": True,
                        })
                    jsonl_f.write(json.dumps(rec) + "\n")
                    jsonl_f.flush()
                    sanity_ok += 1
                except Exception as e:
                    sanity_err += 1
                    print(f"    [sanity] {q[:40]} rep={rep} failed: {e}",
                          file=sys.stderr)
        if sanity_err > 0:
            clear_netem(dry_run=False)
            abort_reason = (f"(0,0) sanity gate: {sanity_err}/{sanity_n} "
                            f"sample(s) failed — the unshaped link is NOT "
                            f"healthy; aborting before loss is applied.")
            exit_code = 1
            _finalize()
        print(f"    [sanity] {sanity_ok}/{sanity_n} OK, 0 errors "
              f"-> link healthy before loss is applied")

        # -----------------------------------------------------------------
        # Per measured (B, C, lossB, lossC) cell.
        # -----------------------------------------------------------------
        for rtt_b, rtt_c, loss_b, loss_c in cells:
            fb, fc = _fmt_loss(loss_b), _fmt_loss(loss_c)
            print(f"\n=== cell (B={rtt_b}ms/{fb}%, C={rtt_c}ms/{fc}%) "
                  f"[shaped] ===")

            # --- pre-cell per-leg fidelity gate (design §3.2/§6) ---
            # validate.sh --link-b <rtt_b> <loss_b> and --link-c <rtt_c>
            # <loss_c>; the sibling-commit fractional-loss readback is
            # enforced by the script when loss>0.
            pre_gates = run_fidelity_gate_3node(
                rtt_b, loss_b, rtt_c, loss_c, "pre", dry_run=False)
            gates_log.extend(pre_gates)
            for g in pre_gates:
                print(f"    [gate pre]  link-{g['link']} rtt={g['rtt_ms']}ms "
                      f"loss={_fmt_loss(g.get('loss_pct', 0))}%: "
                      f"{g['status']} "
                      f"(baseline={g.get('baseline_median_ms')} ms, "
                      f"shaped={g.get('shaped_median_ms')} ms, "
                      f"added={g.get('added_rtt_ms')} ms, "
                      f"loss_readback={g.get('loss_gate')})")
            if any(g["status"] != "PASS" for g in pre_gates):
                clear_netem(dry_run=False)
                bad = next(g for g in pre_gates if g["status"] != "PASS")
                abort_reason = (f"pre-cell fidelity gate FAIL on link-"
                                f"{bad['link']} at "
                                f"(B={rtt_b}/{_fmt_loss(loss_b)}%, "
                                f"C={rtt_c}/{_fmt_loss(loss_c)}%): "
                                f"{bad.get('output_tail','').strip()[-200:]}")
                exit_code = 1
                _finalize()

            # validate.sh clears netem on exit; re-apply both legs with
            # float loss (sibling-commit fractional-loss apply.sh).
            netem_applied = apply_netem_3node(
                rtt_b, loss_b, rtt_c, loss_c, dry_run=False)

            # --- arms (3node-P2 -> 3node-SPHP-from-C per cell) ---
            for variant in spec["variants"]:
                if not check_health(args.gateway_url, timeout=15):
                    clear_netem(dry_run=False)
                    abort_reason = (f"gateway /health failed before arm "
                                    f"{three_node_arm_label(variant)} at "
                                    f"(B={rtt_b}/{_fmt_loss(loss_b)}%, "
                                    f"C={rtt_c}/{_fmt_loss(loss_c)}%)")
                    exit_code = 1
                    _finalize()
                sphp = sphp_flag_for_variant(variant)
                arm_key = (rtt_b, rtt_c, loss_b, loss_c, "hybrid", variant)
                arm = next(a for a in arm_plan
                           if a["rtt_b"] == rtt_b and a["rtt_c"] == rtt_c
                           and a["loss_b"] == loss_b
                           and a["loss_c"] == loss_c
                           and a["variant"] == variant)
                print(f"  arm {three_node_arm_label(variant):<18} "
                      f"(mode={arm['mode']} variant={variant} sphp={sphp})  "
                      f"pool lines {arm['pool_start'] + 1}..{arm['pool_end']}")

                # Warmups: executed but NOT recorded (disjoint slice).
                for w, wq in enumerate(arm["warmups"]):
                    try:
                        http_post_json(endpoint, {
                            "query": wq, "top_k": args.top_k,
                            "mode": "hybrid", "sphp": sphp,
                        }, timeout=args.timeout)
                        n_warmup_ok += 1
                    except Exception as e:
                        n_warmup_err += 1
                        print(f"    [warmup] {variant} w={w} failed: {e}",
                              file=sys.stderr)

                # Measured: N queries x R repeats back-to-back.
                for query in arm["measured"]:
                    for rep in range(args.repeats):
                        b_gate = next((g for g in pre_gates
                                       if g.get("link") == "b"), None)
                        extra = {
                            "stratum": stratum_for_repeat(rep),
                            "rtt_ping_median_ms":
                                (b_gate or {}).get("shaped_median_ms"),
                            "netem_link_b_applied":
                                {"rtt_ms": rtt_b, "loss_pct": loss_b},
                            "netem_link_c_applied":
                                {"rtt_ms": rtt_c, "loss_pct": loss_c},
                            "pool_file": Path(pool_path).name,
                            "pool_manifest_sha256":
                                manifest.get("_verified_sha256"),
                        }
                        try:
                            res = http_post_json(endpoint, {
                                "query": query, "top_k": args.top_k,
                                "mode": "hybrid", "sphp": sphp,
                            }, timeout=args.timeout)
                            rec = build_record(
                                run_id, rtt_b, loss_b, "hybrid", variant,
                                rep, query, res, netem_applied,
                                extra=extra)
                            n_ok += 1
                        except Exception as e:
                            rec: Dict[str, Any] = {
                                "run_id": run_id,
                                "timestamp": _now_iso(),
                                "query_id": None,
                                "query": query,
                                "rtt_ms": rtt_b,
                                "loss_pct": loss_b,
                                "mode": "hybrid",
                                "variant": variant,
                                "repeat_index": rep,
                                "top_k": args.top_k,
                                "netem_applied": netem_applied,
                                **extra,
                            }
                            rec["error"] = str(e)
                            n_err += 1
                        jsonl_f.write(json.dumps(rec) + "\n")
                        jsonl_f.flush()
                        arms_recs.setdefault(arm_key, []).append(rec)

            # --- cell close: per-cell error-rate REPORT (no abort) ---
            cell_arms = [k for k in arms_recs
                         if k[0] == rtt_b and k[1] == rtt_c
                         and k[2] == loss_b and k[3] == loss_c]
            cell_err = 0
            cell_total = 0
            for arm_key in cell_arms:
                recs = arms_recs[arm_key]
                errs = sum(1 for r in recs if "error" in r)
                total = len(recs)
                cell_err += errs
                cell_total += total
                if total:
                    rate = errs / total
                    # Shaped cells REPORT their per-cell error rate WITHOUT
                    # aborting (loss-driven errors are the signal, not a
                    # campaign failure); the design §6.4 ≤1% is a warning
                    # level only here (a report, not an abort).
                    level = ("WARN" if rate > 0.01 else "OK")
                    print(f"    [error-report] cell "
                          f"(B={rtt_b}/{fb}%, C={rtt_c}/{fc}%) "
                          f"arm {three_node_arm_label(arm_key[5])}: "
                          f"{errs}/{total} = {100.0 * rate:.2f}%  [{level}]")
            error_reports.append({
                "cell": [rtt_b, rtt_c, loss_b, loss_c],
                "n_errors": cell_err,
                "n_total": cell_total,
                "error_rate": (cell_err / cell_total) if cell_total else 0.0,
                "gate": "report-only (no abort for shaped cells)",
            })

            # --- post-cell per-leg fidelity gate (design §6) ---
            post_gates = run_fidelity_gate_3node(
                rtt_b, loss_b, rtt_c, loss_c, "post", dry_run=False)
            gates_log.extend(post_gates)
            for g in post_gates:
                print(f"    [gate post] link-{g['link']} "
                      f"rtt={g['rtt_ms']}ms loss="
                      f"{_fmt_loss(g.get('loss_pct', 0))}%: "
                      f"{g['status']} "
                      f"(shaped={g.get('shaped_median_ms')} ms, "
                      f"added={g.get('added_rtt_ms')} ms, "
                      f"loss_readback={g.get('loss_gate')})")
            if any(g["status"] != "PASS" for g in post_gates):
                clear_netem(dry_run=False)
                bad = next(g for g in post_gates if g["status"] != "PASS")
                abort_reason = (f"post-cell fidelity gate FAIL on link-"
                                f"{bad['link']} at "
                                f"(B={rtt_b}/{fb}%, C={rtt_c}/{fc}%): "
                                f"{bad.get('output_tail','').strip()[-200:]}")
                exit_code = 1
                _finalize()

            clear_netem(dry_run=False)
    finally:
        jsonl_f.close()

    _finalize()



# ---------------------------------------------------------------------------
# v2: genuine-WAN characterization leg (CM-4, final design b239833
# §3.3/§4.3/§5.1).  Netem OFF — the unshaped real cross-site path is the
# subject of this leg.  Distinct from the shaping runs: no added-RTT / loss
# fidelity gate, but a PRISTINE readback precondition (verify no shaping
# remains before the first request) and a per-record real-path RTT probe.
# ---------------------------------------------------------------------------
def build_genuine_wan_arm_list(spec: Dict[str, Any]) -> List[Tuple[str, str, str]]:
    """Return the ordered CM-4 genuine-WAN arm list: ``(cell, arm_label,
    variant)``.

    The 2 real paths (cells) the mesh traverses (design §3.3, amended
    2026-10-14): the 2-node path (client -> Node B, ``10.8.0.2``) and the
    3-node path (client -> Node C, ``10.8.0.3``, the user-facing gateway),
    each run with its two placement arms (2-node = {P2, P2-SPHP}; 3-node =
    {3node-P2, 3node-SPHP-from-C}) = **4 arms**.  Order is cell-major, then
    the arm's declared order within the cell.  ``variant`` is the shared
    baseline/sphp label used for pool-slice layout and the SPHP request
    flag.
    """
    arms: List[Tuple[str, str, str]] = []
    for cell_name, _gw, arm_labels in spec["cells"]:
        for var, arm_label in zip(spec["variants"], arm_labels):
            arms.append((cell_name, arm_label, var))
    return arms


def _genuine_wan_arm_label(cell_name: str, variant: str) -> str:
    """Design §3.3 arm key for a (cell, variant): the 2-node cell maps
    baseline -> ``P2`` / sphp -> ``P2-SPHP``; the 3-node cell maps baseline
    -> ``3node-P2`` / sphp -> ``3node-SPHP-from-C`` (mirrors
    ``arm_label`` / ``three_node_arm_label``)."""
    if cell_name == "3-node":
        return three_node_arm_label(variant)
    return arm_label("hybrid", variant)


def select_pool_slices_genuine_wan(
    all_queries: List[str],
    arms: List[Tuple[str, str, str]],
    n: int,
    warmups: int,
    line_start: int,
    line_end: int,
) -> List[Dict[str, Any]]:
    """Assign each CM-4 genuine-WAN arm a DISJOINT contiguous pool line
    slice.

    Arms are laid out in ``build_genuine_wan_arm_list`` order (2-node P2,
    2-node P2-SPHP, 3-node 3node-P2, 3-node 3node-SPHP-from-C) so the fixed
    line-range (design §5.1: 2701..2920 = 4 x 55) maps one arm per
    55-line block.  Arm ``i`` gets lines ``[line_start + i*(N+W) ..
    line_start + (i+1)*(N+W) - 1]``; the first ``warmups`` are warmups
    (executed, not recorded), the rest measured.

    Fails fast via ``_check_pool_range`` (range size / bounds / duplicates)
    and asserts pairwise disjointness.  Returns one dict per arm with keys
    ``cell, gateway_ip, arm, mode, variant, warmups, measured, pool_start,
    pool_end``.
    """
    spec = V2_RUN_SPECS["genuine-wan"]
    # Map each (cell, arm, variant) to its cell's gateway ip.
    cell_to_ip = {c[0]: c[1] for c in spec["cells"]}
    block = _check_pool_range(all_queries, line_start, line_end,
                              len(arms), n, warmups)
    per_arm = n + warmups
    plan: List[Dict[str, Any]] = []
    for i, (cell, arm_label, variant) in enumerate(arms):
        start = (line_start - 1) + i * per_arm
        end = start + per_arm
        slice_ = block[i * per_arm:(i + 1) * per_arm]
        plan.append({
            "cell": cell,
            "gateway_ip": cell_to_ip[cell],
            "arm": arm_label,
            "mode": "hybrid",
            "variant": variant,
            "warmups": slice_[:warmups],
            "measured": slice_[warmups:end - start],
            "pool_start": start,
            "pool_end": end,
        })
    # Disjointness assertion over all arm slices (warmups + measured).
    sets = [set(a["warmups"]) | set(a["measured"]) for a in plan]
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            assert not (sets[i] & sets[j]), (
                "internal error: genuine-WAN slices overlap")
    return plan


def build_genuine_wan_manifest(
    run_id: str,
    pool_path: str,
    manifest: Dict[str, Any],
    line_start: int,
    line_end: int,
    plan: List[Dict[str, Any]],
    n: int,
    warmups: int,
    repeats: int,
    seed: int,
    pristine: Dict[str, Any],
) -> Dict[str, Any]:
    """Build the ``campaign_v2_manifest.json`` document for the CM-4
    genuine-WAN run (design §5.1).  Same manifest discipline as the other
    v2 runs; each arm records its cell, the per-cell gateway it queries, and
    its placement arm label.  The manifest also records the PRISTINE
    readback precondition outcome (netem OFF verified before the first
    request) and the fact that no added-RTT / loss fidelity gate applies
    (the unshaped path is the subject)."""
    sets = [set(a["warmups"]) | set(a["measured"]) for a in plan]
    disjoint = all(not (sets[i] & sets[j])
                   for i in range(len(sets))
                   for j in range(i + 1, len(sets)))
    spec = V2_RUN_SPECS["genuine-wan"]
    return {
        "schema": "pdc.campaign_manifest/v2",
        "design_doc": DESIGN_DOC,
        "design_commit": DESIGN_COMMIT,
        "run_id": run_id,
        "generated_at_utc": _now_iso(),
        "run": "genuine-wan",
        "pool": {
            "path": pool_path,
            "sha256": manifest.get("_verified_sha256"),
            "manifest_path": str(Path(pool_path).with_name(
                Path(pool_path).name + ".manifest.json")),
            "n": manifest.get("n"),
            "line_start": line_start,
            "line_end": line_end,
        },
        "seed": seed,
        "n_per_arm": n,
        "warmups_per_arm": warmups,
        "repeats_per_query": repeats,
        "cells": [[c[0], c[1], c[2]] for c in spec["cells"]],
        "variants": spec["variants"],
        "shaping": "OFF (netem removed; the unshaped real path is the "
                   "subject — no added-RTT / loss fidelity gate applies)",
        "pristine_precondition": pristine,
        "sanity": {
            "pool_start": spec["sanity_pool_start"],
            "n": spec["sanity_n"],
            "repeats": spec["sanity_repeats"],
            "gate": "0-error unshaped pre-flight (ABORT on any error)",
        },
        "rtt_probe": "per-record rtt_probe_ms (TCP connect to the cell's "
                     "gateway service port; ICMP ping is blocked)",
        "deliverable": "emulation-fidelity table (build_fidelity_table) — "
                       "raw real-path stats recorded here; cost-model "
                       "comparison is the analysis layer's job (design "
                       "§3.3)",
        "arms": [
            {
                "cell": a["cell"],
                "gateway_ip": a["gateway_ip"],
                "arm": a["arm"],
                "mode": a["mode"],
                "variant": a["variant"],
                "pool_start": a["pool_start"] + 1,   # 1-based line numbers
                "pool_end": a["pool_end"],
                "warmups": a["warmups"],
                "measured": a["measured"],
            } for a in plan
        ],
        "disjointness": {
            "asserted": disjoint,
            "n_slices": len(plan),
            "pairwise_intersection_size": 0 if disjoint else -1,
        },
    }


def _tc_qdisc_state() -> str:
    """Return the combined tc qdisc state of the shaped iface + ifb0 (empty
    string on any failure).  This is the same tc readback the fidelity gate
    uses (``readback_loss_pct``); the genuine-WAN pristine check reuses it to
    detect ANY residual netem/htb shaping."""
    out = ""
    try:
        p1 = subprocess.run(["tc", "-s", "qdisc", "show"],
                            capture_output=True, text=True, timeout=30)
        out += (p1.stdout or "")
        p2 = subprocess.run(["tc", "-s", "qdisc", "show", "dev", "ifb0"],
                            capture_output=True, text=True, timeout=30)
        out += (p2.stdout or "")
    except Exception:
        pass
    return out


def verify_netem_pristine(dry_run: bool) -> Tuple[bool, str]:
    """CM-4 pristine precondition (design §3.3): verify NO netem/htb
    shaping remains before the first request.

    Runs ``clear.sh`` (idempotent teardown) and then reads back the tc qdisc
    state (the same tooling the fidelity gate uses) to confirm no ``netem``
    or ``htb`` leaf is active on the iface or ifb0.  Returns ``(pristine,
    detail)`` — ``pristine`` is True only if the readback is clean.  For
    dry-run it is trivially pristine (no shaping was ever applied).
    """
    if dry_run:
        return True, "dry-run (no shaping applied)"
    clear_netem(dry_run=False)
    state = _tc_qdisc_state()
    if "netem" in state or "htb" in state:
        tail = (state or "").strip().splitlines()[-1:] or [""]
        return False, f"shaping still present after clear.sh: {tail[0][:200]}"
    return True, "no netem/htb qdisc present (pristine)"


def rtt_probe_ms(host: str, port: int, timeout: float = 3.0) -> float:
    """Real-path RTT probe (design §3.3): a single TCP connect round trip
    (SYN -> SYN/ACK) to ``host:port`` in ms — the established RTT proxy,
    since ICMP ping is blocked in our environments.  Returns NaN if the
    connect fails.  Same technique the gateway pre-flight harness uses
    (``scripts/node_c_prep/run_node_c_benchmark.py::measure_rtt``)."""
    t0 = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
        return (time.perf_counter() - t0) * 1000.0
    except OSError:
        return float("nan")


def build_fidelity_table(
    records: List[Dict[str, Any]],
    predictions: Optional[Dict[str, Dict[str, float]]] = None,
) -> List[Dict[str, Any]]:
    """Deterministic post-pass producing the CM-4 **emulation-fidelity
    table** (design §3.3 deliverable, LR-7 element-6 fidelity metric).

    ``records`` are the genuine-WAN measured records (each carries its
    placement ``arm``, the per-record ``rtt_probe_ms`` real-path TCP probe,
    and the ``ttft_ms`` / ``total_ms`` wall timings).  ``predictions`` is an
    OPTIONAL mapping ``arm -> {ttft_ms: ...}`` with the cost-model
    predictions for the nearest shaped regime (the analysis layer supplies
    these; the driver records raw stats only, per design §3.3).

    Returns one row per arm (sorted by cell then arm) with the real-path
    RTT distribution (p50 / p95 / IQR) and the per-arm TTFT/E2E p50/p95,
    plus — when a prediction is supplied for that arm — the predicted TTFT
    and the emulated-vs-real TTFT gap.  The function is pure (no I/O, no
    RNG), so it is unit-testable on fixture records.  It is descriptive
    (characterization) — not part of any Holm/BH confirmatory family.
    """
    by_arm: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for r in records:
        if "error" in r:
            continue
        by_arm.setdefault((r.get("cell"), r.get("arm")), []).append(r)
    rows: List[Dict[str, Any]] = []
    for (cell, arm), recs in by_arm.items():
        if not recs:
            continue
        probes = [float(r["rtt_probe_ms"]) for r in recs
                  if r.get("rtt_probe_ms") is not None
                  and not (isinstance(r.get("rtt_probe_ms"), float)
                           and math.isnan(r["rtt_probe_ms"]))]
        ttfts = [float(r["ttft_ms"]) for r in recs
                 if r.get("ttft_ms") is not None]
        totals = [float(r["total_ms"]) for r in recs
                  if r.get("total_ms") is not None]
        rtt_p25 = percentile(probes, 25) if probes else None
        rtt_p50 = percentile(probes, 50) if probes else None
        rtt_p75 = percentile(probes, 75) if probes else None
        rtt_p95 = percentile(probes, 95) if probes else None
        rtt_iqr = (rtt_p75 - rtt_p25) if (rtt_p25 is not None
                                          and rtt_p75 is not None) else None
        row: Dict[str, Any] = {
            "cell": cell,
            "arm": arm,
            "n": len(recs),
            "n_rtt_probes": len(probes),
            "rtt_probe_p50_ms": round(rtt_p50, 3) if rtt_p50 is not None else None,
            "rtt_probe_p95_ms": round(rtt_p95, 3) if rtt_p95 is not None else None,
            "rtt_probe_iqr_ms": round(rtt_iqr, 3) if rtt_iqr is not None else None,
            "ttft_p50_ms": round(percentile(ttfts, 50), 3) if ttfts else None,
            "ttft_p95_ms": round(percentile(ttfts, 95), 3) if ttfts else None,
            "total_p50_ms": round(percentile(totals, 50), 3) if totals else None,
            "total_p95_ms": round(percentile(totals, 95), 3) if totals else None,
        }
        if predictions is not None:
            pred = predictions.get((cell, arm)) or predictions.get(arm)
            if pred:
                pred_ttft = pred.get("ttft_ms")
                row["model_ttft_ms"] = pred_ttft
                if pred_ttft is not None and ttfts:
                    row["ttft_gap_ms"] = round(
                        percentile(ttfts, 50) - pred_ttft, 3)
                else:
                    row["ttft_gap_ms"] = None
        rows.append(row)
    rows.sort(key=lambda r: (r["cell"], r["arm"]))
    return rows


def print_plan_v2_genuine_wan(
    args: argparse.Namespace,
    all_queries: List[str],
    arm_plan: List[Dict[str, Any]],
    manifest: Dict[str, Any],
    line_start: int,
    line_end: int,
) -> None:
    """Dry-run plan for the CM-4 genuine-WAN run (no netem, no HTTP, no
    files)."""
    spec = V2_RUN_SPECS["genuine-wan"]
    n_arms = len(arm_plan)
    per_arm = args.query_sample + args.warmups
    n_measured_per_arm = len(arm_plan[0]["measured"]) * args.repeats
    n_warmups_per_arm = len(arm_plan[0]["warmups"])
    n_total_per_arm = n_warmups_per_arm + n_measured_per_arm
    n_measured = n_arms * n_measured_per_arm
    n_warmups = n_arms * n_warmups_per_arm
    n_total = n_arms * n_total_per_arm
    n_sanity = spec["sanity_n"] * spec["sanity_repeats"]
    n_total_all = n_total + n_sanity
    sets = [set(a["warmups"]) | set(a["measured"]) for a in arm_plan]
    disjoint = all(not (sets[i] & sets[j])
                   for i in range(len(sets))
                   for j in range(i + 1, len(sets)))
    # The 20-request sanity block stays clear of the measured band.
    sanity_start = spec["sanity_pool_start"]
    sanity_end = sanity_start + spec["sanity_n"] - 1

    print("=" * 78)
    print("CAMPAIGN V2 — CM-4 GENUINE-WAN CHARACTERIZATION (netem OFF) — DRY RUN")
    print("=" * 78)
    print(f"  design doc   : {DESIGN_DOC} (commit {DESIGN_COMMIT})")
    print(f"  run type     : genuine-wan  ({spec['label']})")
    print(f"  pool file    : {args.pool}  ({len(all_queries)} lines, "
          f"sha256={manifest.get('_verified_sha256')})")
    print(f"  pool manifest: VERIFIED OK (sha256 + n={manifest.get('n')} "
          f"+ line_count all match)")
    print(f"  pool lines   : {line_start}..{line_end} of the pool file "
          f"(design §5.1; {n_arms} arms x {per_arm} = "
          f"{n_arms * per_arm} lines)")
    print(f"  shaping      : OFF — netem removed; the unshaped real path is "
          f"the subject (NO §3.1-style added-RTT / loss fidelity gate)")
    print(f"  pristine     : clear.sh then tc-qdisc readback BEFORE the "
          f"first request — ABORT if any netem/htb shaping remains")
    print(f"  cells        : {len(spec['cells'])} real cross-site paths — "
          f"2-node (client -> Node B 10.8.0.2) and 3-node (client -> Node C "
          f"10.8.0.3, user-facing)")
    print(f"  arms         : {n_arms} = 2 cells x 2 placement arms "
          f"({', '.join(l for _, _, ls in spec['cells'] for l in ls)}) "
          f"(mode=hybrid, sphp axis)")
    print(f"  per arm      : W={args.warmups} warmups + N={args.query_sample} "
          f"x R={args.repeats} = {n_total_per_arm} requests "
          f"({n_measured_per_arm} measured + {n_warmups_per_arm} warmups)")
    print(f"  rtt probe    : per-record rtt_probe_ms (TCP connect to the "
          f"cell's gateway service port; ICMP ping is blocked)")
    print(f"  sanity       : {n_sanity} unshaped pre-flight requests from the "
          f"free pool tail {sanity_start}..{sanity_end}; 0-error gate — any "
          f"error ABORTS")
    print(f"  deliverable  : emulation-fidelity table (per-arm real-path "
          f"RTT p50/p95/IQR + TTFT/E2E p50/p95) — calibration only, NOT in "
          f"any Holm/BH confirmatory family")
    print(f"  TOTAL        : {n_total} band requests "
          f"({n_measured} measured + {n_warmups} warmups) + {n_sanity} sanity "
          f"= {n_total_all} total")
    print(f"  strata       : repeat 0 = cold-prefix, repeats 1..R-1 = warm "
          f"(no cache flush exists; §5.2)")
    print(f"  campaign manifest: would be written to "
          f"{args.out_dir}/campaign_v2_manifest.json before any request")
    print("-" * 78)
    print("  Execution plan (cells sequentially; netem stays OFF):")
    for cell_name, gw_ip, arm_labels in spec["cells"]:
        tag = (f"NETEM OFF: clear.sh + pristine readback (verify no "
               f"netem/htb) before the first request; query "
               f"{gw_ip}:8000")
        cell_arms = [a for a in arm_plan if a["cell"] == cell_name]
        n_cell_total = sum(len(a["warmups"]) + len(a["measured"]) * args.repeats
                           for a in cell_arms)
        n_cell_measured = sum(len(a["measured"]) * args.repeats
                              for a in cell_arms)
        print(f"    cell {cell_name}  [{tag}]")
        for arm_label in arm_labels:
            arm = next(a for a in cell_arms if a["arm"] == arm_label)
            a_total = (len(arm["warmups"])
                       + len(arm["measured"]) * args.repeats)
            print(f"        arm {arm_label:<18} "
                  f"(mode={arm['mode']} variant={arm['variant']})  "
                  f"-> pool lines {arm['pool_start'] + 1}..{arm['pool_end']}  "
                  f"({len(arm['warmups'])}W + {len(arm['measured'])}x"
                  f"{args.repeats}R = {a_total} req)")
        print(f"        cell subtotal: {n_cell_total} requests "
              f"({n_cell_measured} measured)")
    print("-" * 78)
    print(f"  manifest disjointness: {len(arm_plan)} arm slices, pairwise "
          f"intersection == {'0 (asserted OK)' if disjoint else 'NON-EMPTY (FAIL)'}")
    print("-" * 78)
    print("  DRY RUN complete. No network calls, no netem, no files were made.")


def run_campaign_v2_genuine_wan(
    args: argparse.Namespace,
    all_queries: List[str],
    manifest: Dict[str, Any],
    arm_plan: List[Dict[str, Any]],
    line_start: int,
    line_end: int,
) -> None:
    """Execute the CM-4 genuine-WAN characterization leg (final design
    §3.3/§4.3/§5.1).

    Netem OFF for the whole leg.  Pre-flight: (a) a PRISTINE readback —
    ``clear.sh`` then a tc-qdisc readback (the same tooling the fidelity
    gate uses) confirming NO netem/htb shaping remains; a dirty readback
    ABORTS (exit 1) BEFORE any request.  (b) a 20-request unshaped sanity
    sample from the free pool tail (0-error gate — any error aborts, the
    real link must be healthy).  Then, per cell (2-node then 3-node),
    re-verify the pristine readback before the cell, run the two placement
    arms against the cell's own gateway (the 2-node cell -> Node B
    ``10.8.0.2``, the 3-node cell -> Node C ``10.8.0.3``), and record each
    measured query with its per-record ``rtt_probe_ms`` real-path TCP probe.
    No added-RTT / loss fidelity gate applies (the unshaped path is the
    subject).  The summary carries the **emulation-fidelity table**
    (``build_fidelity_table`` over the measured records).
    Writes ``<out-dir>/campaign_v2_manifest.json`` before any request.
    Exits 1 (with partial artifacts kept) on a pristine or sanity failure.
    """
    spec = V2_RUN_SPECS["genuine-wan"]
    pool_path = str(args.pool)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    run_id = _run_id()
    jsonl_path = out_dir / f"campaign_{run_id}.jsonl"
    if jsonl_path.exists():
        print(f"ERROR: {jsonl_path} already exists; omit --run-id for a "
              f"fresh run.", file=sys.stderr)
        sys.exit(1)

    # -------------------------------------------------------------------
    # PRISTINE precondition (design §3.3): verify NO shaping before the
    # first request; a dirty readback ABORTS before any request is sent.
    # -------------------------------------------------------------------
    pristine_ok, pristine_detail = verify_netem_pristine(dry_run=False)
    if not pristine_ok:
        print(f"ERROR: CM-4 pristine precondition FAILED before any request: "
              f"{pristine_detail}", file=sys.stderr)
        sys.exit(1)
    print(f"Pristine OK (netem OFF): {pristine_detail}")

    # Design §5.1: the campaign manifest is written BEFORE any request.
    manifest_path = out_dir / "campaign_v2_manifest.json"
    manifest_doc = build_genuine_wan_manifest(
        run_id, pool_path, manifest, line_start, line_end,
        arm_plan, args.query_sample, args.warmups, args.repeats, args.seed,
        pristine={"verified": True, "detail": pristine_detail,
                  "shaping": "OFF"})
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_doc, f, indent=2)
    print(f"Manifest written: {manifest_path} "
          f"(disjointness asserted: {manifest_doc['disjointness']['asserted']})")

    arms_recs: Dict[Tuple[str, str, str, str], List[Dict[str, Any]]] = {}
    n_ok = 0
    n_err = 0
    n_warmup_ok = 0
    n_warmup_err = 0
    exit_code = 0
    abort_reason: Optional[str] = None

    def _finalize() -> None:
        summary_rows: List[Dict[str, Any]] = []
        for (cell, arm_label, mode, variant), recs in arms_recs.items():
            row = summarize_arm(recs)
            row = {"cell": cell, "arm": arm_label, "mode": mode,
                   "variant": variant, **row}
            summary_rows.append(row)
        summary_rows.sort(key=lambda r: (r["cell"], r["arm"]))
        fidelity = build_fidelity_table(
            [r for recs in arms_recs.values() for r in recs])
        summary_doc = {
            "schema": "pdc.campaign_summary/v2",
            "design_doc": DESIGN_DOC,
            "design_commit": DESIGN_COMMIT,
            "run": "genuine-wan",
            "run_id": run_id,
            "generated_at": _now_iso(),
            "status": "completed" if abort_reason is None else "aborted",
            "abort_reason": abort_reason,
            "pool": manifest_doc["pool"],
            "line_range": [line_start, line_end],
            "seed": args.seed,
            "query_sample": args.query_sample,
            "warmups": args.warmups,
            "repeats": args.repeats,
            "cells": [[c[0], c[1], c[2]] for c in spec["cells"]],
            "variants": spec["variants"],
            "shaping": "OFF",
            "pristine": manifest_doc["pristine_precondition"],
            "top_k": args.top_k,
            "n_ok": n_ok,
            "n_errors": n_err,
            "n_warmup_ok": n_warmup_ok,
            "n_warmup_errors": n_warmup_err,
            "fidelity_table": fidelity,
            "summary": summary_rows,
        }
        csv_path, json_path = write_summary_outputs(
            out_dir, run_id, summary_rows, summary_doc)
        print(f"\nJSONL   : {jsonl_path}")
        print(f"CSV     : {csv_path}")
        print(f"JSON    : {json_path}  (carries the emulation-fidelity table)")
        if abort_reason is not None:
            print(f"CAMPAIGN ABORTED: {abort_reason}", file=sys.stderr)
        print("Done.")
        sys.exit(exit_code)

    jsonl_f = open(jsonl_path, "w", encoding="utf-8")
    try:
        # -----------------------------------------------------------------
        # Unshaped sanity pre-flight: 20 requests from the free pool tail,
        # 0-error gate (any error ABORTS — the real link must be healthy).
        # -----------------------------------------------------------------
        print(f"\n=== unshaped sanity pre-flight "
              f"(N={spec['sanity_n']}, 0-error gate) ===")
        sanity_start = spec["sanity_pool_start"]
        sanity_n = spec["sanity_n"]
        sanity_reps = spec["sanity_repeats"]
        sanity_qs = all_queries[sanity_start - 1:
                                 sanity_start - 1 + sanity_n]
        sanity_ok = 0
        sanity_err = 0
        for q in sanity_qs:
            for rep in range(sanity_reps):
                try:
                    res = http_post_json(args.gateway_url.rstrip("/")
                                         + "/query/benchmark", {
                        "query": q, "top_k": args.top_k,
                        "mode": "hybrid", "sphp": False,
                    }, timeout=args.timeout)
                    rec = build_record(
                        run_id, 0, 0, "hybrid", "baseline", rep, q, res,
                        netem_applied=False,
                        extra={
                            "stratum": stratum_for_repeat(rep),
                            "rtt_probe_ms": rtt_probe_ms(
                                spec["cells"][0][1], 8000),
                            "netem_link_b_applied": None,
                            "netem_link_c_applied": None,
                            "pool_file": Path(pool_path).name,
                            "pool_manifest_sha256":
                                manifest.get("_verified_sha256"),
                            "sanity": True,
                        })
                    jsonl_f.write(json.dumps(rec) + "\n")
                    jsonl_f.flush()
                    sanity_ok += 1
                except Exception as e:
                    sanity_err += 1
                    print(f"    [sanity] {q[:40]} rep={rep} failed: {e}",
                          file=sys.stderr)
        if sanity_err > 0:
            abort_reason = (f"unshaped sanity gate: {sanity_err}/{sanity_n} "
                            f"sample(s) failed — the real link is NOT "
                            f"healthy; aborting.")
            exit_code = 1
            _finalize()
        print(f"    [sanity] {sanity_ok}/{sanity_n} OK, 0 errors "
              f"-> real link healthy")

        # -----------------------------------------------------------------
        # Per measured cell (2-node then 3-node), netem stays OFF.
        # -----------------------------------------------------------------
        for cell_name, gw_ip, arm_labels in spec["cells"]:
            gw_url = f"http://{gw_ip}:8000"
            print(f"\n=== cell {cell_name} (gateway {gw_ip}:8000) "
                  f"[NETEM OFF] ===")
            # Re-verify the pristine readback before each cell (design
            # §3.3: re-verified before each of the two cells).
            p_ok, p_detail = verify_netem_pristine(dry_run=False)
            if not p_ok:
                abort_reason = (f"CM-4 pristine precondition re-check FAILED "
                                f"before cell {cell_name}: {p_detail}")
                exit_code = 1
                _finalize()
            print(f"    [pristine] {p_detail}")

            for variant, arm_label in zip(spec["variants"], arm_labels):
                if not check_health(gw_url, timeout=15):
                    abort_reason = (f"gateway /health failed before arm "
                                    f"{arm_label} in cell {cell_name} "
                                    f"({gw_url}/health)")
                    exit_code = 1
                    _finalize()
                sphp = (variant == "sphp")
                arm = next(a for a in arm_plan if a["arm"] == arm_label)
                arm_key = (cell_name, arm_label, "hybrid", variant)
                print(f"  arm {arm_label:<18} (mode=hybrid variant={variant} "
                      f"sphp={sphp})  pool lines "
                      f"{arm['pool_start'] + 1}..{arm['pool_end']}")

                # Warmups: executed but NOT recorded (disjoint slice).
                for w, wq in enumerate(arm["warmups"]):
                    try:
                        http_post_json(gw_url + "/query/benchmark", {
                            "query": wq, "top_k": args.top_k,
                            "mode": "hybrid", "sphp": sphp,
                        }, timeout=args.timeout)
                        n_warmup_ok += 1
                    except Exception as e:
                        n_warmup_err += 1
                        print(f"    [warmup] {arm_label} w={w} failed: {e}",
                              file=sys.stderr)

                # Measured: N queries x R repeats; each record carries the
                # per-record real-path RTT probe (design §3.3).
                for query in arm["measured"]:
                    for rep in range(args.repeats):
                        probe = rtt_probe_ms(gw_ip, 8000)
                        extra = {
                            "cell": cell_name,
                            "gateway_ip": gw_ip,
                            "stratum": stratum_for_repeat(rep),
                            "rtt_probe_ms": probe,
                            "netem_link_b_applied": None,
                            "netem_link_c_applied": None,
                            "pool_file": Path(pool_path).name,
                            "pool_manifest_sha256":
                                manifest.get("_verified_sha256"),
                        }
                        try:
                            res = http_post_json(gw_url + "/query/benchmark", {
                                "query": query, "top_k": args.top_k,
                                "mode": "hybrid", "sphp": sphp,
                            }, timeout=args.timeout)
                            rec = build_record(
                                run_id, 0, 0, "hybrid", variant, rep,
                                query, res, netem_applied=False,
                                extra=extra)
                            n_ok += 1
                        except Exception as e:
                            rec: Dict[str, Any] = {
                                "run_id": run_id,
                                "timestamp": _now_iso(),
                                "query_id": None,
                                "query": query,
                                "rtt_ms": 0,
                                "loss_pct": 0,
                                "mode": "hybrid",
                                "variant": variant,
                                "repeat_index": rep,
                                "top_k": args.top_k,
                                "netem_applied": False,
                                **extra,
                            }
                            rec["error"] = str(e)
                            n_err += 1
                        jsonl_f.write(json.dumps(rec) + "\n")
                        jsonl_f.flush()
                        arms_recs.setdefault(arm_key, []).append(rec)
    finally:
        jsonl_f.close()

    _finalize()


# ---------------------------------------------------------------------------
# v2: loss-sweep run (amended design §3.1/§4.3/§5.1, amended 2026-10-14)
# ---------------------------------------------------------------------------
def loss_sweep_cell_gate(rtt: int, loss: float, when: str,
                         dry_run: bool) -> Dict[str, Any]:
    """Per-cell fidelity gate for the loss-sweep run (design §3.1).

    - rtt > 0: ``validate.sh`` 11-ping ±30% added-RTT + fractional-loss
      readback.
    - rtt == 0: tc-based loss readback only (no added-RTT gate; there is no
      unshaped baseline cell in this run — every cell is shaped).
    """
    return run_fidelity_gate(rtt, when, dry_run, loss=loss)


def run_campaign_v2_losssweep(
    args: argparse.Namespace,
    all_queries: List[str],
    manifest: Dict[str, Any],
    arm_plan: List[Dict[str, Any]],
    line_start: int,
    line_end: int,
) -> None:
    """Execute the amended-design loss-sweep (2-node, §3.1/§6).

    Pre-flight: per-RTT-tier loss-0 sanity samples (0-error gate — any
    error ABORTS, the shaped link must be healthy before loss is applied).
    Then, per (rtt, loss) cell: a pre-cell fidelity gate (11-ping ±30%
    added RTT + loss readback; rtt-0 cells get loss-readback only), apply,
    both arms (P2 → P2-SPHP), a post-cell gate, a per-cell error-rate
    REPORT (no abort — loss-driven errors are the signal), then clear.  The
    summary carries the key deliverable: per-cell {rtt, loss} targets +
    per-cell 11-ping medians, and the per-cell SPHP hit rate with a Wilson
    95% CI (lazily imported from stats.py).

    Writes ``<out-dir>/campaign_v2_manifest.json`` before any request.
    Exits 1 (with partial artifacts kept) on a sanity or fidelity-gate
    failure.
    """
    spec = V2_RUN_SPECS["loss-sweep"]
    cells = spec["cells"]
    pool_path = str(args.pool)
    endpoint = args.gateway_url.rstrip("/") + "/query/benchmark"

    # Health check (fail fast).
    if not check_health(args.gateway_url, timeout=15):
        print(f"ERROR: gateway health check failed at {args.gateway_url}/health",
              file=sys.stderr)
        sys.exit(1)
    print(f"Gateway healthy: {args.gateway_url}")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    run_id = _run_id()
    jsonl_path = out_dir / f"campaign_{run_id}.jsonl"
    if jsonl_path.exists():
        print(f"ERROR: {jsonl_path} already exists.", file=sys.stderr)
        sys.exit(1)

    # Design §5.1: the campaign manifest is written BEFORE any request.
    sanity_ranges = v2_sanity_line_ranges(
        "loss-sweep", spec["rtt_tiers"], spec["sanity_n"])
    manifest_path = out_dir / "campaign_v2_manifest.json"
    manifest_doc = build_loss_sweep_manifest(
        run_id, pool_path, manifest, line_start, line_end,
        arm_plan, args.query_sample, args.warmups, args.repeats,
        args.seed, sanity_ranges)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_doc, f, indent=2)
    print(f"Manifest written: {manifest_path} "
          f"(disjointness asserted: {manifest_doc['disjointness']['asserted']})")

    clear_netem(dry_run=False)

    arms_recs: Dict[Tuple[int, float, str, str], List[Dict[str, Any]]] = {}
    gates_log: List[Dict[str, Any]] = []
    error_reports: List[Dict[str, Any]] = []
    n_ok = 0
    n_err = 0
    n_warmup_ok = 0
    n_warmup_err = 0
    exit_code = 0
    abort_reason: Optional[str] = None
    # Per-RTT-tier sanity accounting (drives the 0-error pre-flight gate).
    sanity_counts: Dict[int, Dict[str, int]] = {
        rtt: {"n_ok": 0, "n_err": 0} for rtt in spec["rtt_tiers"]
    }

    def _finalize() -> None:
        summary_rows: List[Dict[str, Any]] = []
        for (rtt, loss, mode, variant), recs in arms_recs.items():
            row = summarize_arm(recs)
            # Key deliverable: per-cell SPHP hit rate + Wilson 95% CI.
            if variant == "sphp":
                row["sphp_hit_rate_wilson"] = sphp_hit_rate_wilson(recs)
            row = {"rtt_ms": rtt, "loss_pct": loss, "mode": mode,
                   "variant": variant,
                   "arm": arm_label(mode, variant),
                   "error_rate": (len([r for r in recs if "error" in r])
                                  / len(recs)) if recs else None,
                   **row}
            summary_rows.append(row)
        summary_rows.sort(key=lambda r: (r["rtt_ms"], r["loss_pct"],
                                         r["mode"], r["variant"]))
        # Per-cell 11-ping medians (the rtt-0 cells have no added-RTT
        # gate, so their shaped median stays None).
        cell_ping_medians: Dict[str, Optional[float]] = {}
        for g in gates_log:
            key = f"{g['rtt_ms']}-{g.get('loss_pct', 0)}"
            if g.get("shaped_median_ms") is not None:
                cell_ping_medians[key] = g["shaped_median_ms"]
        summary_doc = {
            "schema": "pdc.campaign_summary/v2",
            "design_doc": DESIGN_DOC,
            "design_commit": DESIGN_COMMIT,
            "run": "loss-sweep",
            "run_id": run_id,
            "generated_at": _now_iso(),
            "status": "completed" if abort_reason is None else "aborted",
            "abort_reason": abort_reason,
            "gateway_url": args.gateway_url,
            "pool": manifest_doc["pool"],
            "line_range": [line_start, line_end],
            "seed": args.seed,
            "query_sample": args.query_sample,
            "warmups": args.warmups,
            "repeats": args.repeats,
            "rtt_tiers": spec["rtt_tiers"],
            "cells": [list(c) for c in cells],
            "variants": spec["variants"],
            "cell_ping_medians": cell_ping_medians,
            "sanity": {f"rtt_{r}": v for r, v in sanity_counts.items()},
            "fidelity_gates": gates_log,
            "per_cell_error_reports": error_reports,
            "n_ok": n_ok,
            "n_errors": n_err,
            "n_warmup_ok": n_warmup_ok,
            "n_warmup_errors": n_warmup_err,
            "summary": summary_rows,
        }
        csv_path, json_path = write_summary_outputs(
            out_dir, run_id, summary_rows, summary_doc)
        print(f"\nJSONL   : {jsonl_path}")
        print(f"CSV     : {csv_path}")
        print(f"JSON    : {json_path}")
        if abort_reason is not None:
            print(f"CAMPAIGN ABORTED: {abort_reason}", file=sys.stderr)
        print("Done.")
        sys.exit(exit_code)

    jsonl_f = open(jsonl_path, "w", encoding="utf-8")
    try:
        # -----------------------------------------------------------------
        # Per-RTT-tier loss-0 sanity pre-flight: 0-error gate (any error
        # ABORTS — the shaped link must be healthy before loss is applied).
        # -----------------------------------------------------------------
        for tier_idx, rtt in enumerate(spec["rtt_tiers"]):
            print(f"\n=== loss-0 sanity pre-flight (rtt={rtt}ms, "
                  f"N={spec['sanity_n']}, 0-error gate) ===")
            r_start, r_end = sanity_ranges[tier_idx]
            sanity_qs = all_queries[r_start - 1:r_end]
            t_ok = 0
            t_err = 0
            for q in sanity_qs:
                for rep in range(spec["sanity_repeats"]):
                    extra = {
                        "stratum": stratum_for_repeat(rep),
                        "rtt_ping_median_ms": None,
                        "netem_link_b_applied": {"rtt_ms": rtt,
                                                 "loss_pct": 0.0},
                        "netem_link_c_applied": None,
                        "pool_file": Path(pool_path).name,
                        "pool_manifest_sha256":
                            manifest.get("_verified_sha256"),
                        "sanity": True,
                        "sanity_rtt_ms": rtt,
                    }
                    try:
                        res = http_post_json(endpoint, {
                            "query": q, "top_k": args.top_k,
                            "mode": "hybrid", "sphp": False,
                        }, timeout=args.timeout)
                        rec = build_record(
                            run_id, rtt, 0, "hybrid", "baseline", rep, q,
                            res, netem_applied=False, extra=extra)
                        jsonl_f.write(json.dumps(rec) + "\n")
                        jsonl_f.flush()
                        t_ok += 1
                    except Exception as e:
                        rec = {
                            "run_id": run_id,
                            "timestamp": _now_iso(),
                            "query_id": None,
                            "query": q,
                            "rtt_ms": rtt,
                            "loss_pct": 0,
                            "mode": "hybrid",
                            "variant": "baseline",
                            "repeat_index": rep,
                            "top_k": args.top_k,
                            "netem_applied": False,
                            **extra,
                        }
                        rec["error"] = str(e)
                        jsonl_f.write(json.dumps(rec) + "\n")
                        jsonl_f.flush()
                        t_err += 1
                        print(f"    [sanity] {q[:40]} rep={rep} failed: {e}",
                              file=sys.stderr)
            sanity_counts[rtt] = {"n_ok": t_ok, "n_err": t_err}
            if t_err > 0:
                clear_netem(dry_run=False)
                abort_reason = (f"loss-0 sanity gate (rtt={rtt}): "
                                f"{t_err}/{spec['sanity_n']} sample(s) "
                                f"failed — the shaped link is NOT healthy; "
                                f"aborting before loss is applied.")
                exit_code = 1
                _finalize()
            print(f"    [sanity] rtt={rtt}: {t_ok}/{spec['sanity_n']} OK, "
                  f"0 errors -> link healthy before loss is applied")

        # -----------------------------------------------------------------
        # Per (rtt, loss) cell.
        # -----------------------------------------------------------------
        for rtt, loss in cells:
            fb = _fmt_loss(loss)
            print(f"\n=== cell rtt={rtt}ms loss={fb}% [shaped] ===")

            # --- pre-cell fidelity gate (design §3.1/§6) ---
            pre_gate = loss_sweep_cell_gate(rtt, loss, "pre", dry_run=False)
            gates_log.append(pre_gate)
            print(f"    [gate pre]  rtt={rtt} loss={fb}%: {pre_gate['status']} "
                  f"(shaped={pre_gate.get('shaped_median_ms')} ms, "
                  f"loss_readback={pre_gate.get('loss_gate')})")
            if pre_gate["status"] != "PASS":
                clear_netem(dry_run=False)
                abort_reason = (f"pre-cell fidelity gate FAIL at "
                                f"rtt={rtt} loss={fb}%: "
                                f"{pre_gate.get('output_tail', '').strip()[-200:]}")
                exit_code = 1
                _finalize()

            # validate.sh clears netem on exit; re-apply.
            netem_applied = apply_netem_link_b(rtt, loss, dry_run=False)

            # --- arms (P2 -> P2-SPHP per cell) ---
            for variant in spec["variants"]:
                if not check_health(args.gateway_url, timeout=15):
                    clear_netem(dry_run=False)
                    abort_reason = (f"gateway /health failed before arm "
                                    f"{arm_label('hybrid', variant)} at "
                                    f"rtt={rtt} loss={fb}%")
                    exit_code = 1
                    _finalize()
                sphp = sphp_flag_for_variant(variant)
                arm_key = (rtt, loss, "hybrid", variant)
                arm = next(a for a in arm_plan
                           if (a["rtt_ms"], a["loss_pct"],
                               a["mode"], a["variant"]) == arm_key)
                print(f"  arm {arm_label('hybrid', variant):<9} "
                      f"(mode=hybrid variant={variant} sphp={sphp})  "
                      f"pool lines {arm['pool_start'] + 1}..{arm['pool_end']}")

                # Warmups: executed but NOT recorded (disjoint slice).
                for w, wq in enumerate(arm["warmups"]):
                    try:
                        http_post_json(endpoint, {
                            "query": wq, "top_k": args.top_k,
                            "mode": "hybrid", "sphp": sphp,
                        }, timeout=args.timeout)
                        n_warmup_ok += 1
                    except Exception as e:
                        n_warmup_err += 1
                        print(f"    [warmup] {variant} w={w} failed: {e}",
                              file=sys.stderr)

                # Measured: N queries x R repeats back-to-back (R=1).
                for query in arm["measured"]:
                    for rep in range(args.repeats):
                        extra = {
                            "stratum": stratum_for_repeat(rep),
                            "rtt_ping_median_ms":
                                pre_gate.get("shaped_median_ms"),
                            "netem_link_b_applied":
                                {"rtt_ms": rtt, "loss_pct": loss},
                            "netem_link_c_applied": None,
                            "pool_file": Path(pool_path).name,
                            "pool_manifest_sha256":
                                manifest.get("_verified_sha256"),
                        }
                        try:
                            res = http_post_json(endpoint, {
                                "query": query, "top_k": args.top_k,
                                "mode": "hybrid", "sphp": sphp,
                            }, timeout=args.timeout)
                            rec = build_record(
                                run_id, rtt, loss, "hybrid", variant,
                                rep, query, res, netem_applied, extra=extra)
                            n_ok += 1
                        except Exception as e:
                            rec = {
                                "run_id": run_id,
                                "timestamp": _now_iso(),
                                "query_id": None,
                                "query": query,
                                "rtt_ms": rtt,
                                "loss_pct": loss,
                                "mode": "hybrid",
                                "variant": variant,
                                "repeat_index": rep,
                                "top_k": args.top_k,
                                "netem_applied": netem_applied,
                                **extra,
                            }
                            rec["error"] = str(e)
                            n_err += 1
                        jsonl_f.write(json.dumps(rec) + "\n")
                        jsonl_f.flush()
                        arms_recs.setdefault(arm_key, []).append(rec)

            # --- cell close: per-cell error-rate REPORT (no abort) ---
            cell_err = 0
            cell_total = 0
            for arm_key, recs in arms_recs.items():
                if arm_key[0] == rtt and arm_key[1] == loss:
                    cell_err += sum(1 for r in recs if "error" in r)
                    cell_total += len(recs)
            rate = (cell_err / cell_total) if cell_total else 0.0
            level = "WARN" if rate > 0.01 else "OK"
            print(f"    [error-report] cell rtt={rtt} loss={fb}%: "
                  f"{cell_err}/{cell_total} = {100.0 * rate:.2f}%  "
                  f"[{level}] (report-only, no abort)")
            error_reports.append({
                "cell": [rtt, loss],
                "n_errors": cell_err,
                "n_total": cell_total,
                "error_rate": rate,
                "gate": "report-only (no abort for loss>0 cells)",
            })

            # --- post-cell fidelity gate (design §3.1/§6) ---
            post_gate = loss_sweep_cell_gate(rtt, loss, "post",
                                             dry_run=False)
            gates_log.append(post_gate)
            print(f"    [gate post] rtt={rtt} loss={fb}%: {post_gate['status']} "
                  f"(shaped={post_gate.get('shaped_median_ms')} ms, "
                  f"loss_readback={post_gate.get('loss_gate')})")
            if post_gate["status"] != "PASS":
                clear_netem(dry_run=False)
                abort_reason = (f"post-cell fidelity gate FAIL at "
                                f"rtt={rtt} loss={fb}%: "
                                f"{post_gate.get('output_tail', '').strip()[-200:]}")
                exit_code = 1
                _finalize()

            clear_netem(dry_run=False)
    finally:
        jsonl_f.close()

    _finalize()


def build_loss_sweep_manifest(
    run_id: str,
    pool_path: str,
    manifest: Dict[str, Any],
    line_start: int,
    line_end: int,
    plan: List[Dict[str, Any]],
    n: int,
    warmups: int,
    repeats: int,
    seed: int,
    sanity_ranges: List[Tuple[int, int]],
) -> Dict[str, Any]:
    """Build the ``campaign_v2_manifest.json`` document for the loss-sweep
    run (design §5.1).  Records the per-cell {rtt, loss} targets, the
    per-RTT-tier loss-0 sanity sample ranges, and the per-cell gate
    semantics (0-error for the loss-0 sanity pre-flight; report-only for
    the loss>0 measured cells)."""
    disjoint = slices_disjoint(plan)
    spec = V2_RUN_SPECS["loss-sweep"]
    return {
        "schema": "pdc.campaign_manifest/v2",
        "design_doc": DESIGN_DOC,
        "design_commit": DESIGN_COMMIT,
        "run_id": run_id,
        "generated_at_utc": _now_iso(),
        "run": "loss-sweep",
        "pool": {
            "path": pool_path,
            "sha256": manifest.get("_verified_sha256"),
            "manifest_path": str(Path(pool_path).with_name(
                Path(pool_path).name + ".manifest.json")),
            "n": manifest.get("n"),
            "line_start": line_start,
            "line_end": line_end,
        },
        "seed": seed,
        "n_per_arm": n,
        "warmups_per_arm": warmups,
        "repeats_per_query": repeats,
        "rtt_tiers": spec["rtt_tiers"],
        "cells": [list(c) for c in spec["cells"]],
        "variants": spec["variants"],
        "sanity": {
            "n": spec["sanity_n"],
            "repeats": spec["sanity_repeats"],
            "ranges": [list(r) for r in sanity_ranges],
            "gate": "0-error (ABORT on any error — the shaped link must be "
                    "healthy before loss is applied)",
        },
        "per_cell_gate_semantics": {
            "loss_0_sanity_preflight":
                "0-error SANITY gate (ABORT on any error)",
            "loss_gt_0_measured_cells":
                "per-cell error-rate REPORT (no abort; loss-driven errors "
                "are the signal)",
            "fidelity":
                "validate.sh 11-ping ±30% added-RTT + fractional-loss "
                "readback, pre and post every cell; rtt-0 cells: "
                "loss-readback only (no added-RTT gate)",
        },
        "arms": [
            {
                "arm": arm_label(a["mode"], a["variant"]),
                "rtt_ms": a["rtt_ms"],
                "loss_pct": a["loss_pct"],
                "mode": a["mode"],
                "variant": a["variant"],
                "netem_link_b_applied": {"rtt_ms": a["rtt_ms"],
                                         "loss_pct": a["loss_pct"]},
                "pool_start": a["pool_start"] + 1,
                "pool_end": a["pool_end"],
                "warmups": a["warmups"],
                "measured": a["measured"],
            } for a in plan
        ],
        "disjointness": {
            "asserted": disjoint,
            "n_slices": len(plan),
            "pairwise_intersection_size": 0 if disjoint else -1,
        },
    }


def print_plan_v2_losssweep(
    args: argparse.Namespace,
    all_queries: List[str],
    arm_plan: List[Dict[str, Any]],
    manifest: Dict[str, Any],
    line_start: int,
    line_end: int,
) -> None:
    """Dry-run plan for the loss-sweep run (no netem, no HTTP, no files)."""
    spec = V2_RUN_SPECS["loss-sweep"]
    cells = spec["cells"]
    n_arms = len(arm_plan)
    per_arm = args.query_sample + args.warmups
    n_measured_per_arm = len(arm_plan[0]["measured"]) * args.repeats
    n_warmups_per_arm = len(arm_plan[0]["warmups"])
    n_total_per_arm = n_warmups_per_arm + n_measured_per_arm
    n_measured = n_arms * n_measured_per_arm
    n_warmups = n_arms * n_warmups_per_arm
    n_total = n_arms * n_total_per_arm
    sanity_ranges = v2_sanity_line_ranges(
        "loss-sweep", spec["rtt_tiers"], spec["sanity_n"])
    n_sanity_total = sum(r_end - r_start + 1 for r_start, r_end in
                         sanity_ranges)
    disjoint = slices_disjoint(arm_plan)

    print("=" * 78)
    print("CAMPAIGN V2 — 2-NODE LOSS SWEEP (AMENDED DESIGN) — DRY RUN")
    print("=" * 78)
    print(f"  design doc   : {DESIGN_DOC} (commit {DESIGN_COMMIT})")
    print(f"  run type     : loss-sweep  ({spec['label']})")
    print(f"  gateway      : {args.gateway_url} (Node B)")
    print(f"  pool file    : {args.pool}  ({len(all_queries)} lines, "
          f"sha256={manifest.get('_verified_sha256')})")
    print(f"  pool manifest: VERIFIED OK (sha256 + n={manifest.get('n')} "
          f"+ line_count all match)")
    print(f"  pool lines   : {line_start}..{line_end} of the pool file "
          f"(design §5.1; {n_arms} arms x {per_arm} = "
          f"{n_arms * per_arm} lines)")
    print(f"  cells        : RTT {spec['rtt_tiers']} ms x loss "
          f"{{0.5, 1, 2}}% = {len(cells)} cells (link-B only)")
    print(f"  arms         : {n_arms} = {len(cells)} cells x "
          f"{{P2, P2-SPHP}} (mode=hybrid, sphp axis on)")
    print(f"  per arm      : W={args.warmups} warmups + N={args.query_sample} "
          f"x R={args.repeats} = {n_total_per_arm} requests "
          f"({n_measured_per_arm} measured + {n_warmups_per_arm} warmups)")
    print(f"  sanity       : per-RTT-tier loss-0 pre-flight "
          f"(N={spec['sanity_n']}/tier x {len(spec['rtt_tiers'])} tiers = "
          f"{n_sanity_total}), pool tail {sanity_ranges[0][0]}..; "
          f"0-error gate — any error ABORTS")
    print(f"  error gate   : loss>0 cells REPORT per-cell error rate "
          f"WITHOUT aborting (loss-driven errors are the signal)")
    print(f"  fidelity     : validate.sh 11-ping ±30% added-RTT + "
          f"fractional-loss readback (pre+post every cell); "
          f"rtt-0 cells: loss-readback only (no added-RTT)")
    print(f"  key deliverable: per-cell SPHP hit rate with Wilson 95% CI "
          f"(imported from experiments/analysis/stats.py::wilson_ci) + "
          f"per-cell {{rtt, loss}} targets + 11-ping medians in the manifest")
    print(f"  strata       : R={args.repeats} (repeat 0 = cold-prefix)")
    print(f"  TOTAL        : {n_total} requests "
          f"({n_measured} measured + {n_warmups} warmups) + "
          f"{n_sanity_total} sanity = {n_total + n_sanity_total} total")
    print(f"  campaign manifest: would be written to "
          f"{args.out_dir}/campaign_v2_manifest.json before any request")
    print("-" * 78)
    print("  Execution plan (cells sequentially; arms P2 -> P2-SPHP "
          "per cell):")
    for rtt, loss in cells:
        fb = _fmt_loss(loss)
        if rtt == 0:
            tag = (f"shaped (loss-only): apply --link-b 0 {fb}; "
                   f"gate pre+post (tc loss-readback only); clear after")
        else:
            tag = (f"shaped: apply --link-b {rtt} {fb}; "
                   f"gate pre+post (11-ping ±30% + loss readback); "
                   f"clear after")
        cell_arms = [a for a in arm_plan
                     if a["rtt_ms"] == rtt and a["loss_pct"] == loss]
        n_cell_total = sum(len(a["warmups"]) + len(a["measured"]) * args.repeats
                           for a in cell_arms)
        n_cell_measured = sum(len(a["measured"]) * args.repeats
                              for a in cell_arms)
        print(f"    cell rtt={rtt:>3}ms loss={fb}%  [{tag}]")
        for variant in spec["variants"]:
            arm = next(a for a in cell_arms if a["variant"] == variant)
            a_total = (len(arm["warmups"])
                       + len(arm["measured"]) * args.repeats)
            print(f"        arm {arm_label(arm['mode'], variant):<9} "
                  f"(mode={arm['mode']} variant={variant})  "
                  f"-> pool lines {arm['pool_start'] + 1}..{arm['pool_end']}  "
                  f"({len(arm['warmups'])}W + {len(arm['measured'])}x"
                  f"{args.repeats}R = {a_total} req)")
        print(f"        cell subtotal: {n_cell_total} requests "
              f"({n_cell_measured} measured)")
    print("-" * 78)
    print(f"  manifest disjointness: {len(arm_plan)} arm slices, pairwise "
          f"intersection == {'0 (asserted OK)' if disjoint else 'NON-EMPTY (FAIL)'}")
    print("-" * 78)
    print("  DRY RUN complete. No network calls, no netem, no files were made.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    args = build_parser().parse_args()

    if is_v2(args) and args.queries != V1_DEFAULT_QUERIES:
        print("ERROR: --pool and --queries are mutually exclusive "
              "(v2 uses the pool file and its manifest sidecar)", file=sys.stderr)
        sys.exit(1)

    # v2 mode: resolve the default pool / run type.
    #   --pool FILE            -> v2, pool = FILE, run defaults to
    #                             'confirmatory' (the historical surface);
    #   --run <type> [pool]    -> v2, run = <type>, pool defaults to the
    #                             design §5.1 master pool.
    if is_v2(args):
        if args.pool is None:
            args.pool = V2_DEFAULT_POOL
        if args.run is None:
            args.run = "confirmatory"

    queries_path = resolve_queries_path(args.pool if is_v2(args) else args.queries)
    try:
        all_queries = load_queries(queries_path)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    if not all_queries:
        print(f"ERROR: no queries loaded from {queries_path}", file=sys.stderr)
        sys.exit(1)

    # v2: verify the pool against its manifest sidecar BEFORE anything else
    # (fail fast on any sha256 / n / line_count mismatch).
    manifest: Optional[Dict[str, Any]] = None
    if is_v2(args):
        try:
            manifest = verify_pool_manifest(queries_path)
        except ManifestError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)

    resolve_mode_defaults(args)

    if is_v2(args) and args.run == "three-node":
        # WS-CM 3-node matrix (design §2.2/§3.2): 3 (B, C) cells x
        # {3node-P2, 3node-SPHP-from-C} = 6 arms, pool lines 1371..1910.
        spec = V2_RUN_SPECS["three-node"]
        arms = build_3node_arm_list(spec)
        n_arms = len(arms)
        line_start, line_end = v2_line_range(
            "three-node", n_arms, args.query_sample, args.warmups)
        try:
            arm_plan = select_pool_slices_3node(
                all_queries, spec["cells"], spec["variants"],
                args.query_sample, args.warmups, line_start, line_end)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)
        if args.dry_run:
            print_plan_v2_3node(args, all_queries, arm_plan, manifest,
                                line_start, line_end)
            return
        run_campaign_v3node(args, all_queries, manifest, arm_plan,
                            line_start, line_end)
        return

    if is_v2(args) and args.run == "loss-sweep":
        # Amended-design 2-node loss sweep (design §3.1/§4.3/§5.1, amended
        # 2026-10-14): RTT{0,40} x loss{0.5,1,2} = 6 cells x 2 arms = 12
        # arms, N=50 W=5 R=1 -> 660 lines, pool band 2041..2700.
        spec = V2_RUN_SPECS["loss-sweep"]
        arms = build_arm_list(args)
        n_arms = len(arms)
        line_start, line_end = v2_line_range(
            "loss-sweep", n_arms, args.query_sample, args.warmups)
        try:
            arm_plan = select_pool_slices(
                all_queries, arms, args.query_sample, args.warmups,
                line_start, line_end)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)
        if args.dry_run:
            print_plan_v2_losssweep(args, all_queries, arm_plan, manifest,
                                    line_start, line_end)
            return
        run_campaign_v2_losssweep(args, all_queries, manifest, arm_plan,
                                  line_start, line_end)
        return

    if is_v2(args) and args.run == "three-node-stress":
        # CM-5 3-node per-leg loss/RTT stress matrix (final design §3.4,
        # finalized in b239833): 27 (B, C, lossB, lossC) cells x 2 arms =
        # 54 arms, N=15 / W=2 / R=1 -> 54 x 17 = 918 lines -> pool band
        # 2921..3838 (zero slack); (0,0) sanity from free tail 3839..
        spec = V2_RUN_SPECS["three-node-stress"]
        arms = build_stress_arm_list(spec)
        n_arms = len(arms)
        line_start, line_end = v2_line_range(
            "three-node-stress", n_arms, args.query_sample, args.warmups)
        try:
            arm_plan = select_pool_slices_stress(
                all_queries, spec["cells"], spec["variants"],
                args.query_sample, args.warmups, line_start, line_end)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)
        if args.dry_run:
            print_plan_v3node_stress(args, all_queries, arm_plan, manifest,
                                     line_start, line_end)
            return
        run_campaign_v3node_stress(args, all_queries, manifest, arm_plan,
                                   line_start, line_end)
        return

    if is_v2(args) and args.run == "genuine-wan":
        # CM-4 genuine-WAN characterization leg (final design §3.3/§4.3/
        # §5.1, finalized in b239833): 2 real cross-site paths x 2 placement
        # arms = 4 arms, N=50 / W=5 / R=1 -> 4 x (50+5) = 220 lines -> pool
        # band 2701..2920 (zero slack); netem OFF; pristine readback
        # precondition + per-record real-path RTT probe; 20-request unshaped
        # sanity pre-flight from free tail 3839..
        spec = V2_RUN_SPECS["genuine-wan"]
        arms = build_genuine_wan_arm_list(spec)
        n_arms = len(arms)
        line_start, line_end = v2_line_range(
            "genuine-wan", n_arms, args.query_sample, args.warmups)
        try:
            arm_plan = select_pool_slices_genuine_wan(
                all_queries, arms, args.query_sample, args.warmups,
                line_start, line_end)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)
        if args.dry_run:
            print_plan_v2_genuine_wan(args, all_queries, arm_plan, manifest,
                                      line_start, line_end)
            return
        run_campaign_v2_genuine_wan(args, all_queries, manifest, arm_plan,
                                    line_start, line_end)
        return

    # Build the full arm list FIRST, then assign each arm its query slice.
    arms = build_arm_list(args)

    if is_v2(args):
        line_start, line_end = v2_line_range(
            args.run, len(arms), args.query_sample, args.warmups)
        try:
            arm_plan = select_pool_slices(
                all_queries, arms, args.query_sample, args.warmups,
                line_start, line_end)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)
        if args.dry_run:
            print_plan_v2(args, all_queries, arm_plan, manifest,
                          line_start, line_end)
            return
        run_campaign_v2(args, all_queries, manifest, arm_plan,
                        line_start, line_end)
        return

    # ------------------------------ v1 path --------------------------------
    try:
        arm_plan = build_arm_query_plan(
            all_queries, arms, args.query_sample, args.warmups, args.seed)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    # Index the plan by arm key for O(1) lookup in the run loop.
    arm_plan_by_key = {
        (a["rtt_ms"], a["loss_pct"], a["mode"], a["variant"]): a
        for a in arm_plan
    }

    endpoint = args.gateway_url.rstrip("/") + "/query/benchmark"

    if args.dry_run:
        print_plan(args, all_queries, arm_plan)
        return

    # Health check (fail fast if the gateway is down).
    if not check_health(args.gateway_url, timeout=15):
        print(f"ERROR: gateway health check failed at {args.gateway_url}/health",
              file=sys.stderr)
        sys.exit(1)
    print(f"Gateway healthy: {args.gateway_url}")

    # Ensure a clean slate before any shaping.
    clear_netem(dry_run=False)

    run_id = _run_id()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = out_dir / f"campaign_{run_id}.jsonl"
    csv_path = out_dir / f"summary_{run_id}.csv"
    json_path = out_dir / f"summary_{run_id}.json"

    # Accumulator: arm key (rtt, loss, mode, variant) -> list of measured records.
    arms: Dict[Tuple[int, int, str, str], List[Dict[str, Any]]] = {}
    n_ok = 0
    n_err = 0
    n_warmup_ok = 0
    n_warmup_err = 0

    jsonl_f = open(jsonl_path, "w", encoding="utf-8")
    try:
        for rtt in args.rtt_tiers:
            for loss in args.loss_tiers:
                shaped = not (rtt == 0 and loss == 0)
                print(f"\n=== regime rtt={rtt}ms loss={loss}% "
                      f"[{'shaped' if shaped else 'baseline'}] ===")
                netem_applied = apply_netem_link_b(rtt, loss, dry_run=False)

                for mode in args.modes:
                    for variant in variants_for_mode(mode, args.sphp_axis):
                        sphp = sphp_flag_for_variant(variant)
                        arm_key = (rtt, loss, mode, variant)
                        arm = arm_plan_by_key[arm_key]
                        warmup_queries = arm["warmups"]
                        measured_queries = arm["measured"]
                        print(f"  mode={mode:<7} variant={variant:<8} "
                              f"(sphp={sphp})  "
                              f"queries[{arm['pool_start']}:{arm['pool_end']}]")

                        # Warmups: executed but NOT recorded.  This arm's
                        # warmups are disjoint from every other arm's queries,
                        # so they prime the engine without pre-caching this
                        # arm's measured prefixes (repeat 0 stays cold).
                        for w, wq in enumerate(warmup_queries):
                            try:
                                http_post_json(endpoint, {
                                    "query": wq,
                                    "top_k": args.top_k,
                                    "mode": mode,
                                    "sphp": sphp,
                                }, timeout=args.timeout)
                                n_warmup_ok += 1
                            except Exception as e:
                                n_warmup_err += 1
                                print(f"    [warmup] {mode}/{variant} w={w} "
                                      f"failed: {e}", file=sys.stderr)

                        # Measured: N queries, each R times back-to-back.
                        for query in measured_queries:
                            for rep in range(args.repeats):
                                rec: Dict[str, Any] = {
                                    "run_id": run_id,
                                    "timestamp": _now_iso(),
                                    "query_id": None,
                                    "query": query,
                                    "rtt_ms": rtt,
                                    "loss_pct": loss,
                                    "mode": mode,
                                    "variant": variant,
                                    "repeat_index": rep,
                                    "top_k": args.top_k,
                                    "netem_applied": netem_applied,
                                }
                                try:
                                    res = http_post_json(endpoint, {
                                        "query": query,
                                        "top_k": args.top_k,
                                        "mode": mode,
                                        "sphp": sphp,
                                    }, timeout=args.timeout)
                                    rec = build_record(
                                        run_id, rtt, loss, mode, variant,
                                        rep, query, res, netem_applied)
                                    n_ok += 1
                                except Exception as e:
                                    rec["error"] = str(e)
                                    n_err += 1

                                jsonl_f.write(json.dumps(rec) + "\n")
                                jsonl_f.flush()
                                arms.setdefault(arm_key, []).append(rec)

                clear_netem(dry_run=False)
    finally:
        jsonl_f.close()

    # ------------------------------------------------------------------
    # Summary per (tier, mode, variant) arm
    # ------------------------------------------------------------------
    summary_rows: List[Dict[str, Any]] = []
    for (rtt, loss, mode, variant), recs in arms.items():
        row = summarize_arm(recs)
        row = {
            "rtt_ms": rtt,
            "loss_pct": loss,
            "mode": mode,
            "variant": variant,
            **row,
        }
        summary_rows.append(row)
    summary_rows.sort(key=lambda r: (r["rtt_ms"], r["loss_pct"],
                                     r["mode"], r["variant"]))

    # Write CSV.
    if summary_rows:
        fieldnames = list(summary_rows[0].keys())
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(summary_rows)

    # Write JSON summary (always, even if empty).
    summary_doc = {
        "run_id": run_id,
        "generated_at": _now_iso(),
        "gateway_url": args.gateway_url,
        "queries_file": queries_path,
        "n_queries_total": len(all_queries),
        "query_sample": args.query_sample,
        "seed": args.seed,
        "warmups": args.warmups,
        "rtt_tiers": args.rtt_tiers,
        "loss_tiers": args.loss_tiers,
        "modes": args.modes,
        "sphp_axis": args.sphp_axis,
        "repeats": args.repeats,
        "top_k": args.top_k,
        "n_ok": n_ok,
        "n_errors": n_err,
        "n_warmup_ok": n_warmup_ok,
        "n_warmup_errors": n_warmup_err,
        "summary": summary_rows,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary_doc, f, indent=2)

    # ------------------------------------------------------------------
    # Console summary
    # ------------------------------------------------------------------
    print("\n" + "=" * 78)
    print(f"SUMMARY  (measured ok={n_ok} errors={n_err} | "
          f"warmup ok={n_warmup_ok} errors={n_warmup_err})")
    print("=" * 78)
    hdr = (f"{'rtt':>4} {'loss':>4} {'mode':<7} {'variant':<8} "
           f"{'n':>4} {'ttft mean':>10} {'ttft p50':>9} {'ttft p95':>9} "
           f"{'ttft std':>9} {'total mean':>11} {'total p95':>10} "
           f"{'r0 ttft':>8} {'warm ttft':>10} {'sphp hit':>9} {'mean ovl':>9}")
    print(hdr)
    print("-" * len(hdr))
    for r in summary_rows:
        hit = r["sphp_hit_rate"]
        ovl = r["mean_overlap"]
        hit_s = f"{hit:.3f}" if hit is not None else "-"
        ovl_s = f"{ovl:.3f}" if ovl is not None else "-"
        print(f"{r['rtt_ms']:>4} {r['loss_pct']:>4} {r['mode']:<7} "
              f"{r['variant']:<8} {r['n']:>4} "
              f"{r['ttft_mean']:>10.1f} {r['ttft_p50']:>9.1f} {r['ttft_p95']:>9.1f} "
              f"{r['ttft_std']:>9.1f} {r['total_mean']:>11.1f} {r['total_p95']:>10.1f} "
              f"{r['ttft_repeat0_mean']:>8.1f} {r['ttft_warm_mean']:>10.1f} "
              f"{hit_s:>9} {ovl_s:>9}")
    print("-" * len(hdr))
    print(f"\nJSONL   : {jsonl_path}")
    print(f"CSV     : {csv_path}")
    print(f"JSON    : {json_path}")
    print("Done.")


if __name__ == "__main__":
    main()
