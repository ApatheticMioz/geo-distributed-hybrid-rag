#!/usr/bin/env python3
"""
Factorial campaign runner for the Node B hybrid RAG gateway.

Sweeps the full factorial of:

    topology  x  rtt  x  loss  x  mode  x  repeat  x  query

and, for each network regime ``(rtt, loss)``, applies kernel-level WAN shaping
via ``scripts/netem/apply.sh`` (and clears it with ``clear.sh``) so that every
hop of the gateway -> Node A path is shaped, not just an application-level
header.  The baseline regime ``(rtt=0, loss=0)`` runs unshaped.

For every measured query execution the runner:
  * POSTs to the gateway ``/query/benchmark`` endpoint,
  * appends a full-timing JSONL record to ``<out-dir>/campaign_<run_id>.jsonl``.

At the end it writes:
  * ``<out-dir>/summary_<run_id>.csv``  (p50/p95 TTFT + total latency per cell)
  * ``<out-dir>/summary_<run_id>.json`` (same summary + run metadata)

The runner is intentionally stdlib-only so it can run from the orchestration
host against the live gateway.  ``--dry-run`` prints the plan only: no netem,
no HTTP, no output files.

Usage:
    python3 benchmarks/campaign.py \
        --gateway-url http://10.8.0.2:8000 \
        --queries benchmarks/queries50.txt \
        --topologies P0 P2 \
        --rtt-tiers 0 10 50 100 200 \
        --loss-tiers 0 1 \
        --modes hybrid dense sparse \
        --repeats 3 --warmups 2 \
        --out-dir benchmarks/campaigns

    # Plan only (no netem, no HTTP, no files):
    python3 benchmarks/campaign.py --dry-run
"""

import argparse
import csv
import json
import math
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

VALID_TOPOLOGIES = ("P0", "P2")
VALID_MODES = ("hybrid", "sparse", "dense")


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


def summarize(values: List[float]) -> Dict[str, float]:
    """Return n/mean/p50/p95/min/max for a list of latency samples (ms)."""
    if not values:
        return {"n": 0, "mean": 0.0, "p50": 0.0, "p95": 0.0, "min": 0.0, "max": 0.0}
    n = len(values)
    mean = sum(values) / n
    return {
        "n": n,
        "mean": round(mean, 2),
        "p50": round(percentile(values, 50), 2),
        "p95": round(percentile(values, 95), 2),
        "min": round(min(values), 2),
        "max": round(max(values), 2),
    }


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
        description="Factorial campaign runner for the Node B hybrid RAG gateway.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--gateway-url", default="http://10.8.0.2:8000",
                    help="Node B gateway base URL")
    ap.add_argument("--queries", default="benchmarks/queries50.txt",
                    help="Path to a one-query-per-line text file")
    ap.add_argument("--topologies", nargs="+", default=list(VALID_TOPOLOGIES),
                    choices=VALID_TOPOLOGIES,
                    help="Placement topology presets to sweep")
    ap.add_argument("--rtt-tiers", nargs="+", type=int,
                    default=[0, 10, 50, 100, 200],
                    help="One-way RTT tiers (ms) to sweep")
    ap.add_argument("--loss-tiers", nargs="+", type=int, default=[0, 1],
                    help="Packet-loss tiers (%%) to sweep")
    ap.add_argument("--modes", nargs="+", default=["hybrid", "dense", "sparse"],
                    choices=VALID_MODES,
                    help="Retrieval modes to sweep")
    ap.add_argument("--repeats", type=int, default=3,
                    help="Measured repeats (full passes over the query set) per cell")
    ap.add_argument("--warmups", type=int, default=2,
                    help="Warmup requests per (rtt, loss, mode) before measuring")
    ap.add_argument("--top-k", type=int, default=10,
                    help="top_k sent to the gateway")
    ap.add_argument("--timeout", type=int, default=120,
                    help="Per-request HTTP timeout (seconds)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the plan only: no netem, no HTTP, no output files")
    ap.add_argument("--out-dir", default="benchmarks/campaigns",
                    help="Directory for JSONL/CSV/JSON outputs")
    return ap


# ---------------------------------------------------------------------------
# Plan / dry-run
# ---------------------------------------------------------------------------
def print_plan(args: argparse.ArgumentParser, queries: List[str]) -> None:
    n_regimes = len(args.rtt_tiers) * len(args.loss_tiers)
    n_cells = n_regimes * len(args.topologies) * len(args.modes)
    n_measured = n_cells * args.repeats * len(queries)
    n_warmups = (len(args.rtt_tiers) * len(args.loss_tiers)
                * len(args.modes) * args.warmups)
    n_shaped = sum(1 for r in args.rtt_tiers for l in args.loss_tiers
                   if not (r == 0 and l == 0))

    print("=" * 78)
    print("FACTORIAL CAMPAIGN RUNNER (Node B hybrid RAG gateway) — DRY RUN")
    print("=" * 78)
    print(f"  gateway      : {args.gateway_url}")
    print(f"  queries file : {args.queries} ({len(queries)} queries)")
    print(f"  topologies   : {args.topologies}")
    print(f"  rtt tiers    : {args.rtt_tiers} ms")
    print(f"  loss tiers   : {args.loss_tiers} %")
    print(f"  modes        : {args.modes}")
    print(f"  repeats      : {args.repeats}")
    print(f"  warmups      : {args.warmups} per (rtt, loss, mode)")
    print(f"  top_k        : {args.top_k}")
    print(f"  out dir      : {args.out_dir}")
    print(f"  regimes      : {n_regimes} (rtt x loss)")
    print(f"  cells        : {n_cells} (regime x topology x mode)")
    print(f"  measured req : {n_measured}")
    print(f"  warmup req   : {n_warmups}")
    print(f"  shaped regimes (netem apply/clear): {n_shaped}")
    print("-" * 78)
    print("  Plan (netem would be applied/cleared for shaped regimes only):")
    for rtt in args.rtt_tiers:
        for loss in args.loss_tiers:
            shaped = not (rtt == 0 and loss == 0)
            tag = "shaped" if shaped else "baseline"
            print(f"    regime rtt={rtt:>3}ms loss={loss}%  [{tag}]")
            for mode in args.modes:
                for topo in args.topologies:
                    print(f"        mode={mode:<7} topology={topo}  "
                          f"-> {args.repeats} repeats x {len(queries)} queries")
    print("-" * 78)
    print("  DRY RUN complete. No network calls, no netem, no files were made.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    args = build_parser().parse_args()

    queries_path = resolve_queries_path(args.queries)
    try:
        queries = load_queries(queries_path)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    if not queries:
        print(f"ERROR: no queries loaded from {queries_path}", file=sys.stderr)
        sys.exit(1)

    endpoint = args.gateway_url.rstrip("/") + "/query/benchmark"

    if args.dry_run:
        print_plan(args, queries)
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

    # Accumulator: cell key (topology, rtt, loss, mode) -> list of records.
    cells: Dict[Tuple[str, int, int, str], List[Dict[str, Any]]] = {}
    n_ok = 0
    n_err = 0

    jsonl_f = open(jsonl_path, "w", encoding="utf-8")
    try:
        for rtt in args.rtt_tiers:
            for loss in args.loss_tiers:
                shaped = not (rtt == 0 and loss == 0)
                print(f"\n=== regime rtt={rtt}ms loss={loss}% "
                      f"[{'shaped' if shaped else 'baseline'}] ===")
                netem_applied = apply_netem(rtt, loss, dry_run=False)

                for mode in args.modes:
                    # Warmups (not measured, not written) — prime GPU / caches.
                    for w in range(args.warmups):
                        try:
                            http_post_json(endpoint, {
                                "query": f"warmup query {w}",
                                "top_k": args.top_k,
                                "mode": mode,
                            }, timeout=args.timeout)
                        except Exception as e:
                            print(f"    [warmup] {mode} w={w} failed: {e}",
                                  file=sys.stderr)

                    for topology in args.topologies:
                        for rep in range(args.repeats):
                            for qi, query in enumerate(queries):
                                t0 = time.perf_counter()
                                rec: Dict[str, Any] = {
                                    "run_id": run_id,
                                    "timestamp": _now_iso(),
                                    "topology": topology,
                                    "rtt_ms": rtt,
                                    "loss_pct": loss,
                                    "mode": mode,
                                    "repeat": rep,
                                    "query_index": qi,
                                    "query": query,
                                    "top_k": args.top_k,
                                    "netem_applied": netem_applied,
                                }
                                try:
                                    res = http_post_json(endpoint, {
                                        "query": query,
                                        "top_k": args.top_k,
                                        "mode": mode,
                                    }, timeout=args.timeout)
                                    rec["query_id"] = res.get("query_id")
                                    rec["timings"] = res.get("timings", {})
                                    rec["token_count"] = res.get("token_count")
                                    rec["decode_tps"] = res.get("decode_tps")
                                    rec["client_total_ms"] = round(
                                        (time.perf_counter() - t0) * 1000.0, 2)
                                    n_ok += 1
                                except Exception as e:
                                    rec["error"] = str(e)
                                    n_err += 1

                                jsonl_f.write(json.dumps(rec) + "\n")
                                jsonl_f.flush()
                                cells.setdefault(
                                    (topology, rtt, loss, mode), []).append(rec)

                clear_netem(dry_run=False)
    finally:
        jsonl_f.close()

    # ------------------------------------------------------------------
    # Summary per factorial cell
    # ------------------------------------------------------------------
    summary_rows: List[Dict[str, Any]] = []
    for (topology, rtt, loss, mode), recs in cells.items():
        ok_recs = [r for r in recs if "error" not in r]
        ttft_vals = [r["timings"].get("ttft_ms", 0.0) for r in ok_recs]
        total_vals = [r["timings"].get("total_ms", 0.0) for r in ok_recs]
        ttft = summarize(ttft_vals)
        total = summarize(total_vals)
        summary_rows.append({
            "topology": topology,
            "rtt_ms": rtt,
            "loss_pct": loss,
            "mode": mode,
            "n": ttft["n"],
            "n_errors": len(recs) - len(ok_recs),
            "ttft_p50_ms": ttft["p50"],
            "ttft_p95_ms": ttft["p95"],
            "ttft_mean_ms": ttft["mean"],
            "ttft_min_ms": ttft["min"],
            "ttft_max_ms": ttft["max"],
            "total_p50_ms": total["p50"],
            "total_p95_ms": total["p95"],
            "total_mean_ms": total["mean"],
            "total_min_ms": total["min"],
            "total_max_ms": total["max"],
        })
    summary_rows.sort(key=lambda r: (r["rtt_ms"], r["loss_pct"],
                                     r["mode"], r["topology"]))

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
        "n_queries": len(queries),
        "topologies": args.topologies,
        "rtt_tiers": args.rtt_tiers,
        "loss_tiers": args.loss_tiers,
        "modes": args.modes,
        "repeats": args.repeats,
        "warmups": args.warmups,
        "top_k": args.top_k,
        "n_ok": n_ok,
        "n_errors": n_err,
        "summary": summary_rows,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary_doc, f, indent=2)

    # ------------------------------------------------------------------
    # Console summary
    # ------------------------------------------------------------------
    print("\n" + "=" * 78)
    print(f"SUMMARY  (ok={n_ok} errors={n_err})")
    print("=" * 78)
    hdr = (f"{'rtt':>4} {'loss':>4} {'mode':<7} {'topo':<5} "
           f"{'n':>4} {'ttft p50':>9} {'ttft p95':>9} "
           f"{'total p50':>10} {'total p95':>10}")
    print(hdr)
    print("-" * len(hdr))
    for r in summary_rows:
        print(f"{r['rtt_ms']:>4} {r['loss_pct']:>4} {r['mode']:<7} "
              f"{r['topology']:<5} {r['n']:>4} "
              f"{r['ttft_p50_ms']:>9.1f} {r['ttft_p95_ms']:>9.1f} "
              f"{r['total_p50_ms']:>10.1f} {r['total_p95_ms']:>10.1f}")
    print("-" * len(hdr))
    print(f"\nJSONL   : {jsonl_path}")
    print(f"CSV     : {csv_path}")
    print(f"JSON    : {json_path}")
    print("Done.")


if __name__ == "__main__":
    main()
