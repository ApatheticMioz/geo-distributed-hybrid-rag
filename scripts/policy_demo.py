#!/usr/bin/env python3
"""
Live regime-level placement-policy demo (paper RQ3).

This script demonstrates the *live* placement policy end-to-end against the
running Node B gateway:

  1. Load BOTH cache-state PlacementCostModels from
     experiments/analysis/results/cost_model_validation.json:
       * WARM  (cache_states.warm.fitted_params)  -- the policy-selection basis
       * COLD  (cache_states.cold.fitted_params)  -- the state-matched comparison
  2. For each fixed WAN regime (RTT in {0, 15, 40, 80} ms, loss 0%), shape the
     gateway -> Node A path with the SAME netem mechanism campaign.py uses
     (scripts/netem/apply.sh RTT LOSS / clear.sh).
  3. Per regime, run --warmups unrecorded warmup queries (priming the engine),
     then --per-regime recorded queries.  The recorded queries are drawn
     round-robin (seeded, seed 42) from the queries file as DISJOINT slices
     per regime, so no query is ever shared across regimes.
  4. The live policy selects the argmin variant per regime from the WARM fit:
          predicted_p2   = warm_model.predict_ttft_p2(rtt, 100.0)
          predicted_sphp = warm_model.predict_ttft_p2_sphp(rtt, 100.0)
          selected       = argmin(predicted_p2, predicted_sphp)
     and the gateway is asked to run that variant (sphp=True for P2-SPHP,
     sphp=False for P2).
  5. Each recorded query is served R times back-to-back (--repeats, default 3),
     mirroring the campaign's cache-state semantics: repeat_index 0 is the
     COLD KV-prefix reference (first time the query is served) and repeats
     >=1 are WARM (the same query's KV prefix is now cached).  Every request
     is recorded with its repeat_index.
  6. The summary compares realized TTFT against the state-matched prediction:
     warm realized (repeats >=1) vs the WARM prediction of the selected
     variant, and cold realized (repeat 0) vs the COLD prediction of the same
     selected variant.  This is what fixes the naive one-shot demo, where
     every query was a cold prefix but was compared against the WARM fit
     (~2900ms realized vs ~760ms predicted, ~280% abs error).

The gateway request pattern (headers, payload incl. the per-request
think-suppression extra_body) and the netem apply/clear invocation are copied
from experiments/bench/campaign.py so this demo exercises the exact same live path.

Outputs:
  experiments/analysis/results/live_policy_demo.jsonl        (one record per request)
  experiments/analysis/results/live_policy_demo_summary.json (per-regime, per-state summary)

Stdout: a compact per-regime table with one row per cache state (warm and
cold): predicted vs realized TTFT, selected variant, abs error %.

The script is stdlib-only (plus the local experiments.analysis.cost_model module) so it
can run from the orchestration host against the live gateway.  It FAILS FAST
on HTTP errors (the HTTP status code is always surfaced, never masked) and
ALWAYS clears netem in a finally block, even on failure.

Usage:
    python3 scripts/policy_demo.py
    python3 scripts/policy_demo.py --gateway http://10.8.0.2:8000 \
        --queries benchmarks/queries/latency_dev480_seed42.txt \
        --per-regime 5 --warmups 2 --repeats 3
"""

import argparse
import json
import math
import random
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent          # scripts/
PROJECT_ROOT = _HERE.parent                      # project root
sys.path.insert(0, str(PROJECT_ROOT))            # so `analysis.cost_model` imports
from experiments.analysis.cost_model import PlacementCostModel  # noqa: E402

NETEM_DIR = PROJECT_ROOT / "scripts" / "netem"
APPLY_SH = NETEM_DIR / "apply.sh"
CLEAR_SH = NETEM_DIR / "clear.sh"
VALIDATION_JSON = PROJECT_ROOT / "experiments" / "analysis" / "results" / "cost_model_validation.json"
RESULTS_DIR = PROJECT_ROOT / "experiments" / "analysis" / "results"

# Fixed WAN regimes for the RQ3 demo (one-way RTT tiers, no loss).
REGIMES_MS = (0, 15, 40, 80)
LOSS_PCT = 0
SEED = 42
BW_MBPS = 100.0          # WireGuard link bandwidth used by the cost model
DEFAULT_TOP_K = 10       # the fitted model was calibrated at top_k=10
DEFAULT_REPEATS = 3      # back-to-back repeats per recorded query (R)

# Per-request think-suppression (Qwen3 emits reasoning tokens by default;
# suppress so TTFT measures the answer stream, not deliberation tokens).
# Copied from the Node A LLM backend (systems/node_a/implementation/src/main.py).
# The gateway's QueryRequest ignores unknown fields (Pydantic extra=ignore),
# so carrying it in the payload is harmless and documents the per-request
# intent.
THINK_SUPPRESSION_EXTRA_BODY = {"chat_template_kwargs": {"enable_thinking": False}}


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
# netem (copied from experiments/bench/campaign.py)
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


def apply_netem(rtt: int, loss: int) -> bool:
    """Apply one-way WAN shaping for a (rtt, loss) regime.

    Skipped for the unshaped baseline (rtt=0 and loss=0).  Returns True only
    if the qdisc was actually applied.
    """
    if rtt == 0 and loss == 0:
        return False
    ok, out = run_netem(APPLY_SH, rtt, loss)
    if ok:
        print(f"    [netem] applied rtt={rtt}ms loss={loss}%")
    else:
        print(f"    [netem] WARNING: apply.sh failed (rtt={rtt} loss={loss}): {out}",
              file=sys.stderr)
    return ok


def clear_netem() -> None:
    """Remove any active shaping qdisc (idempotent; safe to call repeatedly)."""
    ok, out = run_netem(CLEAR_SH)
    if ok:
        print("    [netem] cleared")
    else:
        print(f"    [netem] WARNING: clear.sh failed: {out}", file=sys.stderr)


# ---------------------------------------------------------------------------
# HTTP (copied from experiments/bench/campaign.py; fail-fast, never mask status codes)
# ---------------------------------------------------------------------------
def http_post_json(url: str, payload: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    """POST JSON to ``url`` and return the parsed JSON response.

    Fail-fast: any non-2xx response raises a RuntimeError that carries the
    HTTP status code (never masked), so a broken gateway aborts the demo
    instead of silently recording garbage.
    """
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # Surface the status code + reason + body; never mask it.
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        raise RuntimeError(
            f"HTTP {e.code} {e.reason} from {url}: {body[:500]}"
        ) from e


def check_health(gateway: str, timeout: int = 15) -> bool:
    url = gateway.rstrip("/") + "/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Cost model (per cache state)
# ---------------------------------------------------------------------------
def load_model(validation_path: Path, state: str) -> PlacementCostModel:
    """Instantiate a PlacementCostModel from one cache state's fitted params.

    ``state`` is ``"warm"`` (steady-state cache; the policy-selection basis)
    or ``"cold"`` (cold KV-prefix reference; the state-matched comparison).
    The fitted params live at ``cache_states.<state>.fitted_params``; note the
    key ``sphp_hit_rate_measured`` maps to the constructor's ``sphp_hit_rate``.
    The remaining constructor args (t_hydrate_per_doc_ms, doc_id_bytes,
    doc_text_bytes, query_bytes) keep their defaults, exactly as
    fit_placement_model() does.
    """
    with open(validation_path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    try:
        fp = doc["cache_states"][state]["fitted_params"]
    except (KeyError, TypeError) as e:
        raise ValueError(
            f"cost-model validation JSON missing cache_states.{state}.fitted_params: {e}"
        ) from e
    return PlacementCostModel(
        t_sparse_base_ms=fp["t_sparse_base_ms"],
        t_dense_base_ms=fp["t_dense_base_ms"],
        t_fusion_base_ms=fp["t_fusion_base_ms"],
        t_prefill_base_ms=fp["t_prefill_base_ms"],
        decode_tok_per_sec=fp["decode_tok_per_sec"],
        sphp_hit_rate=fp["sphp_hit_rate_measured"],
        t_sphp_miss_penalty_ms=fp["t_sphp_miss_penalty_ms"],
    )


# ---------------------------------------------------------------------------
# Query plan (round-robin, seeded, disjoint slices per regime)
# ---------------------------------------------------------------------------
def build_regime_query_plan(
    all_queries: List[str],
    regimes: List[int],
    warmups: int,
    per_regime: int,
    seed: int,
) -> List[Dict[str, Any]]:
    """Assign each regime a DISJOINT, round-robin slice of a seeded query pool.

    The full query list is shuffled with ``random.Random(seed)`` (reproducible
    across reruns).  The shuffled pool is then DEALT ROUND-ROBIN across the
    regimes: regime ``i`` receives ``pool[i], pool[i+n], pool[i+2n], ...``
    (residue class ``i`` mod ``n``).  Because the residue classes partition the
    pool, every regime gets a disjoint slice and no query is ever shared across
    regimes.  The first ``warmups`` of each slice are that regime's warmups
    (executed but NOT recorded); the remaining ``per_regime`` are the recorded
    sample (each served ``--repeats`` times back-to-back).

    Fails fast (raises ``ValueError``) if the file holds fewer than
    ``len(regimes) * (warmups + per_regime)`` distinct queries.
    """
    n_regimes = len(regimes)
    per_regime_total = warmups + per_regime
    required = n_regimes * per_regime_total
    available = len(all_queries)
    if available < required:
        raise ValueError(
            f"not enough distinct queries for per-regime round-robin sampling: "
            f"required {required} (n_regimes={n_regimes} x (W={warmups} + "
            f"N={per_regime})), but the queries file has only {available}. "
            f"Add at least {required - available} more distinct queries, or "
            f"lower --per-regime/--warmups."
        )

    rng = random.Random(seed)
    pool = list(all_queries)
    rng.shuffle(pool)

    plan: List[Dict[str, Any]] = []
    for i, rtt in enumerate(regimes):
        slice_ = pool[i::n_regimes][:per_regime_total]
        plan.append({
            "rtt_ms": rtt,
            "warmups": slice_[:warmups],
            "measured": slice_[warmups:per_regime_total],
        })
    return plan


# ---------------------------------------------------------------------------
# Record building
# ---------------------------------------------------------------------------
def build_record(
    run_id: str,
    rtt: int,
    query: str,
    res: Dict[str, Any],
    predicted_p2: float,
    predicted_sphp: float,
    selected_variant: str,
    netem_applied: bool,
    top_k: int,
    repeat_index: int,
) -> Dict[str, Any]:
    """Flatten a /query/benchmark response into one JSONL record.

    ``repeat_index`` is the back-to-back repeat index for this query
    (0 = cold KV-prefix reference, >=1 = warm).
    """
    timings = res.get("timings", {}) or {}
    return {
        "run_id": run_id,
        "timestamp": _now_iso(),
        "rtt_ms": rtt,
        "loss_pct": LOSS_PCT,
        "query_id": res.get("query_id"),
        "query": query,
        "top_k": top_k,
        "netem_applied": netem_applied,
        "repeat_index": repeat_index,
        "predicted_p2_ms": round(predicted_p2, 2),
        "predicted_sphp_ms": round(predicted_sphp, 2),
        "selected_variant": selected_variant,
        "ttft_ms": timings.get("ttft_ms"),
        "total_ms": timings.get("total_ms"),
    }


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
def summarize_regime(
    rtt: int,
    warm_model: PlacementCostModel,
    cold_model: PlacementCostModel,
    selected_variant: str,
    recs: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Per-regime, per-cache-state summary: realized TTFT vs state-matched pred.

    Realized TTFT is split by cache state (repeat_index 0 = cold, >=1 = warm).
    The WARM block compares warm realized TTFT against the WARM model's
    prediction of the selected variant; the COLD block compares cold realized
    TTFT against the COLD model's prediction of the same selected variant.
    Variant selection itself is always by the WARM fit (``policy_state``).
    """
    cold_recs = [r for r in recs if r.get("repeat_index", 0) == 0]
    warm_recs = [r for r in recs if r.get("repeat_index", 0) >= 1]

    def _state_block(state_recs: List[Dict[str, Any]],
                     model: PlacementCostModel) -> Dict[str, Any]:
        ttft_vals = [r["ttft_ms"] for r in state_recs
                     if r.get("ttft_ms") is not None]
        ttft_mean, _ = mean_std(ttft_vals)
        ttft_p50 = percentile(ttft_vals, 50)
        pred = (
            model.predict_ttft_p2(rtt, BW_MBPS)
            if selected_variant == "P2"
            else model.predict_ttft_p2_sphp(rtt, BW_MBPS)
        )
        err_pct = (
            abs(ttft_mean - pred) / max(pred, 1.0) * 100.0 if pred else 0.0
        )
        return {
            "realized_ttft_mean_ms": round(ttft_mean, 2),
            "realized_ttft_p50_ms": round(ttft_p50, 2),
            "prediction_ms": round(pred, 2),
            "error_pct": round(err_pct, 2),
            "n": len(state_recs),
        }

    return {
        "rtt_ms": rtt,
        "loss_pct": LOSS_PCT,
        "policy_state": "warm",
        "selected_variant": selected_variant,
        "warm": _state_block(warm_recs, warm_model),
        "cold": _state_block(cold_recs, cold_model),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Live regime-level placement-policy demo (paper RQ3).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--gateway", default="http://10.8.0.2:8000",
                    help="Node B gateway base URL")
    ap.add_argument("--queries",
                    default="benchmarks/queries/latency_dev480_seed42.txt",
                    help="Path to a one-query-per-line text file")
    ap.add_argument("--per-regime", type=int, default=5,
                    help="Number of RECORDED queries per regime (N)")
    ap.add_argument("--warmups", type=int, default=2,
                    help="Warmup queries per regime (W); executed but not recorded")
    ap.add_argument("--repeats", type=int, default=DEFAULT_REPEATS,
                    help="Back-to-back repeats per recorded query (R); "
                         "repeat 0 is the cold-prefix reference, >=1 are warm")
    ap.add_argument("--top-k", type=int, default=DEFAULT_TOP_K,
                    help="top_k sent to the gateway (fitted model is calibrated at 10)")
    ap.add_argument("--timeout", type=int, default=120,
                    help="Per-request HTTP timeout (seconds)")
    ap.add_argument("--seed", type=int, default=SEED,
                    help="RNG seed for the round-robin query deal (reproducible)")
    return ap


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    args = build_parser().parse_args()

    # --- Load queries (fail fast) ---
    queries_path = resolve_queries_path(args.queries)
    try:
        all_queries = load_queries(queries_path)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    if not all_queries:
        print(f"ERROR: no queries loaded from {queries_path}", file=sys.stderr)
        sys.exit(1)

    # --- Load BOTH cache-state cost models (fail fast) ---
    if not VALIDATION_JSON.is_file():
        print(f"ERROR: cost-model validation JSON not found: {VALIDATION_JSON}",
              file=sys.stderr)
        sys.exit(1)
    try:
        warm_model = load_model(VALIDATION_JSON, "warm")
        cold_model = load_model(VALIDATION_JSON, "cold")
    except (ValueError, KeyError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    # --- Build the per-regime round-robin query plan (fail fast if too few) ---
    regimes = list(REGIMES_MS)
    try:
        plan = build_regime_query_plan(
            all_queries, regimes, args.warmups, args.per_regime, args.seed)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    plan_by_rtt = {p["rtt_ms"]: p for p in plan}

    # --- Health check (fail fast if the gateway is down) ---
    if not check_health(args.gateway, timeout=15):
        print(f"ERROR: gateway health check failed at {args.gateway}/health",
              file=sys.stderr)
        sys.exit(1)
    print(f"Gateway healthy: {args.gateway}")

    # --- Ensure a clean slate before any shaping ---
    clear_netem()

    run_id = _run_id()
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    jsonl_path = RESULTS_DIR / "live_policy_demo.jsonl"
    summary_path = RESULTS_DIR / "live_policy_demo_summary.json"
    endpoint = args.gateway.rstrip("/") + "/query/benchmark"

    # Per-regime accumulators: rtt -> list of recorded records (all repeats).
    regime_recs: Dict[int, List[Dict[str, Any]]] = {r: [] for r in regimes}
    n_ok = 0
    n_warmup_ok = 0

    jsonl_f = open(jsonl_path, "w", encoding="utf-8")
    try:
        for rtt in regimes:
            shaped = not (rtt == 0 and LOSS_PCT == 0)
            tag = "shaped" if shaped else "baseline"
            print(f"\n=== regime rtt={rtt}ms loss={LOSS_PCT}% [{tag}] ===")
            netem_applied = apply_netem(rtt, LOSS_PCT)

            # Predictions + live policy selection (argmin variant, WARM fit).
            predicted_p2 = warm_model.predict_ttft_p2(rtt, BW_MBPS)
            predicted_sphp = warm_model.predict_ttft_p2_sphp(rtt, BW_MBPS)
            selected_variant = "P2" if predicted_p2 <= predicted_sphp else "P2-SPHP"
            sphp = selected_variant == "P2-SPHP"
            print(f"  [warm fit] P2={predicted_p2:.1f}ms  "
                  f"P2-SPHP={predicted_sphp:.1f}ms  -> selected {selected_variant} "
                  f"(sphp={sphp})")

            arm = plan_by_rtt[rtt]

            # Warmups: executed but NOT recorded (prime the engine into the
            # warm cache state the model was fit on).
            for w, wq in enumerate(arm["warmups"]):
                http_post_json(endpoint, {
                    "query": wq,
                    "top_k": args.top_k,
                    "mode": "hybrid",
                    "sphp": sphp,
                    "extra_body": THINK_SUPPRESSION_EXTRA_BODY,
                }, timeout=args.timeout)
                n_warmup_ok += 1

            # Recorded: N queries, each served R times back-to-back.  Repeat 0
            # is the cold KV-prefix reference; repeats >=1 are warm.
            for q in arm["measured"]:
                for rep in range(args.repeats):
                    res = http_post_json(endpoint, {
                        "query": q,
                        "top_k": args.top_k,
                        "mode": "hybrid",
                        "sphp": sphp,
                        "extra_body": THINK_SUPPRESSION_EXTRA_BODY,
                    }, timeout=args.timeout)
                    rec = build_record(run_id, rtt, q, res,
                                       predicted_p2, predicted_sphp,
                                       selected_variant, netem_applied,
                                       args.top_k, rep)
                    jsonl_f.write(json.dumps(rec) + "\n")
                    jsonl_f.flush()
                    regime_recs[rtt].append(rec)
                    n_ok += 1
                    print(f"    [rep {rep}] ttft={rec['ttft_ms']:7.1f}ms "
                          f"total={rec['total_ms']:7.1f}ms  '{q[:32]}'")

            # Per-regime clear (matches campaign.py); the finally below is the
            # guaranteed clear even on failure.
            clear_netem()
    finally:
        jsonl_f.close()
        # ALWAYS clear netem, even on failure, so the shaped path is never left
        # active behind a crashed run.
        clear_netem()

    # ------------------------------------------------------------------
    # Summary per regime (per cache state)
    # ------------------------------------------------------------------
    summary_rows: List[Dict[str, Any]] = []
    for rtt in regimes:
        predicted_p2 = warm_model.predict_ttft_p2(rtt, BW_MBPS)
        predicted_sphp = warm_model.predict_ttft_p2_sphp(rtt, BW_MBPS)
        selected_variant = "P2" if predicted_p2 <= predicted_sphp else "P2-SPHP"
        summary_rows.append(summarize_regime(
            rtt, warm_model, cold_model, selected_variant, regime_recs[rtt]))

    summary_doc = {
        "run_id": run_id,
        "generated_at": _now_iso(),
        "gateway": args.gateway,
        "queries_file": queries_path,
        "n_queries_total": len(all_queries),
        "per_regime": args.per_regime,
        "warmups": args.warmups,
        "repeats": args.repeats,
        "seed": args.seed,
        "top_k": args.top_k,
        "bw_mbps": BW_MBPS,
        "regimes_ms": regimes,
        "loss_pct": LOSS_PCT,
        "policy_state": "warm",
        "model": "warm+cold PlacementCostModel "
                 "(experiments/analysis/results/cost_model_validation.json)",
        "n_ok": n_ok,
        "n_warmup_ok": n_warmup_ok,
        "summary": summary_rows,
    }
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary_doc, f, indent=2)
        f.write("\n")

    # ------------------------------------------------------------------
    # Console summary (compact per-regime table, one row per cache state)
    # ------------------------------------------------------------------
    print("\n" + "=" * 78)
    print(f"LIVE PLACEMENT-POLICY DEMO (RQ3)  (measured ok={n_ok} | "
          f"warmup ok={n_warmup_ok})")
    print("=" * 78)
    hdr = (f"{'rtt':>4} {'state':>5} {'selected':>9} {'pred':>9} "
           f"{'ttft mean':>10} {'ttft p50':>9} {'abs err%':>9} {'n':>3}")
    print(hdr)
    print("-" * len(hdr))
    for r in summary_rows:
        for state in ("warm", "cold"):
            b = r[state]
            print(f"{r['rtt_ms']:>4} {state:>5} {r['selected_variant']:>9} "
                  f"{b['prediction_ms']:>9.1f} {b['realized_ttft_mean_ms']:>10.1f} "
                  f"{b['realized_ttft_p50_ms']:>9.1f} {b['error_pct']:>8.1f}% "
                  f"{b['n']:>3}")
    print("-" * len(hdr))
    print(f"\nJSONL   : {jsonl_path}")
    print(f"SUMMARY : {summary_path}")
    print("Done.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # Fail fast: surface the error (HTTP status codes are already embedded
        # in the exception message by http_post_json) and exit non-zero.  The
        # finally block in main() has already cleared netem.
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
