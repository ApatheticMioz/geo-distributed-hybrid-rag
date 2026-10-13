#!/usr/bin/env python3
"""
Revision-Board evaluation (RB validation step): like-for-like A/B of the three
Revision-Board proposals RP-1, RP-2, RP-3 from docs/revision_log.md.

Single concern: for each proposal, fit the *proposed* functional form on the
IDENTICAL data used for the *current* (production-form) fit — on BOTH campaign
surfaces:
    * 2026-09-18 : experiments/bench/campaigns/campaign_20260918T130756Z.jsonl
    * 2026-10-13 : experiments/bench/campaigns/campaign_v2_confirm_2node_20261013.jsonl
and score both arms leave-one-RTT-tier-out (regime/tier-mean level — the
paper's C3 protocol), per regime (P2 / P2-SPHP x cold / warm), plus the
*targeted* residual pattern of each proposal, before vs after.

Proposals under test
--------------------
RP-1  SPHP miss penalty:  current = per-state constant Tmiss (mean recorded
       wasted_prefill_ms);  proposed = regime-conditional Tmiss(T) = a_s + g_s*rtt
       fitted from the realized miss penalty (SPHP-miss tier mean minus P2 tier
       mean, per state).
RP-2  SPHP hit credit:    current = none (TTFT_hit = max(t_f, t_p));  proposed =
       state-dependent hit credit C_s with C_w = 0 by construction and C_c
       fitted per-state on the cold tier-level measured-vs-modeled SPHP
       speedup (deterministic grid search, 0..2000 ms in 0.5 ms steps).
RP-3  WAN term:            current = analytic 0.5*rtt + serialization;  proposed =
       per-state fitted Twan_s(rtt) = b_s + g_s*rtt (+ same serialization),
       with (b_s, g_s) regressed from the P2 residual (ttft - max(sparse,
       dense) - fusion), the same residual the production fit absorbs into
       the constant prefill base.

Like-for-like discipline
------------------------
In every A/B the baseline stage terms (sparse/dense/fusion/prefill means, hit
rate, decode tps) are fitted by the IDENTICAL recipe as the production fitter
(cost_model.fit_placement_model) on the identical records; the proposed form
is the only difference between arms.  The variant's NEW term is estimated once
on the full state data and held fixed across LOTO folds (miss counts per tier
are 10-30 records, so per-fold estimation would inject pure noise; the only
per-fold variant estimate is RP-2's C_c, which is cheap and stable).  The
protocol, tiers and scoring are identical in both arms.

VERDICT RULES (explicit, deterministic; applied identically to both surfaces)
-----------------------------------------------------------------------------
A proposal *wins on a data set* iff ALL of:
  (i)   its targeted LOTO regime MAPE is strictly lower in every targeted regime
        (RP-1: SPHP cold+warm;  RP-2: SPHP cold — warm is untouched by
         construction;  RP-3: P2 cold+warm);
  (ii)  every guard regime's LOTO MAPE degrades by at most EPS_GUARD_PP;
  (iii) its targeted residual pattern is removed:
        RP-1 : miss-branch branch-check MAPE lower AND miss-branch residual
               slope on RTT smaller in magnitude (warm, where the rising
               penalty pattern lives; and cold for consistency);
        RP-2 : cold measured-vs-modeled speedup gap |gap| strictly smaller,
               warm gap unchanged (<= 1e-9, since C_w = 0 by construction);
        RP-3 : P2 per-record residual slope on rtt smaller in magnitude (both
               states) AND the fitted warm WAN slope closer to the measured
               tier-mean RTT slope than the analytic 0.5.
Overall:  adopted (analysis-level) iff wins on BOTH data sets;  rejected iff it
wins on none;  deferred iff it wins on exactly one — the output states exactly
what a future data set must show.  "Adopted (analysis-level)" is the maximum
claim of this script: NO model code, paper text or systems/ artifact is
modified here.  Adoption-porting of a positive verdict is a separate slice.

Determinism: no stochastic steps exist (all OLS / exhaustive deterministic
grid).  A fixed seed is nevertheless declared and recorded in the output for
protocol consistency with the seeded query pools.

Fences honoured: reads only the two campaign JSONLs and
experiments/analysis/cost_model.py (imported, never patched); writes only
    experiments/analysis/results/revision_board_eval_20261014.json
    experiments/analysis/results/revision_board_eval_20261014.md
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

_HERE = Path(__file__).resolve().parent
PROJECT_ROOT = _HERE.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from experiments.analysis.cost_model import (          # noqa: E402
    _cache_state,
    _mape,
    _mean,
    _r2,
    _sphp_stats,
    _tiers,
    _tps_of,
    _wilson_ci,
    load_campaign_records,
)

OLD_PATH = PROJECT_ROOT / "experiments/bench/campaigns/campaign_20260918T130756Z.jsonl"
NEW_PATH = PROJECT_ROOT / "experiments/bench/campaigns/campaign_v2_confirm_2node_20261013.jsonl"
OUT_JSON = _HERE / "results/revision_board_eval_20261014.json"
OUT_MD = _HERE / "results/revision_board_eval_20261014.md"

SEED = 42                 # recorded; no stochastic steps exist
BW = 100.0                # Mbps — same as the production fit protocol
TOPK = 10                 # constant across both campaigns
# hydration per-doc term: PlacementCostModel's default (0.28 ms).  Both the
# production fit (fit_placement_model) and the 09-18 refit's LOTO protocol
# (costmodel_refit_v2_20261013.py's regime_loto_validation) use this default,
# so the "current" arm reproduces the published LOTO figures exactly
# (old 1.28/2.76/10.55/5.81; new 2.15/4.13/7.83/4.37).  The published
# *crossover/policy* numbers (5.57% warm / 9.72% cold) were reconstructed in
# that refit with the class default 5.0 ms; the crossover block below
# (HYDRATE_POLICY_MS) reproduces that baseline for the delta comparison.
HYDRATE_PER_DOC_MS = 0.28
HYDRATE_POLICY_MS = 5.0

# RP-2 deterministic grid for the cold-only KV-prefix credit C_c
CC_GRID_COLD = [0.0 + 0.5 * i for i in range(0, 4001)]   # 0 .. 2000 ms

# verdict-rule constants
EPS_GUARD_PP = 0.25       # max allowed degradation (pp) of any guard regime
EPS_WARM_UNCHANGED_PP = 1e-9  # RP-2 warm must be bit-identical (C_w = 0)

RTT_SWEEP = [0, 10, 25, 50, 100, 150, 200]
BW_SWEEP = [10, 50, 100, 500, 1000]


# --------------------------------------------------------------------------- #
# numeric helpers (pure, deterministic OLS)
# --------------------------------------------------------------------------- #
def ols2(x: List[float], y: List[float]):
    """slope, intercept, R^2 of a simple OLS y ~ a + b*x."""
    xa, ya = np.asarray(x, float), np.asarray(y, float)
    n = len(xa)
    if n < 3:
        return 0.0, float(ya.mean()) if n else float("nan"), float("nan")
    xm, ym = float(xa.mean()), float(ya.mean())
    sxx = float(((xa - xm) ** 2).sum())
    b = float(((xa - xm) * (ya - ym)).sum() / sxx) if sxx > 0 else 0.0
    a = ym - b * xm
    yhat = a + b * xa
    ss_res = float(((ya - yhat) ** 2).sum())
    ss_tot = float(((ya - ym) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    return b, a, r2


def slope_of(x: List[float], y: List[float]) -> float:
    return ols2(x, y)[0] if len(x) >= 3 else 0.0


def r2_of(x: List[float], y: List[float]) -> float:
    return ols2(x, y)[2] if len(x) >= 3 else float("nan")


def split_state(recs: List[dict]):
    cold = [r for r in recs if _cache_state(r) == "cold"]
    warm = [r for r in recs if _cache_state(r) == "warm"]
    return cold, warm


def base_and_sphp(recs: List[dict]):
    base = [r for r in recs if r.get("variant") == "baseline"]
    sphp = [r for r in recs if r.get("variant") == "sphp"]
    return base, sphp


def tier_mean(recs: List[dict], rtt: float) -> float:
    xs = [r["ttft_ms"] for r in recs if float(r["rtt_ms"]) == rtt]
    return _mean(xs) if xs else float("nan")


# --------------------------------------------------------------------------- #
# the two model forms
# --------------------------------------------------------------------------- #
def wan_current(payload_bytes: int, rtt: float, bw: float = BW) -> float:
    """Current production WAN term: rtt/2 + serialization (eq. WAN)."""
    if bw <= 0:
        bw = 100.0
    return 0.5 * rtt + (payload_bytes * 8.0) / (bw * 1e6) * 1000.0


def wan_fitted(b: float, g: float, payload_bytes: int, rtt: float,
               bw: float = BW) -> float:
    """Proposed RP-3 WAN term: fitted b_s + g_s*rtt + same serialization."""
    if bw <= 0:
        bw = 100.0
    return b + g * rtt + (payload_bytes * 8.0) / (bw * 1e6) * 1000.0


def p2_pred(p: Dict[str, float], rtt: float, bw: float = BW,
            wan_kind: str = "current", wan_b: float = 0.0,
            wan_g: float = 0.5) -> float:
    """P2 TTFT; wan_kind 'current' (rtt/2) or 'fitted' (b + g*rtt)."""
    retrieval = max(p["t_sparse_base_ms"], p["t_dense_base_ms"])
    payload = p["query_bytes"] + TOPK * p["doc_id_bytes"]
    wan = (wan_current(payload, rtt, bw) if wan_kind == "current"
           else wan_fitted(wan_b, wan_g, payload, rtt, bw))
    return (retrieval + p["t_fusion_base_ms"] + wan
            + p["t_hydrate_per_doc_ms"] * min(TOPK, 5)
            + p["t_prefill_base_ms"])


def sphp_pred(p: Dict[str, float], rtt: float, bw: float = BW,
              miss_fn: Optional[callable] = None,
              credit: float = 0.0,
              wan_kind: str = "current", wan_b: float = 0.0,
              wan_g: float = 0.5) -> float:
    """
    P2-SPHP expected TTFT with pluggable variant terms.
      miss_fn(rtt) : RP-1 regime-conditional miss penalty (None -> current
                     per-state constant)
      credit       : RP-2 state-dependent hit credit (0 -> current)
      wan_kind/_b/_g : RP-3 WAN form
    """
    hint_payload = p["query_bytes"] + 5 * p["doc_id_bytes"]
    fused_payload = p["query_bytes"] + TOPK * p["doc_id_bytes"]

    def wan(payload: int) -> float:
        return (wan_current(payload, rtt, bw) if wan_kind == "current"
                else wan_fitted(wan_b, wan_g, payload, rtt, bw))

    t_prefill_finish = (p["t_sparse_base_ms"] + wan(hint_payload)
                        + p["t_hydrate_per_doc_ms"] * min(TOPK, 5)
                        + p["t_prefill_base_ms"])
    t_fused_arrival = p["t_dense_base_ms"] + p["t_fusion_base_ms"] + wan(fused_payload)
    ttft_hit = max(t_fused_arrival, t_prefill_finish) - credit
    if miss_fn is None:
        ttft_miss = p2_pred(p, rtt, bw, wan_kind, wan_b, wan_g) \
            + p["t_sphp_miss_penalty_ms"]
    else:
        ttft_miss = p2_pred(p, rtt, bw, wan_kind, wan_b, wan_g) + miss_fn(rtt)
    return p["sphp_hit_rate"] * ttft_hit + (1.0 - p["sphp_hit_rate"]) * ttft_miss


# --------------------------------------------------------------------------- #
# variant-term fits (per state, full data; deterministic)
# --------------------------------------------------------------------------- #
def fit_rp1_miss_penalty(base: List[dict], sphp: List[dict]) -> Dict:
    """
    RP-1: Tmiss(T) = a_s + g_s*rtt per state, fitted from the realized miss
    penalty (SPHP-miss tier mean minus P2 tier mean) on the state's tiers.
    """
    tiers = _tiers(sphp)
    pen, rtts = [], []
    for t in tiers:
        miss_tier = [r for r in sphp if r.get("sphp_hit") is False
                     and float(r["rtt_ms"]) == t]
        if not miss_tier:
            continue
        pen.append(tier_mean(miss_tier, t) - tier_mean(base, t))
        rtts.append(t)
    g, a, r2 = ols2(rtts, pen)
    return {
        "a_ms": round(a, 2),
        "g_ms_per_ms": round(g, 4),
        "r2_on_rtt": round(r2, 4),
        "realized_per_tier_ms": {str(int(t)): round(v, 1) for t, v in zip(rtts, pen)},
        "n_miss_per_tier": {str(int(t)): len([r for r in sphp
                            if r.get("sphp_hit") is False
                            and float(r["rtt_ms"]) == t]) for t in rtts},
        "pen_fn": (lambda rt, a=a, g=g: a + g * rt),
    }


def fit_rp3_wan(base: List[dict], p: Dict[str, float]) -> Dict:
    """
    RP-3: Twan_s(rtt) = b_s + g_s*rtt (+serialization), per state, from the
    P2 residual (ttft - max(sparse,dense) - fusion) regressed on rtt.  The
    residual equals WAN + hydration + prefill by the stage model, and the
    production fit absorbs it into the constant prefill base; regressing it
    on rtt isolates the WAN slope and hands the remainder back to the
    (constant) hydration+prefill intercept, i.e. exactly the production
    residual interpretation with the WAN slope unfixed.
    """
    rtts, res = [], []
    for r in base:
        rtts.append(float(r["rtt_ms"]))
        res.append(r["ttft_ms"] - max(r["sparse_ms"], r["dense_ms"]) - r["fusion_ms"])
    g, a, r2 = ols2(rtts, res)
    wan_b = a - p["t_hydrate_per_doc_ms"] * min(TOPK, 5) - p["t_prefill_base_ms"]
    tmeans = [tier_mean(base, t) for t in _tiers(base)]
    return {
        "b_ms": round(wan_b, 2),
        "g_ms_per_ms": round(g, 4),
        "r2_residual_on_rtt": round(r2, 4),
        "analytic_g_ms_per_ms": 0.5,
        "measured_p2_tier_mean_slope_ms_per_ms": round(slope_of(_tiers(base), tmeans), 4),
    }


def fit_rp2_credit(base: List[dict], sphp: List[dict], p: Dict[str, float],
                   grid: List[float]) -> Dict:
    """
    RP-2: state-dependent hit credit C_s (C_w = 0 by construction; C_c fitted
    in cold) minimizing the mean tier-level |measured SPHP speedup - modeled
    SPHP speedup|, with per-tier measured hit rates (deterministic grid).
    """
    tiers = _tiers(sphp)
    measured = [100.0 * (1.0 - tier_mean(sphp, t) / tier_mean(base, t))
                for t in tiers]

    def gap(cc: float) -> List[float]:
        out = []
        for t in tiers:
            s_t = [r for r in sphp if float(r["rtt_ms"]) == t]
            hr_t = _mean([1.0 if r.get("sphp_hit") else 0.0 for r in s_t])
            hint = p["query_bytes"] + 5 * p["doc_id_bytes"]
            fused = p["query_bytes"] + TOPK * p["doc_id_bytes"]
            ttft_hit = max(
                p["t_dense_base_ms"] + p["t_fusion_base_ms"] + wan_current(fused, t),
                p["t_sparse_base_ms"] + wan_current(hint, t)
                + p["t_hydrate_per_doc_ms"] * min(TOPK, 5) + p["t_prefill_base_ms"],
            ) - cc
            ttft_miss = p2_pred(p, t) + p["t_sphp_miss_penalty_ms"]
            sm = hr_t * ttft_hit + (1.0 - hr_t) * ttft_miss
            out.append(100.0 * (1.0 - sm / tier_mean(base, t)))
        return [m - s for m, s in zip(measured, out)]

    best_cc, best_err = grid[0], float("inf")
    for cc in grid:
        err = _mean([abs(g) for g in gap(cc)])
        if err < best_err - 1e-12:
            best_err, best_cc = err, cc
    g0 = gap(0.0)
    gb = gap(best_cc)
    # per-tier implied credit: on the hit branch, speedup is linear in C with
    # slope (hr_t / bm) * 100, so C_implied,t = gap_t / slope_t
    implied = []
    for t, g_t in zip(tiers, g0):
        s_t = [r for r in sphp if float(r["rtt_ms"]) == t]
        hr_t = _mean([1.0 if r.get("sphp_hit") else 0.0 for r in s_t])
        implied.append(g_t / (hr_t / tier_mean(base, t)) if hr_t > 0 else float("nan"))
    return {
        "C_ms_fitted": round(best_cc, 1),
        "grid": "0..2000 ms step 0.5 (deterministic)",
        "mean_tier_gap_pp": round(best_err, 3),
        "measured_tier_speedup_pct": {str(int(t)): round(v, 2)
                                      for t, v in zip(tiers, measured)},
        "modeled_tier_speedup_pct_C0": {str(int(t)): round(m - g, 2)
                                        for t, m, g in zip(tiers, measured, g0)},
        "implied_per_tier_C_ms": {str(int(t)): round(v, 1)
                                  for t, v in zip(tiers, implied)},
    }


# --------------------------------------------------------------------------- #
# LOTO evaluation of one form variant (regime level, the paper's protocol)
# --------------------------------------------------------------------------- #
def loto_evaluate(base: List[dict], sphp: List[dict], tps_source: List[dict],
                  variant: str,
                  rp1: Optional[Dict] = None,
                  rp2_grid: Optional[List[float]] = None,
                  rp3: Optional[Dict] = None) -> Dict:
    """
    Leave-one-RTT-tier-out, regime (tier-mean) level, for one variant form.
    Fold fit: base terms + hit rate exactly like the production fitter on the
    3 training tiers; the variant's full-data terms are held fixed (RP-2's
    C_c is the only per-fold variant estimate — fitted on the 3 training
    tiers, grid search).
    """
    tiers = _tiers(base)

    def fold_params(held: float) -> Dict[str, float]:
        tr_b = [r for r in base if float(r["rtt_ms"]) != held]
        tr_s = [r for r in sphp if float(r["rtt_ms"]) != held]
        from experiments.analysis.cost_model import fit_placement_model
        m = fit_placement_model(tr_b, tr_s, tps_source=tr_b)
        p = {
            "t_sparse_base_ms": m.t_sparse_base_ms,
            "t_dense_base_ms": m.t_dense_base_ms,
            "t_fusion_base_ms": m.t_fusion_base_ms,
            "t_hydrate_per_doc_ms": HYDRATE_PER_DOC_MS,
            "t_prefill_base_ms": m.t_prefill_base_ms,
            "decode_tok_per_sec": m.decode_tok_per_sec,
            "doc_id_bytes": m.doc_id_bytes,
            "doc_text_bytes": m.doc_text_bytes,
            "query_bytes": m.query_bytes,
            "sphp_hit_rate": m.sphp_hit_rate,
            "t_sphp_miss_penalty_ms": m.t_sphp_miss_penalty_ms,
        }
        if variant == "RP-2":
            # per-fold C_c on training tiers (warm: grid = [0.0] -> credit 0)
            if rp2_grid is None or len(rp2_grid) <= 1:
                p["credit"] = 0.0
            else:
                best_cc = rp2_grid[0]
                tr_tiers = [t for t in tiers if t != held]
                best_cc, best_err = rp2_grid[0], float("inf")
                for cc in rp2_grid:
                    errs = []
                    for t in tr_tiers:
                        s_t = [r for r in tr_s if float(r["rtt_ms"]) == t]
                        b_t = [r for r in tr_b if float(r["rtt_ms"]) == t]
                        if not s_t or not b_t:
                            continue
                        hr_t = _mean([1.0 if r.get("sphp_hit") else 0.0 for r in s_t])
                        hint = p["query_bytes"] + 5 * p["doc_id_bytes"]
                        fused = p["query_bytes"] + TOPK * p["doc_id_bytes"]
                        th = max(
                            p["t_dense_base_ms"] + p["t_fusion_base_ms"]
                            + wan_current(fused, t),
                            p["t_sparse_base_ms"] + wan_current(hint, t)
                            + p["t_hydrate_per_doc_ms"] * min(TOPK, 5)
                            + p["t_prefill_base_ms"],
                        ) - cc
                        tm = p2_pred(p, t) + p["t_sphp_miss_penalty_ms"]
                        sm = hr_t * th + (1.0 - hr_t) * tm
                        errs.append(abs(100.0 * (1 - sm / tier_mean(b_t, t))
                                        - 100.0 * (1 - tier_mean(s_t, t) / tier_mean(b_t, t))))
                    e = _mean(errs) if errs else float("inf")
                    if e < best_err - 1e-12:
                        best_err, best_cc = e, cc
                p["credit"] = best_cc
        return p

    per_tier = []
    for held in tiers:
        p = fold_params(held)
        if variant == "current":
            kw = dict(miss_fn=None, credit=0.0,
                      wan_kind="current", wan_b=0.0, wan_g=0.5)
        elif variant == "RP-1":
            kw = dict(miss_fn=rp1["pen_fn"] if rp1 else None, credit=0.0,
                      wan_kind="current", wan_b=0.0, wan_g=0.5)
        elif variant == "RP-2":
            kw = dict(miss_fn=None, credit=p.get("credit", 0.0),
                      wan_kind="current", wan_b=0.0, wan_g=0.5)
        elif variant == "RP-3":
            kw = dict(miss_fn=None, credit=0.0,
                      wan_kind="fitted",
                      wan_b=rp3["b_ms"] if rp3 else 0.0,
                      wan_g=rp3["g_ms_per_ms"] if rp3 else 0.5)
        else:
            raise ValueError(variant)
        pred_p2 = p2_pred(p, held, BW, kw["wan_kind"], kw["wan_b"], kw["wan_g"])
        pred_sphp = sphp_pred(p, held, BW, **kw)
        obs_p2 = tier_mean(base, held)
        obs_sphp = tier_mean(sphp, held)
        per_tier.append({
            "rtt_ms": held,
            "p2": {"pred_ms": round(pred_p2, 1), "obs_ms": round(obs_p2, 1),
                    "ape_pct": round(abs(pred_p2 - obs_p2) / max(obs_p2, 1.0) * 100, 2)},
            "sphp": {"pred_ms": round(pred_sphp, 1), "obs_ms": round(obs_sphp, 1),
                     "ape_pct": round(abs(pred_sphp - obs_sphp) / max(obs_sphp, 1.0) * 100, 2)},
            "sphp_speedup": {
                "obs_pct": round(100.0 * (1 - obs_sphp / obs_p2), 2),
                "pred_pct": round(100.0 * (1 - pred_sphp / pred_p2), 2),
            },
        })

    def loto_summary(key: str) -> Dict:
        pairs = [(t[key]["pred_ms"], t[key]["obs_ms"]) for t in per_tier]
        return {"regime_mape_pct": round(_mape(pairs), 2),
                "regime_r2": round(_r2(pairs), 4)}

    gaps = [t["sphp_speedup"]["obs_pct"] - t["sphp_speedup"]["pred_pct"]
            for t in per_tier]
    return {
        "protocol": "leave-one-RTT-tier-out; regime (tier-mean) level",
        "n_tiers": len(tiers),
        "per_tier": per_tier,
        "p2": loto_summary("p2"),
        "sphp": loto_summary("sphp"),
        "speedup_gap_pp_mean": round(_mean(gaps), 3) if gaps else float("nan"),
        "speedup_gap_pp_trend_per_ms_rtt": slope_of(
            [t["rtt_ms"] for t in per_tier], gaps),
    }


# --------------------------------------------------------------------------- #
# full-fit diagnostics: the *targeted* residual pattern of each proposal
# --------------------------------------------------------------------------- #
def branch_check(base: List[dict], sphp: List[dict], p: Dict[str, float],
                 variant: str, rp1: Optional[Dict], rp3: Optional[Dict],
                 rp2_cc: float) -> Dict:
    """
    Branch-conditional form check (like cost_model.sphp_branch_check) with the
    variant forms: per-record measured components + model WAN/prefill/hydrate.
    """
    hint = p["query_bytes"] + 5 * p["doc_id_bytes"]
    fused = p["query_bytes"] + TOPK * p["doc_id_bytes"]

    def wan(payload: int, rtt: float) -> float:
        if variant == "RP-3":
            return wan_fitted(rp3["b_ms"], rp3["g_ms_per_ms"], payload, rtt)
        return wan_current(payload, rtt)

    hit_pairs, miss_pairs, hit_res, miss_res, miss_rtts = [], [], [], [], []
    for r in sphp:
        rtt = float(r["rtt_ms"])
        if r.get("sphp_hit") is True:
            th = max(
                r["dense_ms"] + r["fusion_ms"] + wan(fused, rtt),
                r["sparse_ms"] + wan(hint, rtt)
                + 5 * p["t_hydrate_per_doc_ms"] + p["t_prefill_base_ms"],
            ) - (rp2_cc if variant == "RP-2" else 0.0)
            hit_pairs.append((th, r["ttft_ms"]))
            hit_res.append(100.0 * (th - r["ttft_ms"]) / max(r["ttft_ms"], 1.0))
        elif r.get("sphp_hit") is False:
            base_r = (max(r["sparse_ms"], r["dense_ms"]) + r["fusion_ms"]
                      + wan(fused, rtt) + 5 * p["t_hydrate_per_doc_ms"]
                      + p["t_prefill_base_ms"])
            pen = (p["t_sphp_miss_penalty_ms"] if (variant != "RP-1" or rp1 is None)
                   else rp1["pen_fn"](rtt))
            miss_pairs.append((base_r + pen, r["ttft_ms"]))
            miss_res.append(100.0 * (base_r + pen - r["ttft_ms"]) / max(r["ttft_ms"], 1.0))
            miss_rtts.append(rtt)
    return {
        "hit": {"n": len(hit_pairs), "mape_pct": round(_mape(hit_pairs), 2)},
        "miss": {"n": len(miss_pairs), "mape_pct": round(_mape(miss_pairs), 2),
                 "resid_slope_pct_per_ms_rtt": round(slope_of(miss_rtts, miss_res), 4)},
    }


def p2_residual_diagnostics(base: List[dict], p: Dict[str, float],
                            rp3: Optional[Dict]) -> Dict:
    """P2 per-record residual (pred - obs, %) vs rtt — the RP-3 pattern."""
    rtts, res_cur, res_rp3 = [], [], []
    for r in base:
        rtt = float(r["rtt_ms"])
        pred_c = p2_pred(p, rtt)
        res_cur.append(100.0 * (pred_c - r["ttft_ms"]) / max(r["ttft_ms"], 1.0))
        if rp3 is not None:
            pred_v = p2_pred(p, rtt, wan_kind="fitted",
                             wan_b=rp3["b_ms"], wan_g=rp3["g_ms_per_ms"])
            res_rp3.append(100.0 * (pred_v - r["ttft_ms"]) / max(r["ttft_ms"], 1.0))
        rtts.append(rtt)
    out = {
        "current": {"slope_pct_per_ms": round(slope_of(rtts, res_cur), 4),
                    "r2": round(r2_of(rtts, res_cur), 4),
                    "per_tier_mean_resid_ms": {
                        str(int(t)): round(_mean([x for x, tt in zip(res_cur, rtts) if tt == t]), 2)
                        for t in _tiers(base)}},
    }
    if rp3 is not None:
        out["rp3"] = {"slope_pct_per_ms": round(slope_of(rtts, res_rp3), 4),
                      "r2": round(r2_of(rtts, res_rp3), 4),
                      "per_tier_mean_resid_ms": {
                          str(int(t)): round(_mean([x for x, tt in zip(res_rp3, rtts) if tt == t]), 2)
                          for t in _tiers(base)}}
    return out


def speedup_diagnostics(base: List[dict], sphp: List[dict], p: Dict[str, float],
                        variant: str, rp2_cc: float) -> Dict:
    """Measured vs modeled SPHP speedup (record level) — the RP-2 pattern."""
    b_all = _mean([r["ttft_ms"] for r in base])
    s_all = _mean([r["ttft_ms"] for r in sphp])
    measured = 100.0 * (1.0 - s_all / b_all)
    p2m = p2_pred(p, 40.0)
    sm = sphp_pred(p, 40.0, credit=rp2_cc if variant == "RP-2" else 0.0)
    modeled = 100.0 * (1.0 - sm / p2m)
    return {
        "measured_pct": round(measured, 2),
        "modeled_pct": round(modeled, 2),
        "gap_pp": round(measured - modeled, 2),
    }


# --------------------------------------------------------------------------- #
# crossover / policy with a given form configuration (analysis-level)
# --------------------------------------------------------------------------- #
def crossover_and_policy(state: str, recs_state: List[dict],
                         p: Dict[str, float],
                         miss_fn: Optional[callable], credit: float,
                         wan_kind: str, wan_b: float, wan_g: float,
                         tiers: List[float]) -> Dict:
    sphp_state = [r for r in recs_state if r.get("variant") == "sphp"]
    base_state = [r for r in recs_state if r.get("variant") == "baseline"]

    def sweep():
        rows = []
        for rtt in RTT_SWEEP:
            for bw in BW_SWEEP:
                p2 = p2_pred(p, float(rtt), bw, wan_kind, wan_b, wan_g)
                ps = sphp_pred(p, float(rtt), bw, miss_fn=miss_fn, credit=credit,
                               wan_kind=wan_kind, wan_b=wan_b, wan_g=wan_g)
                rows.append({"rtt_ms": rtt, "bw_mbps": bw,
                             "p2_ms": round(p2, 1), "sphp_ms": round(ps, 1),
                             "adv_pct": round(100.0 * (p2 - ps) / p2, 3),
                             "argmin": "P2-SPHP" if ps < p2 else "P2"})
        return rows

    rows = sweep()

    def policy_error():
        per_tier = []
        # policy error is evaluated at the campaign's OWN RTT tiers (100 Mbps),
        # not on the fixed sweep grid
        for t in tiers:
            p2 = p2_pred(p, float(t), 100.0, wan_kind, wan_b, wan_g)
            ps = sphp_pred(p, float(t), 100.0, miss_fn=miss_fn, credit=credit,
                           wan_kind=wan_kind, wan_b=wan_b, wan_g=wan_g)
            row = {"rtt_ms": t,
                   "p2_ms": round(p2, 1), "sphp_ms": round(ps, 1),
                   "argmin": "P2-SPHP" if ps < p2 else "P2"}
            recs_t = base_state if row["argmin"] == "P2" else sphp_state
            realized = tier_mean(recs_t, t)
            pred = row["p2_ms"] if row["argmin"] == "P2" else row["sphp_ms"]
            per_tier.append({"rtt_ms": t, "selected": row["argmin"],
                             "pred_ms": pred, "realized_ms": round(realized, 1),
                             "abs_err_pct": round(
                                 abs(pred - realized) / max(realized, 1.0) * 100, 2)})
        pooled = round(_mape([(x["pred_ms"], x["realized_ms"]) for x in per_tier]), 2)
        return {"per_tier": per_tier, "pooled_abs_err_pct": pooled}

    return {
        "sphp_advantage": {
            "min_pct": round(min(r["adv_pct"] for r in rows), 2),
            "max_pct": round(max(r["adv_pct"] for r in rows), 2),
            "argmin_counts": {"P2-SPHP": sum(1 for r in rows if r["argmin"] == "P2-SPHP"),
                              "P2": sum(1 for r in rows if r["argmin"] == "P2")},
        },
        "policy": policy_error(),
        "rows": rows,
    }


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> int:
    random.seed(SEED)
    _HERE.mkdir(parents=True, exist_ok=True)

    out: Dict = {
        "generator": ("analysis/revision_board_eval_20261014.py — Revision-Board "
                      "like-for-like A/B of RP-1/RP-2/RP-3 (docs/revision_log.md)"),
        "seed": SEED,
        "determinism": ("no stochastic steps (all OLS / exhaustive deterministic "
                        "grids on the fixed 4 RTT tiers 0/15/40/80); seed 42 "
                        "recorded for protocol consistency with the seeded pools"),
        "bw_mbps": BW,
        "top_k": TOPK,
        "protocol": ("leave-one-RTT-tier-out, regime (tier-mean) level — the "
                     "paper's C3 validation protocol; identical data and "
                     "identical baseline-term fit recipe in both arms"),
        "verdict_rules": {
            "win_on_dataset": ("targeted LOTO regime MAPE strictly lower in every "
                               f"targeted regime + all guard regimes degrade <= "
                               f"{EPS_GUARD_PP} pp + targeted residual pattern "
                               "removed"),
            "targeted_regimes": {"RP-1": "SPHP cold+warm",
                                 "RP-2": "SPHP cold (warm C=0 by construction)",
                                 "RP-3": "P2 cold+warm (SPHP is the guard)"},
            "pattern_checks": {
                "RP-1": ("miss-branch branch-check MAPE lower AND miss-branch "
                         "residual slope on RTT smaller in magnitude (warm and "
                         "cold)"),
                "RP-2": ("cold measured-vs-modeled speedup gap strictly smaller; "
                         "warm gap unchanged (<= 1e-9 pp)"),
                "RP-3": ("P2 per-record residual slope on rtt smaller in magnitude "
                         "(both states) AND fitted warm WAN slope closer to the "
                         "measured tier-mean RTT slope than the analytic 0.5"),
            },
            "adopted": "wins on BOTH data sets -> 'adopted (analysis-level)'",
            "rejected": "wins on neither",
            "deferred": ("wins on exactly one; the output states what a future "
                         "data set must show"),
            "note": ("'adopted (analysis-level)' means the form is approved for "
                     "the analysis surface only; NOTHING in this script touches "
                     "the production cost model or systems/ — adoption-port is a "
                     "separate slice."),
        },
    }

    prop: Dict = {}
    states_cache: Dict = {}

    for ds_name, path in (("old_20260918", OLD_PATH), ("new_20261013", NEW_PATH)):
        recs = load_campaign_records(path)
        cold, warm = split_state(recs)
        ds: Dict = {"campaign": str(path), "n_records": len(recs),
                    "tiers": _tiers(recs), "states": {}}
        for state in ("cold", "warm"):
            recs_s = cold if state == "cold" else warm
            base, sphp = base_and_sphp(recs_s)

            # ---- baseline production-form fit (identical recipe) ----
            from experiments.analysis.cost_model import fit_placement_model
            m0 = fit_placement_model(base, sphp, tps_source=recs_s)
            p_cur = {
                "t_sparse_base_ms": m0.t_sparse_base_ms,
                "t_dense_base_ms": m0.t_dense_base_ms,
                "t_fusion_base_ms": m0.t_fusion_base_ms,
                "t_hydrate_per_doc_ms": HYDRATE_PER_DOC_MS,
                "t_prefill_base_ms": m0.t_prefill_base_ms,
                "decode_tok_per_sec": m0.decode_tok_per_sec,
                "doc_id_bytes": m0.doc_id_bytes,
                "doc_text_bytes": m0.doc_text_bytes,
                "query_bytes": m0.query_bytes,
                "sphp_hit_rate": m0.sphp_hit_rate,
                "t_sphp_miss_penalty_ms": m0.t_sphp_miss_penalty_ms,
            }
            sstats = _sphp_stats(sphp)

            # ---- variant term fits (full data, deterministic) ----
            rp1 = fit_rp1_miss_penalty(base, sphp)
            rp3 = fit_rp3_wan(base, p_cur)
            cc_grid = CC_GRID_COLD if state == "cold" else [0.0]
            rp2 = fit_rp2_credit(base, sphp, p_cur, cc_grid)

            # ---- LOTO: current vs proposed (same data, same recipe) ----
            loto = {
                "current": loto_evaluate(base, sphp, recs_s, "current"),
                "RP-1": loto_evaluate(base, sphp, recs_s, "RP-1", rp1=rp1),
                "RP-2": loto_evaluate(base, sphp, recs_s, "RP-2", rp2_grid=cc_grid),
                "RP-3": loto_evaluate(base, sphp, recs_s, "RP-3", rp3=rp3),
            }

            # ---- targeted residual-pattern diagnostics (full fit) ----
            cc_for_diag = rp2["C_ms_fitted"] if state == "cold" else 0.0
            pat = {
                "rp1": {
                    "miss_penalty_fit": {k: v for k, v in rp1.items()
                                         if k != "pen_fn"},
                    "miss_branch_current": branch_check(
                        base, sphp, p_cur, "current", None, None, 0.0)["miss"],
                    "miss_branch_proposed": branch_check(
                        base, sphp, p_cur, "RP-1", rp1, None, 0.0)["miss"],
                },
                "rp2": {
                    "credit_fit": {k: v for k, v in rp2.items() if k != "grid"},
                    "speedup_current": speedup_diagnostics(
                        base, sphp, p_cur, "current", 0.0),
                    "speedup_proposed": speedup_diagnostics(
                        base, sphp, p_cur, "RP-2", cc_for_diag),
                },
                "rp3": {
                    "wan_fit": rp3,
                    "p2_residual": p2_residual_diagnostics(base, p_cur, rp3),
                },
            }

            ds["states"][state] = {
                "n_p2": len(base), "n_sphp": len(sphp),
                "sphp_hit_rate_measured": sstats["measured_hit_rate"],
                "sphp_hit_rate_wilson95": sstats["hit_rate_wilson95"],
                "current_fit_params": {k: round(v, 4) for k, v in p_cur.items()},
                "loto": loto,
                "pattern": pat,
            }
            states_cache[(ds_name, state)] = {"recs": recs_s, "p": p_cur,
                                              "rp1": rp1, "rp2": rp2, "rp3": rp3}
        out[ds_name] = ds

    # ------------------------------------------------------------------ #
    # VERDICTS
    # ------------------------------------------------------------------ #
    def _lo(ds: str, st: str, v: str, topo: str) -> float:
        return out[ds]["states"][st]["loto"][v][topo]["regime_mape_pct"]

    verdicts: Dict = {}
    for prop in ("RP-1", "RP-2", "RP-3"):
        wins, notes = {}, []
        for ds in ("old_20260918", "new_20261013"):
            tgt_delta, guard_delta = {}, {}
            if prop == "RP-1":
                for st in ("cold", "warm"):
                    tgt_delta[f"sphp_{st}"] = (_lo(ds, st, "RP-1", "sphp")
                                              - _lo(ds, st, "current", "sphp"))
                    guard_delta[f"p2_{st}"] = (_lo(ds, st, "RP-1", "p2")
                                              - _lo(ds, st, "current", "p2"))
            elif prop == "RP-2":
                tgt_delta["sphp_cold"] = (_lo(ds, "cold", "RP-2", "sphp")
                                         - _lo(ds, "cold", "current", "sphp"))
                for st in ("cold", "warm"):
                    guard_delta[f"p2_{st}"] = (_lo(ds, st, "RP-2", "p2")
                                              - _lo(ds, st, "current", "p2"))
                guard_delta["sphp_warm"] = (_lo(ds, "warm", "RP-2", "sphp")
                                           - _lo(ds, "warm", "current", "sphp"))
            else:  # RP-3
                for st in ("cold", "warm"):
                    tgt_delta[f"p2_{st}"] = (_lo(ds, st, "RP-3", "p2")
                                            - _lo(ds, st, "current", "p2"))
                    guard_delta[f"sphp_{st}"] = (_lo(ds, st, "RP-3", "sphp")
                                                - _lo(ds, st, "current", "sphp"))
            tgt_ok = len(tgt_delta) > 0 and all(v < 0 for v in tgt_delta.values())
            guard_ok = all(v <= EPS_GUARD_PP for v in guard_delta.values())

            # pattern check (full-fit diagnostics; both states)
            if prop == "RP-1":
                pat_ok = True
                for st in ("cold", "warm"):
                    c = out[ds]["states"][st]["pattern"]["rp1"]
                    if not (c["miss_branch_proposed"]["mape_pct"]
                            < c["miss_branch_current"]["mape_pct"]
                            and abs(c["miss_branch_proposed"]["resid_slope_pct_per_ms_rtt"])
                            < abs(c["miss_branch_current"]["resid_slope_pct_per_ms_rtt"])):
                        pat_ok = False
            elif prop == "RP-2":
                pc = out[ds]["states"]["cold"]["pattern"]["rp2"]
                pw = out[ds]["states"]["warm"]["pattern"]["rp2"]
                pat_ok = (abs(pc["speedup_proposed"]["gap_pp"])
                          < abs(pc["speedup_current"]["gap_pp"])
                          and abs(pw["speedup_proposed"]["gap_pp"]
                                  - pw["speedup_current"]["gap_pp"])
                          <= EPS_WARM_UNCHANGED_PP)
            else:  # RP-3
                pat_ok = True
                for st in ("cold", "warm"):
                    pd_ = out[ds]["states"][st]["pattern"]["rp3"]["p2_residual"]
                    if not (abs(pd_["rp3"]["slope_pct_per_ms"])
                            < abs(pd_["current"]["slope_pct_per_ms"])):
                        pat_ok = False
                rw = out[ds]["states"]["warm"]["pattern"]["rp3"]["wan_fit"]
                if not (abs(rw["g_ms_per_ms"]
                            - rw["measured_p2_tier_mean_slope_ms_per_ms"])
                        < abs(0.5 - rw["measured_p2_tier_mean_slope_ms_per_ms"])):
                    pat_ok = False

            wins[ds] = bool(tgt_ok and guard_ok and pat_ok)
            notes.append({
                "data_set": ds,
                "targeted_delta_pp": {k: round(v, 3) for k, v in tgt_delta.items()},
                "guard_delta_pp": {k: round(v, 3) for k, v in guard_delta.items()},
                "targeted_strictly_lower": tgt_ok,
                "guards_within_eps": guard_ok,
                "targeted_pattern_removed": bool(pat_ok),
            })

        n_wins = sum(wins.values())
        if n_wins == 2:
            verdict = "adopted (analysis-level)"
            cond = None
        elif n_wins == 1:
            winner = [k for k, v in wins.items() if v][0]
            loser = [k for k, v in wins.items() if not v][0]
            verdict = "deferred"
            cond = (f"mixed evidence: the proposed form WINS on {winner} but "
                    f"FAILS on {loser}. A future data set (a successor campaign "
                    f"surface) must re-run this identical A/B and win on it, "
                    f"i.e. show (i) targeted LOTO regime MAPE strictly lower in "
                    f"every targeted regime, (ii) all guard regimes within "
                    f"{EPS_GUARD_PP} pp, and (iii) the targeted residual pattern "
                    "removed, as defined in this script's verdict rules. The "
                    "failure on the current surface is the specific regime "
                    "noted below — that is the physical condition a future "
                    "surface must change.")
        else:
            verdict = "rejected"
            cond = None
        verdicts[prop] = {"verdict": verdict, "wins": wins,
                          "deferred_condition": cond, "notes": notes}
        out["verdicts"] = verdicts  # publish incrementally

    out["verdicts"] = verdicts

    # ------------------------------------------------------------------ #
    # adopted-form crossover / policy deltas (analysis-level, new surface)
    # ------------------------------------------------------------------ #
    adopted = [p for p in ("RP-1", "RP-2", "RP-3")
               if verdicts[p]["verdict"] == "adopted (analysis-level)"]
    out["adopted_forms"] = adopted
    if adopted:
        from experiments.analysis.cost_model import PlacementCostModel
        deltas = {}
        for state in ("cold", "warm"):
            sc = states_cache[("new_20261013", state)]
            p = dict(sc["p"])
            # force the SAME hydration constant (5.0, the class default used by
            # the refit's published policy reconstruction) in BOTH the current
            # and adopted arms, so the policy delta is like-for-like.  The
            # hydration term is a fixed additive constant identical in P2 and
            # P2-SPHP, so it does not change the argmin decision.
            p["t_hydrate_per_doc_ms"] = HYDRATE_POLICY_MS
            # baseline: the production model object (full-campaign fit) as
            # reconstructed in the 09-18 refit's crossover block (hydration
            # constant = 5.0 ms, i.e. PlacementCostModel's class default),
            # evaluated at the campaign's own tiers — this reproduces the
            # published 5.57% warm / 9.72% cold policy numbers exactly.
            p0 = PlacementCostModel(
                t_sparse_base_ms=p["t_sparse_base_ms"],
                t_dense_base_ms=p["t_dense_base_ms"],
                t_fusion_base_ms=p["t_fusion_base_ms"],
                t_hydrate_per_doc_ms=HYDRATE_POLICY_MS,
                t_prefill_base_ms=p["t_prefill_base_ms"],
                decode_tok_per_sec=p["decode_tok_per_sec"],
                doc_id_bytes=p["doc_id_bytes"],
                doc_text_bytes=p["doc_text_bytes"],
                query_bytes=p["query_bytes"],
                sphp_hit_rate=p["sphp_hit_rate"],
                t_sphp_miss_penalty_ms=p["t_sphp_miss_penalty_ms"],
            )
            tiers = out["new_20261013"]["tiers"]
            base_state = [r for r in sc["recs"] if r.get("variant") == "baseline"]
            sphp_state = [r for r in sc["recs"] if r.get("variant") == "sphp"]

            def _policy_at_model(m, tiers):
                """argmin policy pooled error for a model-like object with
                predict_ttft_p2 / predict_ttft_p2_sphp, at the campaign tiers.
                The policy picks the cheaper variant and we score its PREDICTED
                TTFT against the REALIZED tier mean of that variant's records."""
                per_tier = []
                for t in tiers:
                    p2 = m.predict_ttft_p2(float(t), 100.0)
                    ps = m.predict_ttft_p2_sphp(float(t), 100.0)
                    chosen = "P2-SPHP" if ps < p2 else "P2"
                    recs_t = sphp_state if chosen == "P2-SPHP" else base_state
                    realized = tier_mean(recs_t, t)
                    pred = ps if chosen == "P2-SPHP" else p2
                    per_tier.append({"rtt_ms": t, "selected": chosen,
                                     "pred_ms": round(pred, 1),
                                     "realized_ms": round(realized, 1),
                                     "abs_err_pct": round(
                                         abs(pred - realized) / max(realized, 1.0) * 100, 2)})
                pooled = round(_mape([(x["pred_ms"], x["realized_ms"])
                                     for x in per_tier]), 2)
                return {"per_tier": per_tier, "pooled_abs_err_pct": pooled}
            cur_policy = _policy_at_model(p0, tiers)
            miss_fn = sc["rp1"]["pen_fn"] if "RP-1" in adopted else None
            credit = (sc["rp2"]["C_ms_fitted"]
                      if ("RP-2" in adopted and state == "cold") else 0.0)
            if "RP-3" in adopted:
                wk, wb, wg = "fitted", sc["rp3"]["b_ms"], sc["rp3"]["g_ms_per_ms"]
            else:
                wk, wb, wg = "current", 0.0, 0.5

            def adopted_pred_fn(rtt):
                return (p2_pred(p, rtt, 100.0, wk, wb, wg),
                        sphp_pred(p, rtt, 100.0, miss_fn=miss_fn, credit=credit,
                                  wan_kind=wk, wan_b=wb, wan_g=wg))
            per_tier = []
            for t in tiers:
                p2, ps = adopted_pred_fn(float(t))
                chosen = "P2-SPHP" if ps < p2 else "P2"
                recs_t = base_state if chosen == "P2" else sphp_state
                realized = tier_mean(recs_t, t)
                pred = ps if chosen == "P2-SPHP" else p2
                per_tier.append({"rtt_ms": t, "selected": chosen,
                                 "pred_ms": round(pred, 1),
                                 "realized_ms": round(realized, 1),
                                 "abs_err_pct": round(
                                     abs(pred - realized) / max(realized, 1.0) * 100, 2)})
            adv_policy = {"per_tier": per_tier,
                          "pooled_abs_err_pct": round(
                              _mape([(x["pred_ms"], x["realized_ms"])
                                     for x in per_tier]), 2)}
            cur = crossover_and_policy(state, sc["recs"], p,
                                       miss_fn=None, credit=0.0,
                                       wan_kind="current", wan_b=0.0, wan_g=0.5,
                                       tiers=tiers)
            adv = crossover_and_policy(state, sc["recs"], p,
                                       miss_fn=miss_fn, credit=credit,
                                       wan_kind=wk, wan_b=wb, wan_g=wg,
                                       tiers=tiers)
            flips = [f'rtt{r["rtt_ms"]}/bw{r["bw_mbps"]}'
                     for r in cur["rows"] for a in adv["rows"]
                     if (a["rtt_ms"], a["bw_mbps"]) == (r["rtt_ms"], r["bw_mbps"])
                     and a["argmin"] != r["argmin"]]
            deltas[state] = {
                "argmin_flips_vs_current": flips,
                "sphp_advantage_current": cur["sphp_advantage"],
                "sphp_advantage_adopted": adv["sphp_advantage"],
                "policy_current": cur_policy,
                "policy_adopted": adv_policy,
                "policy_pooled_err_current_pct": cur_policy["pooled_abs_err_pct"],
                "policy_pooled_err_adopted_pct": adv_policy["pooled_abs_err_pct"],
                "policy_delta_pp": round(adv_policy["pooled_abs_err_pct"]
                                         - cur_policy["pooled_abs_err_pct"], 2),
            }
        out["crossover_policy_deltas"] = deltas

    OUT_JSON.write_text(json.dumps(out, indent=2, default=str) + "\n")

    # ------------------------------------------------------------------ #
    # markdown summary
    # ------------------------------------------------------------------ #
    md: List[str] = []
    md.append("# Revision-Board like-for-like evaluation of RP-1 / RP-2 / RP-3\n")
    md.append("Script: `analysis/revision_board_eval_20261014.py` (seed "
              f"{SEED}; deterministic — no stochastic steps). Protocol: "
              "leave-one-RTT-tier-out, regime (tier-mean) level, identical data "
              "and identical baseline-term fit recipe in both arms of every A/B. "
              "BW = 100 Mbps, top_k = 10.\n")
    md.append("\n> **Reading the R² column.** R² is reported for completeness but is "
              "*not* the operative metric here: the regime-level target is the 4 "
              "RTT-tier means of TTFT, whose between-tier spread is dominated by the "
              "large, nearly-constant prefill term (hence large |Δ| in the "
              "denominator and often-negative R², exactly as in the published "
              "re-baseline, which carries R² = −2.27 / −62.17 / … for the identical "
              "current form). **MAPE is the metric used for every verdict.** The "
              "current arm reproduces the stored re-baseline LOTO values (MAPE and "
              "R²) bit-for-bit, so the like-for-like comparison is exact.\n")
    md.append("\n## Verdicts\n")
    md.append("| proposal | verdict | wins on 09-18 | wins on 10-13 |")
    md.append("|---|---|---|---|")
    for p in ("RP-1", "RP-2", "RP-3"):
        v = verdicts[p]
        md.append(f"| {p} | **{v['verdict']}** | {v['wins']['old_20260918']} "
                  f"| {v['wins']['new_20261013']} |")
    md.append("")
    for p in ("RP-1", "RP-2", "RP-3"):
        v = verdicts[p]
        md.append(f"\n### {p}\n")
        for ds in ("old_20260918", "new_20261013"):
            md.append(f"\n**{ds}** — LOTO regime MAPE% (R²) and the win check:\n")
            md.append("| regime | current | proposed | Δ pp |")
            md.append("|---|---|---|---|")
            for st in ("cold", "warm"):
                c = out[ds]["states"][st]["loto"]["current"]
                wv = out[ds]["states"][st]["loto"][p]
                md.append(f"| {st} P2 | {c['p2']['regime_mape_pct']} "
                          f"({c['p2']['regime_r2']}) | {wv['p2']['regime_mape_pct']} "
                          f"({wv['p2']['regime_r2']}) | "
                          f"{round(wv['p2']['regime_mape_pct'] - c['p2']['regime_mape_pct'], 2)} |")
                md.append(f"| {st} P2-SPHP | {c['sphp']['regime_mape_pct']} "
                          f"({c['sphp']['regime_r2']}) | {wv['sphp']['regime_mape_pct']} "
                          f"({wv['sphp']['regime_r2']}) | "
                          f"{round(wv['sphp']['regime_mape_pct'] - c['sphp']['regime_mape_pct'], 2)} |")
            n = v["notes"][0 if ds == "old_20260918" else 1]
            md.append(f"\n- targeted Δ: {n['targeted_delta_pp']}  (all strictly "
                      f"lower: {n['targeted_strictly_lower']})")
            md.append(f"- guard Δ: {n['guard_delta_pp']}  (all ≤ {EPS_GUARD_PP} pp: "
                      f"{n['guards_within_eps']})")
            md.append(f"- targeted residual pattern removed: "
                      f"{n['targeted_pattern_removed']}")
            md.append(f"- **win on {ds}: {v['wins'][ds]}**")
            md.append("")
            # targeted residual-pattern detail (the specific pattern the
            # proposal targets), current form vs proposed form
            if p == "RP-1":
                for st in ("cold", "warm"):
                    c = out[ds]["states"][st]["pattern"]["rp1"]
                    mf = c["miss_penalty_fit"]
                    md.append(f"  - {st} miss-penalty fit: Tmiss(T) = "
                              f"{mf['a_ms']} + {mf['g_ms_per_ms']}·rtt "
                              f"(R²={mf['r2_on_rtt']}); realized per-tier "
                              f"{mf['realized_per_tier_ms']} ms")
                    md.append(f"    - miss-branch MAPE: current "
                              f"{c['miss_branch_current']['mape_pct']}% (n="
                              f"{c['miss_branch_current']['n']}) → proposed "
                              f"{c['miss_branch_proposed']['mape_pct']}%; "
                              f"miss-branch resid slope on RTT "
                              f"{c['miss_branch_current']['resid_slope_pct_per_ms_rtt']} → "
                              f"{c['miss_branch_proposed']['resid_slope_pct_per_ms_rtt']} "
                              f"pp/ms")
            elif p == "RP-2":
                for st in ("cold", "warm"):
                    c = out[ds]["states"][st]["pattern"]["rp2"]
                    cf = c["credit_fit"]
                    md.append(f"  - {st} hit-credit C_s: fitted "
                              f"{cf['C_ms_fitted']} ms "
                              f"(implied per-tier {cf['implied_per_tier_C_ms']}); "
                              f"measured vs modeled SPHP speedup: current "
                              f"{c['speedup_current']['measured_pct']}% vs "
                              f"{c['speedup_current']['modeled_pct']}% (gap "
                              f"{c['speedup_current']['gap_pp']} pp) → proposed "
                              f"{c['speedup_proposed']['measured_pct']}% vs "
                              f"{c['speedup_proposed']['modeled_pct']}% (gap "
                              f"{c['speedup_proposed']['gap_pp']} pp)")
            else:  # RP-3
                for st in ("cold", "warm"):
                    c = out[ds]["states"][st]["pattern"]["rp3"]
                    wf = c["wan_fit"]
                    pr = c["p2_residual"]
                    md.append(f"  - {st} WAN fit: Twan_s = {wf['b_ms']} + "
                              f"{wf['g_ms_per_ms']}·rtt (+serialization); "
                              f"measured P2 tier-mean RTT slope = "
                              f"{wf['measured_p2_tier_mean_slope_ms_per_ms']} "
                              f"vs analytic 0.5")
                    md.append(f"    - P2 per-record residual slope on rtt: "
                              f"current {pr['current']['slope_pct_per_ms']} "
                              f"(R²={pr['current']['r2']}) → proposed "
                              f"{pr['rp3']['slope_pct_per_ms']} "
                              f"(R²={pr['rp3']['r2']}) pp/ms")
                    md.append(f"    - per-tier mean resid (ms): current "
                              f"{pr['current']['per_tier_mean_resid_ms']} → "
                              f"proposed {pr['rp3']['per_tier_mean_resid_ms']}")
        if v["deferred_condition"]:
            md.append(f"\n> Deferred condition: {v['deferred_condition']}")
        md.append("")

    if adopted:
        md.append("\n## Adopted-form crossover / policy deltas (analysis-level, 10-13 surface)\n")
        for st, d in out["crossover_policy_deltas"].items():
            a_c, a_a = d["sphp_advantage_current"], d["sphp_advantage_adopted"]
            md.append(f"- **{st}** SPHP advantage: {a_c['min_pct']}–{a_c['max_pct']}% "
                      f"(current) → {a_a['min_pct']}–{a_a['max_pct']}% (adopted); "
                      f"argmin {a_c['argmin_counts']} → {a_a['argmin_counts']}")
            md.append(f"- **{st}** argmin policy pooled error: "
                      f"{d['policy_pooled_err_current_pct']}% → "
                      f"{d['policy_pooled_err_adopted_pct']}% "
                      f"(Δ {d['policy_delta_pp']} pp); "
                      f"argmin flips vs current: {d['argmin_flips_vs_current'] or 'none'}")
            for t in d["policy_adopted"]["per_tier"]:
                md.append(f"  - rtt {t['rtt_ms']}: selected {t['selected']}, "
                          f"pred {t['pred_ms']} ms vs realized {t['realized_ms']} ms "
                          f"({t['abs_err_pct']}%)")
        md.append("\n> Analysis-level only: the production cost model and systems/ "
                  "are untouched by this script; adoption-port is a separate slice.")
    else:
        md.append("\nNo proposal was adopted — no crossover/policy recomputation "
                  "was performed (and adoption-port is a separate slice in any "
                  "case).\n")

    OUT_MD.write_text("\n".join(md) + "\n")
    print(f"wrote {OUT_JSON}")
    print(f"wrote {OUT_MD}")
    print("\nVERDICTS:")
    for p, v in verdicts.items():
        print(f"  {p}: {v['verdict']}  wins={v['wins']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
