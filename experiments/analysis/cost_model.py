#!/usr/bin/env python3
"""
Analytical Cost Model & Placement Policy for Geo-Distributed Hybrid RAG.

Stage model:
  T_sparse : BM25 (Tantivy) retrieval leg
  T_dense  : BGE-M3 embedding + Qdrant ANN leg
  T_fusion : Reciprocal Rank Fusion (RRF) overhead
  T_wan    : WireGuard leg transit (RTT/2) + payload serialization
  T_prefill: LLM time-to-first-token residual (hydration + engine prefill + queueing)
  T_decode : autoregressive token decoding

Evaluates placement topologies:
  P0       Colocated (scenario analysis only - not deployed)
  P2       Boundary before generation (deployed baseline: retrieval on Node B,
           generation on Node A)
  P2-SPHP  Speculative Progressive Hydration & Prefill (deployed variant)
  P3       Boundary between index and corpus text (scenario analysis only)

Fitting (campaign-driven):
  Every parameter is fitted from a latency-campaign JSONL written by
  experiments/bench/campaign.py (experiments/bench/campaigns/campaign_*.jsonl). Fitting is
  performed SEPARATELY per cache state
      cold = repeat_index 0  (first measured query of an arm slice; cold KV prefix)
      warm = repeat_index >= 1
  because the retrieval components differ by an order of magnitude between
  the two states. The SPHP hit rate is MEASURED from the campaign (never
  assumed), and the SPHP miss penalty is the measured mean wasted prefill.

Validation protocol:
  Macro (population-level) predictions are validated leave-one-tier-out at
  the REGIME level: fit on three RTT tiers, predict the held-out tier's
  mean TTFT, compare against the observed tier mean. Query-level variance
  is dominated by prompt content rather than placement, so per-query R^2 is
  not a meaningful target (the pre-refit fitter reported R^2 = -0.03 for
  exactly this reason; its 23.73%/29.48% MAPE figures are obsolete - see
  legacy_reconciliation in the validation output).
  The component-level residual check (predicted vs observed given measured
  components) is reported as a form-error diagnostic.
"""

import csv
import json
import math
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
PROJECT_ROOT = _HERE.parent.parent          # repo root (script lives in experiments/analysis)
RESULTS_DIR = _HERE / "results"
CAMPAIGN_GLOB = PROJECT_ROOT / "experiments" / "bench" / "campaigns" / "campaign_*.jsonl"


def _mean(xs: List[float]) -> float:
    return sum(xs) / len(xs)


def _median(xs: List[float]) -> float:
    s = sorted(xs)
    n = len(s)
    if n == 0:
        raise ValueError("median of empty sequence")
    mid = n // 2
    return s[mid] if n % 2 else 0.5 * (s[mid - 1] + s[mid])


def _wilson_ci(p: float, n: int, z: float = 1.96) -> Tuple[float, float]:
    """Wilson score interval for a binomial proportion."""
    if n == 0:
        return (0.0, 0.0)
    denom = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denom
    half = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def _ols_slope(xs: List[float], ys: List[float]) -> float:
    n = len(xs)
    mx, my = _mean(xs), _mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / sxx if sxx > 0 else 0.0


def _mape(pairs: List[Tuple[float, float]]) -> float:
    """Mean absolute percentage error over (predicted, observed) pairs."""
    if not pairs:
        return float("nan")
    return 100.0 * _mean([abs(p - a) / max(abs(a), 1.0) for p, a in pairs])


def _r2(pairs: List[Tuple[float, float]]) -> float:
    if not pairs:
        return float("nan")
    actuals = [a for _, a in pairs]
    mean_a = _mean(actuals)
    ss_tot = sum((a - mean_a) ** 2 for a in actuals)
    ss_res = sum((a - p) ** 2 for p, a in pairs)
    return 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")


class PlacementCostModel:
    """Analytical stage-timing and placement policy engine.

    The constructor defaults are illustrative placeholders only; production
    use must instantiate via fit_placement_model() from campaign data.
    """

    def __init__(
        self,
        t_sparse_base_ms: float = 145.0,
        t_dense_base_ms: float = 340.0,
        t_fusion_base_ms: float = 0.025,
        t_hydrate_per_doc_ms: float = 0.28,
        t_prefill_base_ms: float = 420.0,
        decode_tok_per_sec: float = 75.0,
        doc_id_bytes: int = 8,
        doc_text_bytes: int = 1200,
        query_bytes: int = 64,
        sphp_hit_rate: float = 0.60,
        t_sphp_miss_penalty_ms: float = 5.0,
    ):
        self.t_sparse_base_ms = t_sparse_base_ms
        self.t_dense_base_ms = t_dense_base_ms
        self.t_fusion_base_ms = t_fusion_base_ms
        self.t_hydrate_per_doc_ms = t_hydrate_per_doc_ms
        self.t_prefill_base_ms = t_prefill_base_ms
        self.decode_tok_per_sec = decode_tok_per_sec
        self.doc_id_bytes = doc_id_bytes
        self.doc_text_bytes = doc_text_bytes
        self.query_bytes = query_bytes
        self.sphp_hit_rate = sphp_hit_rate
        self.t_sphp_miss_penalty_ms = t_sphp_miss_penalty_ms

    def wan_delay_ms(self, payload_bytes: int, rtt_ms: float, bw_mbps: float) -> float:
        """One-way WAN transfer latency = RTT/2 + serialization delay."""
        if bw_mbps <= 0:
            bw_mbps = 100.0  # default 100 Mbps WireGuard link
        transit_ms = 0.5 * rtt_ms
        # bits / (Mbps * 10^6) * 1000 ms
        serial_ms = (payload_bytes * 8.0) / (bw_mbps * 1e6) * 1000.0
        return transit_ms + serial_ms

    def predict_ttft_p0(self, top_k: int = 10) -> float:
        """P0 (Colocated): All stages local on generation host."""
        retrieval_ms = max(self.t_sparse_base_ms, self.t_dense_base_ms)
        hydrate_ms = self.t_hydrate_per_doc_ms * min(top_k, 5)
        return retrieval_ms + self.t_fusion_base_ms + hydrate_ms + self.t_prefill_base_ms

    def predict_ttft_p2(
        self, rtt_ms: float, bw_mbps: float = 100.0, top_k: int = 10
    ) -> float:
        """
        P2 (Boundary Before Generation):
        Node B performs Sparse + Dense + RRF. Sends top_k doc_ids over WAN
        to Node A. Node A hydrates locally and performs LLM prefill.
        """
        retrieval_ms = max(self.t_sparse_base_ms, self.t_dense_base_ms)
        payload_bytes = self.query_bytes + (top_k * self.doc_id_bytes)
        wan_ms = self.wan_delay_ms(payload_bytes, rtt_ms, bw_mbps)
        hydrate_ms = self.t_hydrate_per_doc_ms * min(top_k, 5)
        return retrieval_ms + self.t_fusion_base_ms + wan_ms + hydrate_ms + self.t_prefill_base_ms

    def predict_ttft_p2_sphp(
        self, rtt_ms: float, bw_mbps: float = 100.0, top_k: int = 10
    ) -> float:
        """
        P2 with SPHP (Speculative Progressive Hydration & Prefill):
        1. At t = t_sparse, a SparseHint (top-5 sparse doc_ids) crosses the WAN.
        2. Node A begins provisional hydration + prefill at
             t_prefill_start = t_sparse + wan_hint + hydrate.
        3. Node B's dense leg completes at t_dense + t_fusion.
        4. FusedContext arrives at Node A at
             t_fused_arrival = t_dense + t_fusion + wan_fused.
        5. On hit (measured probability sphp_hit_rate) the prefill performed
           during the barrier is saved; on miss the wasted provisional prefill
           (measured mean t_sphp_miss_penalty_ms) is paid on top of P2.
        """
        hint_payload = self.query_bytes + (5 * self.doc_id_bytes)
        fused_payload = self.query_bytes + (top_k * self.doc_id_bytes)
        wan_hint = self.wan_delay_ms(hint_payload, rtt_ms, bw_mbps)
        wan_fused = self.wan_delay_ms(fused_payload, rtt_ms, bw_mbps)
        hydrate_ms = self.t_hydrate_per_doc_ms * min(top_k, 5)

        # Timeline coordinates
        t_prefill_start = self.t_sparse_base_ms + wan_hint + hydrate_ms
        t_prefill_finish = t_prefill_start + self.t_prefill_base_ms
        t_fused_arrival = self.t_dense_base_ms + self.t_fusion_base_ms + wan_fused

        # Hit case: TTFT is max of fused-context reconciliation vs prefill finish
        ttft_hit = max(t_fused_arrival, t_prefill_finish)

        # Miss case: SPHP falls back to standard P2 plus the measured
        # wasted-provisional-prefill penalty.
        ttft_miss = self.predict_ttft_p2(rtt_ms, bw_mbps, top_k) + self.t_sphp_miss_penalty_ms

        return (self.sphp_hit_rate * ttft_hit) + ((1.0 - self.sphp_hit_rate) * ttft_miss)

    def predict_ttft_p3(
        self, rtt_ms: float, bw_mbps: float = 100.0, top_k: int = 10
    ) -> float:
        """
        P3 (Boundary Between Index and Corpus Text):
        Node B performs Retrieval AND Hydration locally. Full text of the
        top-5 passages is transmitted across the WAN to Node A.
        """
        retrieval_ms = max(self.t_sparse_base_ms, self.t_dense_base_ms)
        hydrate_ms = self.t_hydrate_per_doc_ms * min(top_k, 5)
        payload_bytes = self.query_bytes + (min(top_k, 5) * self.doc_text_bytes)
        wan_ms = self.wan_delay_ms(payload_bytes, rtt_ms, bw_mbps)
        return retrieval_ms + self.t_fusion_base_ms + hydrate_ms + wan_ms + self.t_prefill_base_ms

    def predict_total_e2e(
        self,
        topology: str,
        rtt_ms: float,
        bw_mbps: float = 100.0,
        token_count: int = 250,
        top_k: int = 10,
    ) -> float:
        """Total query turnaround latency = TTFT + token decode + stream return."""
        if topology == "P0":
            ttft = self.predict_ttft_p0(top_k)
        elif topology == "P2":
            ttft = self.predict_ttft_p2(rtt_ms, bw_mbps, top_k)
        elif topology == "P2-SPHP":
            ttft = self.predict_ttft_p2_sphp(rtt_ms, bw_mbps, top_k)
        elif topology == "P3":
            ttft = self.predict_ttft_p3(rtt_ms, bw_mbps, top_k)
        else:
            raise ValueError(f"Unknown topology {topology}")

        decode_time_ms = (token_count / self.decode_tok_per_sec) * 1000.0
        token_stream_ms = self.wan_delay_ms(token_count * 4, rtt_ms, bw_mbps)
        return ttft + decode_time_ms + token_stream_ms


# ---------------------------------------------------------------------------
# Campaign-driven fitting
# ---------------------------------------------------------------------------

# decode_tps outside this band is a degenerate timer artifact (e.g. a
# near-zero decode_ms on a cold-start record), not a real decode rate.
TPS_BAND = (1.0, 500.0)


def load_campaign_records(path: Path) -> List[dict]:
    records: List[dict] = []
    n_bad = 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("error") or not isinstance(r.get("ttft_ms"), (int, float)):
                n_bad += 1
                continue
            records.append(r)
    if not records:
        raise ValueError(f"no usable records in {path}")
    print(f"Loaded {len(records)} records from {path} ({n_bad} error/empty records skipped)")
    return records


def _cache_state(r: dict) -> str:
    return "cold" if r.get("repeat_index", 0) == 0 else "warm"


def _tps_of(recs: List[dict]) -> Tuple[float, int, int]:
    """Median decode rate plus (used, excluded-degenerate) counts."""
    tps = [r["decode_tps"] for r in recs if isinstance(r.get("decode_tps"), (int, float))]
    ok = [t for t in tps if TPS_BAND[0] <= t <= TPS_BAND[1]]
    return _median(ok), len(ok), len(tps) - len(ok)


def _fit_p2_params(recs: List[dict]) -> Dict[str, float]:
    """Baseline-record component means + prefill residual for one state."""
    sparse = [r["sparse_ms"] for r in recs]
    dense = [r["dense_ms"] for r in recs]
    fusion = [r["fusion_ms"] for r in recs]
    residual = [
        r["ttft_ms"] - max(r["sparse_ms"], r["dense_ms"]) - r["fusion_ms"] for r in recs
    ]
    return {
        "t_sparse_base_ms": _mean(sparse),
        "t_dense_base_ms": _mean(dense),
        "t_fusion_base_ms": _mean(fusion),
        # Residual interpretation: hydration + engine prefill + queueing.
        "t_prefill_base_ms": _mean(residual),
    }


def _sphp_stats(sphp_recs: List[dict]) -> Dict[str, object]:
    hits = [1.0 if r.get("sphp_hit") else 0.0 for r in sphp_recs]
    n = len(hits)
    p = _mean(hits) if n else 0.0
    lo, hi = _wilson_ci(p, n)
    misses = [r for r in sphp_recs if r.get("sphp_hit") is False]
    wasted = [r["wasted_prefill_ms"] for r in misses if isinstance(r.get("wasted_prefill_ms"), (int, float))]
    return {
        "n": n,
        "measured_hit_rate": round(p, 4),
        "hit_rate_wilson95": [round(lo, 4), round(hi, 4)],
        "per_tier_hit_rate": {
            str(rtt): round(_mean([1.0 if r.get("sphp_hit") else 0.0 for r in sphp_recs if r["rtt_ms"] == rtt]), 4)
            for rtt in sorted({r["rtt_ms"] for r in sphp_recs})
        },
        "miss_penalty_ms": round(_mean(wasted), 1) if wasted else 5.0,
        "n_miss_used_for_penalty": len(wasted),
    }


def fit_placement_model(
    p2_recs: List[dict],
    sphp_recs: List[dict],
    tps_source: Optional[List[dict]] = None,
) -> PlacementCostModel:
    """Fit a PlacementCostModel from one cache state's records.

    p2_recs:   baseline-variant records of this state (component means).
    sphp_recs: sphp-variant records of this state (measured hit rate, miss penalty).
    tps_source: decode-rate source (defaults to warm records if available).
    """
    params = _fit_p2_params(p2_recs)
    tps, _, _ = _tps_of(tps_source if tps_source is not None else p2_recs)
    sphp = _sphp_stats(sphp_recs)
    return PlacementCostModel(
        t_sparse_base_ms=params["t_sparse_base_ms"],
        t_dense_base_ms=params["t_dense_base_ms"],
        t_fusion_base_ms=params["t_fusion_base_ms"],
        t_prefill_base_ms=params["t_prefill_base_ms"],
        decode_tok_per_sec=tps,
        sphp_hit_rate=float(sphp["measured_hit_rate"]),
        t_sphp_miss_penalty_ms=float(sphp["miss_penalty_ms"]),
    )


def _tiers(recs: List[dict]) -> List[float]:
    return sorted({float(r["rtt_ms"]) for r in recs})


def regime_loto_validation(
    state_recs: List[dict],
    sphp_state_recs: List[dict],
    topology: str = "P2",
) -> Dict[str, object]:
    """Leave-one-tier-out validation at the regime level.

    For each RTT tier: fit all model parameters on the OTHER tiers, predict
    the held-out tier's mean TTFT, and compare with the observed tier mean.
    """
    tiers = _tiers(state_recs)
    per_tier = []
    for held in tiers:
        train = [r for r in state_recs if float(r["rtt_ms"]) != held]
        train_sphp = [r for r in sphp_state_recs if float(r["rtt_ms"]) != held]
        model = fit_placement_model(train, train_sphp, tps_source=train)
        if topology == "P2":
            pred = model.predict_ttft_p2(rtt_ms=held)
            obs = _mean([r["ttft_ms"] for r in state_recs if float(r["rtt_ms"]) == held])
        elif topology == "P2-SPHP":
            pred = model.predict_ttft_p2_sphp(rtt_ms=held)
            obs = _mean([r["ttft_ms"] for r in sphp_state_recs if float(r["rtt_ms"]) == held])
        else:
            raise ValueError(topology)
        per_tier.append({
            "rtt_ms": held,
            "predicted_mean_ms": round(pred, 1),
            "observed_mean_ms": round(obs, 1),
            "ape_pct": round(abs(pred - obs) / max(obs, 1.0) * 100.0, 2),
        })
    pairs = [(t["predicted_mean_ms"], t["observed_mean_ms"]) for t in per_tier]
    return {
        "protocol": "leave-one-tier-out on RTT tiers; regime (tier-mean) level",
        "n_tiers": len(tiers),
        "per_tier": per_tier,
        "regime_mape_pct": round(_mape(pairs), 2),
        "regime_r2": round(_r2(pairs), 4),
    }


def component_residual_check(state_recs: List[dict], model: PlacementCostModel) -> Dict[str, object]:
    """Form-error diagnostic: per-record prediction given measured components."""
    pairs = []
    for r in state_recs:
        pred = (
            max(r["sparse_ms"], r["dense_ms"])
            + r["fusion_ms"]
            + model.wan_delay_ms(model.query_bytes + 10 * model.doc_id_bytes, r["rtt_ms"], 100.0)
            + 5 * model.t_hydrate_per_doc_ms
            + model.t_prefill_base_ms
        )
        pairs.append((pred, r["ttft_ms"]))
    return {
        "n": len(pairs),
        "mape_pct": round(_mape(pairs), 2),
        "note": "given measured retrieval components; isolates wan+hydrate+prefill form error",
    }


def sphp_branch_check(sphp_recs: List[dict], model: PlacementCostModel) -> Dict[str, object]:
    """Validate the hit/miss branch forms against measured sphp records."""
    hint_payload = model.query_bytes + 5 * model.doc_id_bytes
    fused_payload = model.query_bytes + 10 * model.doc_id_bytes

    hit_pairs, miss_pairs = [], []
    for r in sphp_recs:
        if r.get("sphp_hit") is True:
            t_prefill_finish = (
                r["sparse_ms"]
                + model.wan_delay_ms(hint_payload, r["rtt_ms"], 100.0)
                + 5 * model.t_hydrate_per_doc_ms
                + model.t_prefill_base_ms
            )
            t_fused_arrival = (
                r["dense_ms"]
                + r["fusion_ms"]
                + model.wan_delay_ms(fused_payload, r["rtt_ms"], 100.0)
            )
            hit_pairs.append((max(t_fused_arrival, t_prefill_finish), r["ttft_ms"]))
        elif r.get("sphp_hit") is False:
            base = (
                max(r["sparse_ms"], r["dense_ms"])
                + r["fusion_ms"]
                + model.wan_delay_ms(fused_payload, r["rtt_ms"], 100.0)
                + 5 * model.t_hydrate_per_doc_ms
                + model.t_prefill_base_ms
            )
            miss_pairs.append((base + model.t_sphp_miss_penalty_ms, r["ttft_ms"]))

    return {
        "hit_branch": {"n": len(hit_pairs), "mape_pct": round(_mape(hit_pairs), 2)},
        "miss_branch": {"n": len(miss_pairs), "mape_pct": round(_mape(miss_pairs), 2)},
        "note": "branch-conditional form check with per-record measured components",
    }


def generate_crossover_table(model: PlacementCostModel, cache_state: str) -> List[Dict[str, object]]:
    """
    Sweep RTT (0..200 ms) x Bandwidth (10..1000 Mbps) evaluating TTFT for
    P2, P2-SPHP, and P3 to derive optimal placement policies, under the
    fitted parameters of one cache state.
    """
    rtt_tiers = [0, 10, 25, 50, 100, 150, 200]
    bw_tiers = [10, 50, 100, 500, 1000]
    table = []
    for rtt in rtt_tiers:
        for bw in bw_tiers:
            p2 = model.predict_ttft_p2(rtt, bw)
            p2_sphp = model.predict_ttft_p2_sphp(rtt, bw)
            p3 = model.predict_ttft_p3(rtt, bw)
            options = {"P2": p2, "P2-SPHP": p2_sphp, "P3": p3}
            best_topo = min(options, key=options.get)
            table.append({
                "rtt_ms": rtt,
                "bw_mbps": bw,
                "cache_state": cache_state,
                "ttft_p0_ms": round(model.predict_ttft_p0(), 1),
                "ttft_p2_ms": round(p2, 1),
                "ttft_p2_sphp_ms": round(p2_sphp, 1),
                "ttft_p3_ms": round(p3, 1),
                "sphp_speedup_pct": round(((p2 - p2_sphp) / p2) * 100.0, 1),
                "optimal_topology": best_topo,
            })
    return table


def main(argv: Optional[List[str]] = None) -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    args = list(argv if argv is not None else sys.argv[1:])
    if args:
        campaign_path = Path(args[0])
    else:
        candidates = sorted(CAMPAIGN_GLOB.parent.glob(CAMPAIGN_GLOB.name))
        if not candidates:
            print(f"ERROR: no campaign JSONL found under {CAMPAIGN_GLOB.parent}", file=sys.stderr)
            return 1
        campaign_path = candidates[-1]  # newest by timestamped name

    records = load_campaign_records(campaign_path)
    cold = [r for r in records if _cache_state(r) == "cold"]
    warm = [r for r in records if _cache_state(r) == "warm"]
    sphp = [r for r in records if r.get("variant") == "sphp"]
    sphp_cold = [r for r in sphp if _cache_state(r) == "cold"]
    sphp_warm = [r for r in sphp if _cache_state(r) == "warm"]

    print("=" * 69)
    print("GEODISTRIBUTED HYBRID RAG: CAMPAIGN-DRIVEN COST MODEL REFIT")
    print("=" * 69)

    validation: Dict[str, object] = {
        "generator": "analysis/cost_model.py (campaign-driven refit)",
        "campaign": str(campaign_path),
        "n_records": len(records),
        "n_cold": len(cold),
        "n_warm": len(warm),
        "top_k_measured": sorted({int(r["top_k"]) for r in records}),
        "regimes_measured_ms": _tiers(records),
    }

    tps, n_used, n_excl = _tps_of(warm)
    validation["decode"] = {
        "warm_median_tok_per_sec": round(tps, 2),
        "cold_median_tok_per_sec": round(_tps_of(cold)[0], 2),
        "n_tps_used": n_used,
        "n_tps_excluded_degenerate": n_excl,
        "exclusion_band": list(TPS_BAND),
    }

    cache_states: Dict[str, object] = {}
    crossover_tables: Dict[str, List[Dict[str, object]]] = {}
    for state, recs, sphp_state in (("cold", cold, sphp_cold), ("warm", warm, sphp_warm)):
        p2_recs = [r for r in recs if r.get("variant") == "baseline"]
        model = fit_placement_model(p2_recs, sphp_state, tps_source=warm)
        params = {
            "t_sparse_base_ms": round(model.t_sparse_base_ms, 2),
            "t_dense_base_ms": round(model.t_dense_base_ms, 2),
            "t_fusion_base_ms": round(model.t_fusion_base_ms, 4),
            "t_prefill_base_ms": round(model.t_prefill_base_ms, 2),
            "decode_tok_per_sec": round(model.decode_tok_per_sec, 2),
            "sphp_hit_rate_measured": round(model.sphp_hit_rate, 4),
            "t_sphp_miss_penalty_ms": round(model.t_sphp_miss_penalty_ms, 1),
        }
        loto_p2 = regime_loto_validation(p2_recs, sphp_state, topology="P2")
        loto_sphp = regime_loto_validation(p2_recs, sphp_state, topology="P2-SPHP")
        comp = component_residual_check(p2_recs, model)
        branch = sphp_branch_check(sphp_state, model)
        cache_states[state] = {
            "n_p2": len(p2_recs),
            "n_sphp": len(sphp_state),
            "fitted_params": params,
            "sphp_measurement": _sphp_stats(sphp_state),
            "regime_validation": {"P2": loto_p2, "P2-SPHP": loto_sphp},
            "component_residual_check": comp,
            "sphp_branch_check": branch,
        }
        crossover_tables[state] = generate_crossover_table(model, state)

        print(f"\n--- {state.upper()} state ({len(p2_recs)} baseline / {len(sphp_state)} sphp) ---")
        for k, v in params.items():
            print(f"  {k}: {v}")
        print(f"  LOTO regime MAPE: P2={loto_p2['regime_mape_pct']}% "
              f"(R^2={loto_p2['regime_r2']})  SPHP={loto_sphp['regime_mape_pct']}%")
        print(f"  component residual MAPE: {comp['mape_pct']}%")
        print(f"  sphp branch MAPE: hit={branch['hit_branch']['mape_pct']}% "
              f"(n={branch['hit_branch']['n']})  miss={branch['miss_branch']['mape_pct']}% "
              f"(n={branch['miss_branch']['n']})")

    validation["cache_states"] = cache_states

    # Measured RTT sensitivity from baseline warm tier means (steady state).
    base_warm = [r for r in warm if r.get("variant") == "baseline"]
    tier_means = [
        (rtt, _mean([r["ttft_ms"] for r in base_warm if float(r["rtt_ms"]) == rtt]))
        for rtt in _tiers(base_warm)
    ]
    slope = _ols_slope([t for t, _ in tier_means], [m for _, m in tier_means])
    validation["rtt_sensitivity"] = {
        "warm_baseline_tier_means_ms": {str(int(t)): round(m, 1) for t, m in tier_means},
        "warm_slope_ms_per_ms": round(slope, 3),
        "note": "measured TTFT response to netem RTT; the analytical wan term "
                "(rtt/2 per modelled crossing) is an upper bound on this response",
    }
    print(f"\nRTT sensitivity (warm baseline): slope={slope:.3f} ms/ms")
    for t, m in tier_means:
        print(f"  rtt={int(t):>3d} ms -> mean TTFT {m:.1f} ms")

    validation["legacy_reconciliation"] = {
        "old_paper_macro_mape_pct": 23.73,
        "old_artifact_macro_mape_pct": 29.48,
        "resolution": "Both figures were produced by the pre-refit fitter against the "
                      "deprecated llama3-awq results_matrix_gpu.json with a per-query "
                      "validation protocol. The generator has since been re-baselined to "
                      "qwen3.8-27b and every number re-measured under bidirectional netem; "
                      "per-query R^2/MAPE is not a meaningful target for a population-mean "
                      "model. Both figures are obsolete and superseded by the regime-level "
                      "leave-one-tier-out validation in cache_states.*.regime_validation.",
    }

    out_validation = RESULTS_DIR / "cost_model_validation.json"
    with open(out_validation, "w", encoding="utf-8") as f:
        json.dump(validation, f, indent=2)
        f.write("\n")
    print(f"\nWrote {out_validation}")

    # Crossover: warm-fitted table is the steady-state placement policy
    # (primary artifact); cold-fitted table documents the transient regime.
    crossover_warm = crossover_tables["warm"]
    crossover_cold = crossover_tables["cold"]
    with open(RESULTS_DIR / "crossover_analysis.json", "w", encoding="utf-8") as f:
        json.dump(crossover_warm, f, indent=2)
    with open(RESULTS_DIR / "crossover_analysis_cold.json", "w", encoding="utf-8") as f:
        json.dump(crossover_cold, f, indent=2)
    csv_path = RESULTS_DIR / "crossover_analysis.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(crossover_warm[0].keys()))
        writer.writeheader()
        writer.writerows(crossover_warm)

    print(f"Crossover analysis exported to:")
    print(f"  JSON (warm): {RESULTS_DIR / 'crossover_analysis.json'}")
    print(f"  JSON (cold): {RESULTS_DIR / 'crossover_analysis_cold.json'}")
    print(f"  CSV  (warm): {csv_path}")

    print("\nSample warm-state placement decisions:")
    print(f"{'RTT (ms)':<10} | {'BW (Mbps)':<10} | {'P2 (ms)':<10} | {'P2-SPHP (ms)':<14} | {'P3 (ms)':<10} | {'Speedup':<10} | {'Optimal'}")
    print("-" * 85)
    for r in crossover_warm:
        if r["bw_mbps"] in (50, 100) and r["rtt_ms"] in (10, 50, 100, 200):
            print(f"{r['rtt_ms']:<10} | {r['bw_mbps']:<10} | {r['ttft_p2_ms']:<10} | "
                  f"{r['ttft_p2_sphp_ms']:<14} | {r['ttft_p3_ms']:<10} | "
                  f"{r['sphp_speedup_pct']:>5.1f}%     | {r['optimal_topology']}")
    print("=" * 69)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
