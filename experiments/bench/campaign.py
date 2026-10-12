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
and genuine-WAN run types remain WS-CM extension points (see
``V2_RUN_SPECS``).
"""

import argparse
import csv
import hashlib
import json
import math
import random
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
# Note: v1 used _HERE.parent here, which silently pointed NETEM_DIR at a
# non-existent experiments/bench/scripts/netem (run_netem would then just
# report "script not found"); _HERE.parent.parent is the actual repo root.
PROJECT_ROOT = _HERE.parent.parent
NETEM_DIR = PROJECT_ROOT / "scripts" / "netem"
APPLY_SH = NETEM_DIR / "apply.sh"
CLEAR_SH = NETEM_DIR / "clear.sh"
VALIDATE_SH = NETEM_DIR / "validate.sh"

DESIGN_DOC = "docs/campaign_v2_design.md"
DESIGN_COMMIT = "1f911ef"

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
#   "three_node":    lines 1371..1910 (6 arms x (80+10) = 540)   [implemented]
#   "loss-sweep":    {"pool_line_start": 1041},  # 6 arms x (50+5)  = 330  [WS-CM, not yet registered]
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
            f"{sorted(V2_RUN_SPECS)}. (3-node/dropout/genuine-WAN are WS-CM "
            f"extension points, not implemented in this slice.)")
    start = spec["pool_line_start"]
    end = start + n_arms * (n + warmups) - 1
    return start, end


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


def apply_netem_link_b(rtt: int, loss: int, dry_run: bool) -> bool:
    """Apply the 2-node (link-B) shaping regime via the explicit-link
    contract: ``apply.sh --link-b <RTT> <LOSS>`` (no positional args — the
    current apply.sh forbids them).

    Skipped for dry-run or the unshaped baseline (rtt=0 and loss=0).
    Returns True only if the qdisc was actually applied.
    """
    if dry_run or (rtt == 0 and loss == 0):
        return False
    ok, out = run_netem(APPLY_SH, "--link-b", rtt, loss)
    if ok:
        print(f"    [netem] applied --link-b rtt={rtt}ms loss={loss}%")
    else:
        print(f"    [netem] WARNING: apply.sh failed (rtt={rtt} loss={loss}): {out}",
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


def apply_netem_3node(rtt_b: int, loss_b: int, rtt_c: int, loss_c: int,
                      dry_run: bool) -> bool:
    """Apply the 3-node per-leg shaping regime (design §3.2).

    ``apply.sh --link-b <RTT_B> <LOSS_B> --link-c <RTT_C> <LOSS_C>`` shapes
    the A<->B and A<->C legs independently in one call (the explicit-link
    contract; C<->B traffic relays through A).  ``rtt_c=0``/``loss_c=0``
    means the C leg is left on the unshaped default class.  Skipped for
    dry-run.  Returns True only if apply.sh succeeded.
    """
    if dry_run:
        return False
    ok, out = run_netem(APPLY_SH,
                        "--link-b", rtt_b, loss_b,
                        "--link-c", rtt_c, loss_c)
    if ok:
        print(f"    [netem] applied --link-b rtt={rtt_b}ms loss={loss_b}% "
              f"--link-c rtt={rtt_c}ms loss={loss_c}%")
    else:
        print(f"    [netem] WARNING: apply.sh failed "
              f"(--link-b {rtt_b} {loss_b} --link-c {rtt_c} {loss_c}): {out}",
              file=sys.stderr)
    return ok


def run_fidelity_gate_3node(rtt_b: int, rtt_c: int, when: str,
                            dry_run: bool) -> List[Dict[str, Any]]:
    """Per-leg 11-ping fidelity gate for a 3-node cell (design §3.2/§6).

    Validates EACH shaped leg independently — ``validate.sh --link-b
    <RTT_B>`` and ``validate.sh --link-c <RTT_C>`` — so the ±30% added-RTT
    window is enforced per leg, not across a merged ping set.  A 0-RTT leg
    (unshaped) is recorded as ``skipped-baseline``.  ``when`` is "pre" (before
    the cell's arms) or "post" (at cell close).  Returns one record per
    shaped leg, in [B, C] order.
    """
    if dry_run:
        return [{"link": "b", "rtt_ms": rtt_b, "when": when,
                 "status": "planned" if rtt_b > 0 else "skipped-baseline"},
                {"link": "c", "rtt_ms": rtt_c, "when": when,
                 "status": "planned" if rtt_c > 0 else "skipped-baseline"}]
    out: List[Dict[str, Any]] = []
    for link, rtt in (("b", rtt_b), ("c", rtt_c)):
        if rtt <= 0:
            out.append({"link": link, "rtt_ms": rtt, "when": when,
                        "status": "skipped-baseline",
                        "baseline_median_ms": None, "shaped_median_ms": None,
                        "added_rtt_ms": None, "output_tail": ""})
            continue
        ok, gate_out = run_netem(VALIDATE_SH, f"--link-{link}", rtt,
                                 timeout=300)
        parsed = parse_fidelity_gate_output(gate_out)
        out.append({"link": link,
                    "rtt_ms": rtt,
                    "when": when,
                    "status": "PASS" if ok else "FAIL",
                    **parsed,
                    "output_tail": (gate_out or "")[-500:]})
    return out


# ---------------------------------------------------------------------------
# v2: fidelity gate (design §6 — 11-ping median, ±30% added-RTT window)
# ---------------------------------------------------------------------------
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


def run_fidelity_gate(rtt: int, when: str, dry_run: bool) -> Dict[str, Any]:
    """Run the per-tier 11-ping fidelity gate for a 2-node link-B regime.

    ``validate.sh --link-b <RTT>`` clears, measures the baseline median,
    re-applies the shaping, measures the shaped median, enforces the ±30%
    window on the added RTT, and (trap) clears again on exit — so a gate
    call always leaves the link unshaped; the caller re-applies afterwards.
    The unshaped baseline tier (rtt=0) has no gate (validate requires >= 1
    ms): it is recorded as ``skipped-baseline``.

    ``when`` is "pre" (before the tier's arms) or "post" (at tier close).
    """
    if dry_run:
        return {"rtt_ms": rtt, "when": when,
                "status": "planned" if rtt > 0 else "skipped-baseline"}
    if rtt <= 0:
        return {"rtt_ms": 0, "when": when, "status": "skipped-baseline",
                "baseline_median_ms": None, "shaped_median_ms": None,
                "added_rtt_ms": None, "output_tail": ""}
    ok, out = run_netem(VALIDATE_SH, "--link-b", rtt, timeout=300)
    parsed = parse_fidelity_gate_output(out)
    rec: Dict[str, Any] = {
        "rtt_ms": rtt,
        "when": when,
        "status": "PASS" if ok else "FAIL",
        **parsed,
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
                         "core) or 'three-node' (WS-CM 3-node matrix). dropout "
                         "/ genuine-WAN are WS-CM extension points, not "
                         "registered. Default: v1 mode unless --pool is given "
                         "(then 'confirmatory').")
    ap.add_argument("--rtt-tiers", nargs="+", type=int,
                    default=[0, 15, 40, 80],
                    help="Full-round-trip RTT tiers (ms) to sweep (paper tiers)")
    ap.add_argument("--loss-tiers", nargs="+", type=int, default=None,
                    help="Packet-loss tiers (%%) to sweep "
                         "(default: v1 [0, 1]; v2 [0] — the confirmatory core "
                         "is loss-free)")
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
    """Return the ordered 3-node arm list: ``(rtt_b, rtt_c, variant)``.

    Order is cell-major (the design's (B, C) cells in §3.2), then the two
    placement arms (baseline -> sphp).  Both legs are shaped in every 3-node
    cell (loss is 0 on both); there is no unshaped 3-node control (the
    2-node rtt-0 baseline is the control, design §3.2).
    """
    arms: List[Tuple[int, int, str]] = []
    for rtt_b, rtt_c in spec["cells"]:
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
            pre_gates = run_fidelity_gate_3node(rtt_b, rtt_c, "pre",
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
                        rtt_b, rtt_c, "post", dry_run=False)
                    gates_log.extend(post_gates)
                    clear_netem(dry_run=False)
                    abort_reason = (f"≤1% error gate FAILED on arm "
                                    f"{three_node_arm_label(arm_key[3])} at "
                                    f"(B={rtt_b}, C={rtt_c}): {errs}/{total} "
                                    f"errors ({100.0 * errs / total:.1f}%)")
                    exit_code = 1
                    _finalize()

            # --- post-cell per-leg fidelity gate (design §6; clears netem) ---
            post_gates = run_fidelity_gate_3node(rtt_b, rtt_c, "post",
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
