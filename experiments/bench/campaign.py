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

Usage (the paper's final matrix — 4 WAN regimes x SPHP on/off, hybrid mode):
    python3 benchmarks/campaign.py \
        --gateway-url http://10.8.0.2:8000 \
        --queries experiments/bench/queries/latency_dev480_seed42.txt \
        --rtt-tiers 0 15 40 80 \
        --loss-tiers 0 \
        --modes hybrid \
        --sphp-axis \
        --query-sample 50 --seed 42 \
        --warmups 5 --repeats 3 \
        --out-dir benchmarks/campaigns

    # Plan only (no netem, no HTTP, no files):
    python3 benchmarks/campaign.py --dry-run
"""

import argparse
import csv
import json
import math
import random
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
PROJECT_ROOT = _HERE.parent
NETEM_DIR = PROJECT_ROOT / "scripts" / "netem"
APPLY_SH = NETEM_DIR / "apply.sh"
CLEAR_SH = NETEM_DIR / "clear.sh"

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
# Small helpers
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


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
def run_netem(script: Path, *args: str) -> Tuple[bool, str]:
    """Run a netem shell script; return (ok, combined_output)."""
    if not script.is_file():
        return False, f"script not found: {script}"
    cmd = ["bash", str(script)] + [str(a) for a in args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        combined = "\n".join(x for x in (out, err) if x)
        return proc.returncode == 0, combined
    except Exception as e:  # timeout, missing bash, etc.
        return False, f"exception: {e}"


def apply_netem(rtt: int, loss: int, dry_run: bool) -> bool:
    """Apply one-way WAN shaping for a (rtt, loss) regime.

    Skipped for dry-run or the unshaped baseline (rtt=0 and loss=0).
    Returns True only if the qdisc was actually applied.
    """
    if dry_run or (rtt == 0 and loss == 0):
        return False
    ok, out = run_netem(APPLY_SH, rtt, loss)
    if ok:
        print(f"    [netem] applied rtt={rtt}ms loss={loss}%")
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
        description="Final-matrix campaign runner for the Node B hybrid RAG gateway.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--gateway-url", default="http://10.8.0.2:8000",
                    help="Node B gateway base URL")
    ap.add_argument("--queries", default="experiments/bench/queries/latency_dev480_seed42.txt",
                    help="Path to a one-query-per-line text file")
    ap.add_argument("--rtt-tiers", nargs="+", type=int,
                    default=[0, 15, 40, 80],
                    help="One-way RTT tiers (ms) to sweep (paper tiers)")
    ap.add_argument("--loss-tiers", nargs="+", type=int, default=[0, 1],
                    help="Packet-loss tiers (%%) to sweep")
    ap.add_argument("--modes", nargs="+", default=["hybrid", "dense", "sparse"],
                    choices=VALID_MODES,
                    help="Retrieval modes to sweep")
    ap.add_argument("--sphp-axis", action="store_true",
                    help="For mode=hybrid, run two variants per tier "
                         "(sphp=false baseline and sphp=true); dense/sparse run once")
    ap.add_argument("--query-sample", type=int, default=50,
                    help="Number of measured queries per arm (N)")
    ap.add_argument("--seed", type=int, default=42,
                    help="RNG seed for the query sample (reproducible)")
    ap.add_argument("--repeats", type=int, default=3,
                    help="Back-to-back repeats per measured query (R); "
                         "repeat 0 is the cold-prefix reference")
    ap.add_argument("--warmups", type=int, default=5,
                    help="Warmup queries per (tier, mode, variant) arm (W); "
                         "executed but not recorded")
    ap.add_argument("--top-k", type=int, default=10,
                    help="top_k sent to the gateway")
    ap.add_argument("--timeout", type=int, default=120,
                    help="Per-request HTTP timeout (seconds)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the plan only: no netem, no HTTP, no output files")
    ap.add_argument("--out-dir", default="experiments/bench/campaigns",
                    help="Directory for JSONL/CSV/JSON outputs")
    return ap


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


def build_arm_list(args: argparse.Namespace) -> List[Tuple[int, int, str, str]]:
    """Return the full ordered arm list: (rtt, loss, mode, variant).

    Order is regime-major (rtt x loss), then mode, then variant — the same
    order the runner executes arms in.  Used both to size the per-arm query
    pool and to drive the dry-run plan so the two always agree.
    """
    arms: List[Tuple[int, int, str, str]] = []
    for rtt in args.rtt_tiers:
        for loss in args.loss_tiers:
            for mode in args.modes:
                for variant in variants_for_mode(mode, args.sphp_axis):
                    arms.append((rtt, loss, mode, variant))
    return arms


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
    print(f"  query sample : N={args.query_sample} measured + W={args.warmups} warmups "
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
) -> Dict[str, Any]:
    """Flatten a /query/benchmark response into a single JSONL record."""
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
    return rec


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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    args = build_parser().parse_args()

    queries_path = resolve_queries_path(args.queries)
    try:
        all_queries = load_queries(queries_path)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    if not all_queries:
        print(f"ERROR: no queries loaded from {queries_path}", file=sys.stderr)
        sys.exit(1)

    # Build the full arm list FIRST, then assign each arm a disjoint slice of a
    # seeded query pool (fail fast if the file cannot supply n_arms*(N+W)).
    arms = build_arm_list(args)
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
                netem_applied = apply_netem(rtt, loss, dry_run=False)

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
