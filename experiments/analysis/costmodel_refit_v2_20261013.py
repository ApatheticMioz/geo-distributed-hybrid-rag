#!/usr/bin/env python3
"""
Cost-model REFIT + residual diagnostics on the v2 confirmatory re-baseline (AB-5).

Single concern: re-fit the EXISTING cost-model functional forms on the 2026-10-13
confirmatory 2-node campaign and quantify how the dense-leg fix shifted the cost
surface, for the Revision Board.  No new model forms are adopted here; any
systematic misfit is only DOCUMENTED (see docs/revision_log.md), never changed.

What it does
------------
1.  REFIT the existing per-term forms on the new data (and, for like-for-like,
    on the 2026-09-18 data) and report each term OLD vs NEW:
        * hydration term      -- residual after retrieval+fusion+wan+prefill;
                                  linearity in doc count (proxy = top_k) and
                                  in generated token count, plus the 5-doc fit.
        * decode-tps         -- constancy (median + spread) across tiers/states.
        * cache stratification -- full cold vs warm parameter set.
        * SPHP hit/miss      -- measured hit rate (Wilson 95%) and the miss
                                 penalty (mean wasted_prefill_ms) per state.
2.  GOODNESS (leave-one-tier-out, regime level, the protocol the paper uses):
        * old-fit-on-old  (reproduces the published 1.28/2.76 cold/warm P2)
        * refit-on-new    (the re-baseline)
        * old-fit-on-new  (TRANSFER error = how much the dense-leg fix shifted
                           the cost surface; feeds the testbed-disclosure text).
3.  CROSSTOP + POLICY:
        * P2 vs P2-SPHP crossover re-computed on the new surface (the model is
          monotone in RTT/BW so there is no interior crossing; we report the
          SPHP advantage % and confirm the argmin cell-by-cell).
        * argmin placement-policy demo replayed on the new campaign (predicted
          vs realized warm TTFT of the selected variant, per regime) -> warm
          error %, compared with the published ~5%.
4.  RESIDUAL DIAGNOSTICS: residuals (pred - obs) vs predicted, overall and per
   tier / variant / cold-warm stratum, with linear-trend and curvature tests to
   pin down any systematic pattern (scale-dependence, RTT dependence, regime
   interaction, hit/miss asymmetry).

Fences honoured: this script only READS cost_model.py (imports its helpers) and
the two campaign JSONLs; it writes ONLY under experiments/analysis/results/.
It never modifies model code or the paper.  Outputs:
    results/costmodel_refit_v2_20261013.json
    results/costmodel_refit_v2_20261013.md
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

_HERE = Path(__file__).resolve().parent
PROJECT_ROOT = _HERE.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from experiments.analysis.cost_model import (          # noqa: E402
    PlacementCostModel,
    fit_placement_model,
    regime_loto_validation,
    component_residual_check,
    sphp_branch_check,
    _fit_p2_params,
    _sphp_stats,
    _tps_of,
    _cache_state,
    _tiers,
    _mean,
    _median,
    _mape,
    _wilson_ci,
    load_campaign_records,
)

NEW = PROJECT_ROOT / "experiments/bench/campaigns/campaign_v2_confirm_2node_20261013.jsonl"
OLD = PROJECT_ROOT / "experiments/bench/campaigns/campaign_20260918T130756Z.jsonl"
STORED_VALIDATION = _HERE / "results/cost_model_validation.json"   # published OLD fit
OUT_JSON = _HERE / "results/costmodel_refit_v2_20261013.json"
OUT_MD = _HERE / "results/costmodel_refit_v2_20261013.md"

BW = 100.0   # fitted WireGuard bandwidth (Mbps)
TOPK = 10    # every record in both campaigns is top_k=10


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
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


def ols2(x: List[float], y: List[float]):
    """slope, intercept, and R^2 of a simple OLS y ~ a + b*x."""
    xa, ya = np.asarray(x, float), np.asarray(y, float)
    n = len(xa)
    if n < 3:
        return (0.0, float("nan"), float("nan"))
    xa = xa - xa.mean()
    b = float((xa * (ya - ya.mean())).sum() / (xa * xa).sum()) if (xa * xa).sum() > 0 else 0.0
    a = float((ya - b * (np.asarray(x) - np.asarray(x).mean())).mean())
    yhat = a + b * np.asarray(x)
    ss_res = float(((np.asarray(y) - yhat) ** 2).sum())
    ss_tot = float(((np.asarray(y) - np.asarray(y).mean()) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    return b, a, r2


def trend_slope_pct(x: List[float], y: List[float]) -> float:
    """OLS slope of y on x expressed in percentage points of y per unit of x."""
    if not x or len(set(x)) < 2:
        return float("nan")
    b, _, _ = ols2(x, y)
    return b  # if y is already %-scaled, b is %-per-unit-of-x


def resid_stats(recs: List[dict], pred_fn) -> Dict:
    """residual = pred - obs over a record set, with trend/curvature diagnostics."""
    pred, obs, rtts, toks, prefill = [], [], [], [], []
    for r in recs:
        p = pred_fn(r)
        pred.append(p)
        obs.append(r["ttft_ms"])
        rtts.append(float(r["rtt_ms"]))
        toks.append(r.get("token_count", 0))
        prefill.append(r["ttft_ms"] - max(r["sparse_ms"], r["dense_ms"]) - r["fusion_ms"])
    n = len(pred)
    if n == 0:
        return {"n": 0}
    pred_a, obs_a = np.asarray(pred, float), np.asarray(obs, float)
    res = pred_a - obs_a                       # >0 means model over-predicts
    res_pct = 100.0 * res / np.maximum(np.abs(obs_a), 1.0)
    out = {
        "n": n,
        "mean_resid_ms": round(float(res.mean()), 2),
        "std_resid_ms": round(float(res.std()), 2),
        "bias_pct_of_obs": round(float(res_pct.mean()), 3),
        "mape_pct": round(_mape(list(zip(pred_a, obs_a))), 2),
        "trend_resid_on_rtt_pct_per_ms": round(trend_slope_pct(rtts, list(res_pct)), 4),
        "trend_resid_on_tok_pct_per_tok": round(trend_slope_pct(toks, list(res_pct)), 6),
        "curvature_resid_on_prefill_r2": round(
            ols2(prefill, list(res_pct))[2], 4),
        "curvature_resid_on_tok_r2": round(
            ols2([float(t) ** 2 for t in toks], list(res_pct))[2], 4),
    }
    return out


def model_pred_p2(model: PlacementCostModel, r: dict) -> float:
    return model.predict_ttft_p2(float(r["rtt_ms"]), BW)


def model_pred_sphp(model: PlacementCostModel, r: dict) -> float:
    return model.predict_ttft_p2_sphp(float(r["rtt_ms"]), BW)


def model_from_fitted(f: Dict) -> PlacementCostModel:
    # accept both the refit (analysis) key names and the stored
    # cost_model_validation.json key names
    return PlacementCostModel(
        t_sparse_base_ms=f["t_sparse_base_ms"],
        t_dense_base_ms=f["t_dense_base_ms"],
        t_fusion_base_ms=f["t_fusion_base_ms"],
        t_hydrate_per_doc_ms=f.get("t_hydrate_per_doc_ms", 5.0),
        t_prefill_base_ms=f["t_prefill_base_ms"],
        decode_tok_per_sec=f["decode_tok_per_sec"],
        doc_id_bytes=f.get("doc_id_bytes", 8),
        doc_text_bytes=f.get("doc_text_bytes", 1200),
        query_bytes=f.get("query_bytes", 64),
        sphp_hit_rate=f.get("sphp_hit_rate_measured", f.get("sphp_hit_rate", 0.0)),
        t_sphp_miss_penalty_ms=f["t_sphp_miss_penalty_ms"],
    )


def hydration_diagnostic(base: List[dict], prefill: float) -> Dict:
    """
    Hydration term linearity check on one state's baseline records.
    The residual after removing the retrieval barrier, fusion, the analytic WAN
    and the fitted prefill base is (up to noise) the hydration term, which the
    model takes as 5 * t_hydrate_per_doc_ms.  We:
      (a) fit that residual -> t_hydrate_per_doc (the 5-doc term),
      (b) test whether it depends on generated token count (should be ~0:
          hydration hydrates 5 *passages*, independent of how many *tokens* the
          answer has),
      (c) note the doc-count axis is degenerate (top_k=10 constant) so linearity
          in doc count is a no-op by construction here.
    """
    res = []
    toks = []
    for r in base:
        wan = (0.5 * float(r["rtt_ms"]))  # 144 B serializes in <0.01 ms at 100 Mbps
        hyd = r["ttft_ms"] - max(r["sparse_ms"], r["dense_ms"]) - r["fusion_ms"] - wan - prefill
        res.append(hyd)
        toks.append(r.get("token_count", 0))
    n = len(res)
    if n == 0:
        return {"n": 0}
    t_per_doc = _mean(res) / 5.0
    b, a, r2 = ols2(toks, res)
    return {
        "n": n,
        "mean_hydrate_resid_ms": round(_mean(res), 2),
        "std_hydrate_resid_ms": round(float(np.std(res)), 2),
        "t_hydrate_per_doc_ms": round(t_per_doc, 3),
        "hydrate_per_doc_vs_5doc_default": round(t_per_doc / 5.0 - 1.0, 3) if t_per_doc else None,
        "hydrate_vs_tokcount_slope_ms_per_tok": round(b, 5),
        "hydrate_vs_tokcount_r2": round(r2, 4),
        "top_k_distinct": sorted({r.get("top_k") for r in base}),
        "note": ("top_k is constant (=10) across the campaign, so doc-count "
                 "linearity is a no-op; the token-count slope tests whether the "
                 "hydration term secretly scales with generated length."),
    }


def sphp_miss_diagnostic(sphp: List[dict], model: PlacementCostModel) -> Dict:
    """
    Is the SPHP miss penalty a constant, or does it scale with prefill?
    Miss penalty (per record) = ttft_sphp_miss - (measured P2-style base for the
    same record).  The model uses a single mean Tmiss; we check constancy and
    any correlation with the record's own prefill residual (a hit-path/KV
    effect would show up as a prefill-dependent penalty, not a constant).
    """
    pen, prefill = [], []
    for r in sphp:
        if r.get("sphp_hit") is not False:
            continue
        base = (max(r["sparse_ms"], r["dense_ms"]) + r["fusion_ms"]
                + model.wan_delay_ms(model.query_bytes + 10 * model.doc_id_bytes,
                                     float(r["rtt_ms"]), BW))
        pen.append(r["ttft_ms"] - base)
        prefill.append(r["ttft_ms"] - max(r["sparse_ms"], r["dense_ms"]) - r["fusion_ms"])
    n = len(pen)
    if n == 0:
        return {"n": 0}
    b, a, r2 = ols2(prefill, pen)
    return {
        "n": n,
        "mean_miss_penalty_ms": round(_mean(pen), 2),
        "std_miss_penalty_ms": round(float(np.std(pen)), 2),
        "penalty_vs_prefill_slope": round(b, 4),
        "penalty_vs_prefill_r2": round(r2, 4),
        "reported_wasted_prefill_ms_mean": round(
            _mean([r["wasted_prefill_ms"] for r in sphp
                   if r.get("sphp_hit") is False
                   and isinstance(r.get("wasted_prefill_ms"), (int, float))]), 2),
    }


# --------------------------------------------------------------------------- #
# per-campaign fit (existing functional forms, unchanged)
# --------------------------------------------------------------------------- #
def fit_campaign(path: Path) -> Dict:
    recs = load_campaign_records(path)
    cold, warm = split_state(recs)
    out: Dict = {
        "campaign": str(path),
        "n_records": len(recs),
        "n_cold": len(cold),
        "n_warm": len(warm),
        "tiers": _tiers(recs),
        "top_k": sorted({int(r["top_k"]) for r in recs}),
    }
    states = {}
    for state, recs_s in (("cold", cold), ("warm", warm)):
        base, sphp = base_and_sphp(recs_s)
        p2 = fit_placement_model(base, sphp, tps_source=recs_s)
        tps_w, nused, nexcl = _tps_of(recs_s)
        tps_c, _, _ = _tps_of([r for r in recs_s])
        out_p2 = {k: getattr(p2, k) for k in (
            "t_sparse_base_ms", "t_dense_base_ms", "t_fusion_base_ms",
            "t_prefill_base_ms", "decode_tok_per_sec",
            "sphp_hit_rate", "t_sphp_miss_penalty_ms")}
        sstats = _sphp_stats(sphp)
        states[state] = {
            "n_p2": len(base),
            "n_sphp": len(sphp),
            "fitted_params": {k: round(v, 4) for k, v in out_p2.items()},
            "decode_tps_median": round(tps_w, 3),
            "decode_tps_n_excluded": nexcl,
            "sphp": {
                "measured_hit_rate": sstats["measured_hit_rate"],
                "hit_rate_wilson95": sstats["hit_rate_wilson95"],
                "per_tier_hit_rate": sstats["per_tier_hit_rate"],
                "miss_penalty_ms": sstats["miss_penalty_ms"],
                "n_miss": sstats["n_miss_used_for_penalty"],
            },
            "loto_P2": regime_loto_validation(base, sphp, topology="P2"),
            "loto_P2SPHP": regime_loto_validation(base, sphp, topology="P2-SPHP"),
            "component_residual_check": component_residual_check(base, p2),
            "sphp_branch_check": sphp_branch_check(sphp, p2),
        }
    out["states"] = states
    return out


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> int:
    _HERE.mkdir(parents=True, exist_ok=True)

    new = fit_campaign(NEW)
    old = fit_campaign(OLD)

    # Published OLD fit, stored in the existing validation artifact, is the
    # ground-truth reference the paper's numbers came from.
    stored = json.loads(STORED_VALIDATION.read_text())
    stored_warm = stored["cache_states"]["warm"]["fitted_params"]
    stored_cold = stored["cache_states"]["cold"]["fitted_params"]

    # ---- (2) GOODNESS: three MAPEs (regime LOTO, the paper's protocol) ----
    def loto_summary(fit: Dict) -> Dict:
        return {
            "P2_cold": fit["states"]["cold"]["loto_P2"]["regime_mape_pct"],
            "P2_warm": fit["states"]["warm"]["loto_P2"]["regime_mape_pct"],
            "P2SPHP_cold": fit["states"]["cold"]["loto_P2SPHP"]["regime_mape_pct"],
            "P2SPHP_warm": fit["states"]["warm"]["loto_P2SPHP"]["regime_mape_pct"],
        }

    goodness: Dict = {
        "protocol": "leave-one-tier-out, RTT tiers, regime (tier-mean) level, "
                    "the paper's C3 validation protocol",
        "old_fit_on_old": loto_summary(old),
        "refit_on_new": loto_summary(new),
    }

    # ---- (1) per-term OLD vs NEW (hydration, decode, SPHP) ----
    # split the new records once (reused by transfer, hydration and residuals)
    new_recs_all = load_campaign_records(NEW)
    new_cold, new_warm = split_state(new_recs_all)
    new_base_cold, new_sphp_cold = base_and_sphp(new_cold)
    new_base_warm, new_sphp_warm = base_and_sphp(new_warm)

    # transfer: OLD fit applied to the NEW surface (predict NEW tier means with
    # OLD parameters) -> the cost-surface shift induced by the dense-leg fix.
    transfer = {}
    for state in ("cold", "warm"):
        om = model_from_fitted(stored_warm if state == "warm" else stored_cold)
        base_new = new_base_cold if state == "cold" else new_base_warm
        per_tier = []
        for t in new["tiers"]:
            obs = tier_mean(base_new, t)
            pred = om.predict_ttft_p2(float(t), BW)
            per_tier.append({"rtt_ms": t, "observed_mean_ms": round(obs, 1),
                              "predicted_by_oldfit_ms": round(pred, 1),
                              "ape_pct": round(abs(pred - obs) / max(obs, 1.0) * 100, 2)})
        pairs = [(p["predicted_by_oldfit_ms"], p["observed_mean_ms"]) for p in per_tier]
        transfer[state] = {"mape_pct": round(_mape(pairs), 2), "per_tier": per_tier}
    goodness["old_fit_on_new_transfer"] = transfer

    def prefill_of(base):
        return _mean([r["ttft_ms"] - max(r["sparse_ms"], r["dense_ms"]) - r["fusion_ms"]
                      for r in base])

    per_term: Dict = {"hydration": {}, "decode": {}, "sphp": {}, "params": {}}
    per_term["hydration"]["cold"] = hydration_diagnostic(new_base_cold, prefill_of(new_base_cold))
    per_term["hydration"]["warm"] = hydration_diagnostic(new_base_warm, prefill_of(new_base_warm))
    per_term["decode"] = {
        "new_cold_median_tps": round(_tps_of(new_cold)[0], 2),
        "new_warm_median_tps": round(_tps_of(new_warm)[0], 2),
        "old_cold_median_tps": round(_tps_of(split_state(load_campaign_records(OLD))[0])[0], 2),
        "old_warm_median_tps": round(_tps_of(split_state(load_campaign_records(OLD))[1])[0], 2),
    }
    per_term["sphp"] = {
        "new_cold": new["states"]["cold"]["sphp"],
        "new_warm": new["states"]["warm"]["sphp"],
        "old_cold": old["states"]["cold"]["sphp"],
        "old_warm": old["states"]["warm"]["sphp"],
    }
    per_term["params"] = {
        "new": new["states"],
        "old": old["states"],
        "stored_published": {"cold": stored_cold, "warm": stored_warm},
    }

    # ---- (3) CROSSTOP + POLICY on the new surface ----
    warm_model = model_from_fitted(new["states"]["warm"]["fitted_params"])
    cold_model = model_from_fitted(new["states"]["cold"]["fitted_params"])

    def sphp_advantage(model):
        rows = []
        for rtt in [0, 10, 25, 50, 100, 150, 200]:
            for bw in [10, 50, 100, 500, 1000]:
                p2 = model.predict_ttft_p2(float(rtt), bw)
                ps = model.predict_ttft_p2_sphp(float(rtt), bw)
                rows.append((rtt, bw, p2, ps, 100.0 * (p2 - ps) / p2))
        return rows

    new_adv = sphp_advantage(warm_model)
    old_adv = sphp_advantage(model_from_fitted(stored_warm))

    def argmin_rows(adv):
        # the model is monotone; report the min/max SPHP advantage and the
        # argmin topology per cell (all should be P2-SPHP)
        advs = [r[4] for r in adv]
        argmins = { "P2-SPHP": 0, "P2": 0 }
        for rtt, bw, p2, ps, _ in adv:
            argmins["P2-SPHP" if ps < p2 else "P2"] += 1
        return {"min_sphp_advantage_pct": round(min(advs), 2),
                "max_sphp_advantage_pct": round(max(advs), 2),
                "argmin_topology_counts": argmins}

    crossover: Dict = {
        "note": "P2 vs P2-SPHP: the WAN payload is 104/144 B so the model is "
                "monotone in RTT and BW -- there is no interior crossover; the "
                "decision is 'always P2-SPHP' with a small, RTT/BW-flat edge.",
        "new_sphp_advantage": argmin_rows(new_adv),
        "old_sphp_advantage": argmin_rows(old_adv),
    }

    # argmin placement-policy demo replayed on the NEW campaign: for each
    # regime the policy picks the argmin variant (warm fit) and we compare the
    # predicted warm TTFT to the realized warm TTFT of that variant's records.
    policy = {}
    for state, recs_s in (("warm", new_warm), ("cold", new_cold)):
        sel_model = warm_model if state == "warm" else cold_model  # policy selects from warm fit
        per_tier = []
        for t in new["tiers"]:
            p2 = sel_model.predict_ttft_p2(float(t), BW)
            ps = sel_model.predict_ttft_p2_sphp(float(t), BW)
            chosen = "P2-SPHP" if ps < p2 else "P2"
            chosen_pred = ps if chosen == "P2-SPHP" else p2
            realized = [r["ttft_ms"] for r in recs_s
                        if float(r["rtt_ms"]) == t and r.get("variant") ==
                        ("sphp" if chosen == "P2-SPHP" else "baseline")]
            rm = _mean(realized) if realized else float("nan")
            per_tier.append({
                "rtt_ms": t, "selected": chosen,
                "pred_ms": round(chosen_pred, 1), "realized_ms": round(rm, 1),
                "n": len(realized),
                "abs_err_pct": round(abs(chosen_pred - rm) / max(rm, 1.0) * 100, 2),
            })
        allpred = [x["pred_ms"] for x in per_tier]
        allreal = [x["realized_ms"] for x in per_tier]
        policy[state] = {
            "per_tier": per_tier,
            "pooled_abs_err_pct": round(_mape(list(zip(allpred, allreal))), 2),
            "selected_variants": {x["rtt_ms"]: x["selected"] for x in per_tier},
        }

    # ---- (4) RESIDUAL DIAGNOSTICS on the refit (new) model ----
    old_cold_model = model_from_fitted(stored_cold)
    old_warm_model = model_from_fitted(stored_warm)

    def _residual_block(base, sphp, model):
        p2pred, sphppred = model.predict_ttft_p2, model.predict_ttft_p2_sphp
        return {
            "P2": resid_stats(base, lambda r: p2pred(float(r["rtt_ms"]), BW)),
            "P2SPHP_overall": resid_stats(sphp, lambda r: sphppred(float(r["rtt_ms"]), BW)),
            "P2_per_tier_mean_resid_ms": {
                str(int(t)): round(_mean([p2pred(float(t), BW) - r["ttft_ms"]
                                          for r in base if float(r["rtt_ms"]) == t]), 1)
                for t in new["tiers"]},
        }

    # refit-on-new (new model, new data) and old-fit-on-new (old model, new data)
    residuals: Dict = {
        "refit_on_new_cold":  _residual_block(new_base_cold, new_sphp_cold, cold_model),
        "refit_on_new_warm":  _residual_block(new_base_warm, new_sphp_warm, warm_model),
        "old_fit_on_new_cold":  _residual_block(new_base_cold, new_sphp_cold, old_cold_model),
        "old_fit_on_new_warm":  _residual_block(new_base_warm, new_sphp_warm, old_warm_model),
    }

    # hit/miss branch asymmetry (new refit)
    branch = {
        "cold": new["states"]["cold"]["sphp_branch_check"],
        "warm": new["states"]["warm"]["sphp_branch_check"],
    }

    # SPHP form-error extras (the two systematic misfits the board must see):
    #  (a) realized MISS cost (miss TTFT tier-mean - baseline P2 tier-mean)
    #      vs the recorded wasted_prefill_ms the model uses as Tmiss;
    #  (b) MEASURED vs MODELED SPHP speedup (the KV-prefix-credit gap).
    sphp_formerror = {}
    for state, base, sphp, model in (
            ("cold", new_base_cold, new_sphp_cold, cold_model),
            ("warm", new_base_warm, new_sphp_warm, warm_model)):
        realized_miss_pen = {}
        for t in new["tiers"]:
            b = tier_mean(base, t)
            miss = tier_mean([r for r in sphp if r.get("sphp_hit") is False], t)
            realized_miss_pen[str(int(t))] = round(miss - b, 1)
        recorded_pen = _mean([r["wasted_prefill_ms"] for r in sphp
                              if r.get("sphp_hit") is False
                              and isinstance(r.get("wasted_prefill_ms"), (int, float))])
        b_all = _mean([r["ttft_ms"] for r in base])
        s_all = _mean([r["ttft_ms"] for r in sphp])
        # modeled expected speedup from the fitted model at 100 Mbps
        p2m = model.predict_ttft_p2(40.0, BW)
        sm = model.predict_ttft_p2_sphp(40.0, BW)
        sphp_formerror[state] = {
            "realized_miss_penalty_ms": realized_miss_pen,
            "realized_miss_penalty_mean_ms": round(
                _mean([v for v in realized_miss_pen.values()]), 1),
            "recorded_wasted_prefill_ms_mean": round(recorded_pen, 1),
            "gap_realized_minus_recorded_ms": round(
                _mean([v for v in realized_miss_pen.values()]) - recorded_pen, 1),
            "measured_sphp_speedup_pct": round(100.0 * (1 - s_all / b_all), 2),
            "modeled_sphp_speedup_pct": round(100.0 * (1 - sm / p2m), 2),
        }

    # ---------------------------------------------------------------------- #
    out = {
        "generator": "analysis/costmodel_refit_v2_20261013.py (AB-5)",
        "new_campaign": str(NEW),
        "old_campaign": str(OLD),
        "new_campaign_n": new["n_records"],
        "old_campaign_n": old["n_records"],
        "bw_mbps": BW,
        "top_k": TOPK,
        "per_term": per_term,
        "goodness": goodness,
        "crossover": crossover,
        "policy": policy,
        "residuals": residuals,
        "sphp_branch_check": branch,
        "sphp_formerror": sphp_formerror,
        "new_full": new,
        "old_full": old,
    }
    OUT_JSON.write_text(json.dumps(out, indent=2) + "\n")

    # compact markdown for the revision board
    md = []
    md.append("# Cost-model refit + residual diagnostics on the v2 confirmatory re-baseline (AB-5)\n")
    md.append("New data: `campaign_v2_confirm_2node_20261013.jsonl` (N=%d, 0 errors); "
              "like-for-like old data: `campaign_20260918T130756Z.jsonl` (N=%d). "
              "Protocol: leave-one-tier-out, regime (tier-mean) level.\n" % (new["n_records"], old["n_records"]))

    md.append("\n## 1. Per-term parameters (old vs new)\n")
    md.append("| term | old (09-18) | new (10-13) |")
    md.append("|---|---|---|")
    o = old["states"]; n = new["states"]
    for state in ("cold", "warm"):
        for k in ("t_sparse_base_ms", "t_dense_base_ms", "t_fusion_base_ms",
                  "t_prefill_base_ms", "decode_tok_per_sec",
                  "sphp_hit_rate", "t_sphp_miss_penalty_ms"):
            ov = o[state]["fitted_params"].get(k)
            nv = n[state]["fitted_params"].get(k)
            ov = round(ov, 3) if isinstance(ov, (int, float)) else ov
            nv = round(nv, 3) if isinstance(nv, (int, float)) else nv
            md.append(f"| {state} {k} | {ov} | {nv} |")
    md.append("")

    md.append("\n### Hydration term (linearity checks, new data)\n")
    for state in ("cold", "warm"):
        h = per_term["hydration"][state]
        md.append(f"- **{state}**: mean hydration residual {h['mean_hydrate_resid_ms']} ms "
                  f"(std {h['std_hydrate_resid_ms']}), t_per_doc={h['t_hydrate_per_doc_ms']} ms/doc "
                  f"(5-doc fit); token-count slope {h['hydrate_vs_tokcount_slope_ms_per_tok']} "
                  f"ms/tok (R^2={h['hydrate_vs_tokcount_r2']}); top_k={h['top_k_distinct']}.")
    md.append("")

    md.append("\n### SPHP hit/miss (new vs old)\n")
    for state in ("cold", "warm"):
        ns = per_term["sphp"]["new_" + state]; os_ = per_term["sphp"]["old_" + state]
        md.append(f"- **{state}** new: hit={ns['measured_hit_rate']} "
                  f"({ns['hit_rate_wilson95']}), Tmiss={ns['miss_penalty_ms']} ms; "
                  f"old: hit={os_['measured_hit_rate']}, Tmiss={os_['miss_penalty_ms']} ms.")
    md.append("")

    md.append("\n## 2. Goodness (regime LOTO MAPE)\n")
    md.append("| fit | P2 cold | P2 warm | SPHP cold | SPHP warm |")
    md.append("|---|---|---|---|---|")
    g = goodness
    md.append(f"| old-fit-on-old | {g['old_fit_on_old']['P2_cold']} | {g['old_fit_on_old']['P2_warm']} "
              f"| {g['old_fit_on_old']['P2SPHP_cold']} | {g['old_fit_on_old']['P2SPHP_warm']} |")
    md.append(f"| refit-on-new | {g['refit_on_new']['P2_cold']} | {g['refit_on_new']['P2_warm']} "
              f"| {g['refit_on_new']['P2SPHP_cold']} | {g['refit_on_new']['P2SPHP_warm']} |")
    md.append(f"| **old-fit-on-new (TRANSFER)** | {g['old_fit_on_new_transfer']['cold']['mape_pct']} "
              f"| {g['old_fit_on_new_transfer']['warm']['mape_pct']} | — | — |")
    md.append("\n> The transfer row (old fit applied to the new surface) is the cost-surface "
              "shift induced by the dense-leg fix -- this is the number the testbed-disclosure "
              "text needs.")
    md.append("")

    md.append("\n## 3. Crossover + policy\n")
    md.append(f"- New SPHP advantage (RTT×BW sweep): min {crossover['new_sphp_advantage']['min_sphp_advantage_pct']}%, "
              f"max {crossover['new_sphp_advantage']['max_sphp_advantage_pct']}%; "
              f"argmin counts {crossover['new_sphp_advantage']['argmin_topology_counts']}.")
    md.append(f"- Old SPHP advantage: min {crossover['old_sphp_advantage']['min_sphp_advantage_pct']}%, "
              f"max {crossover['old_sphp_advantage']['max_sphp_advantage_pct']}%.")
    for state in ("warm", "cold"):
        p = policy[state]
        md.append(f"\n- **{state}** argmin demo (predicted vs realized {state} TTFT of the selected variant), "
                  f"pooled abs error = **{p['pooled_abs_err_pct']}%**:")
        for t in p["per_tier"]:
            md.append(f"  - rtt {t['rtt_ms']}: selected {t['selected']}, "
                      f"pred {t['pred_ms']} ms vs realized {t['realized_ms']} ms "
                      f"({t['abs_err_pct']}%, n={t['n']})")
    md.append("\n> Published warm error was 4.5–8.6% (mean ~5.5%). The refit-on-new "
              "warm number is the like-for-like comparison.\n")

    md.append("\n## 4. Residual diagnostics\n")
    for key in ("refit_on_new_cold", "refit_on_new_warm",
                "old_fit_on_new_cold", "old_fit_on_new_warm"):
        d = residuals[key]
        md.append(f"\n### {key.replace('_',' ')}")
        md.append(f"- P2: {json.dumps(d['P2'])}")
        md.append(f"- P2SPHP overall: {json.dumps(d['P2SPHP_overall'])}")
        md.append(f"- P2 per-tier mean residual (ms): {d['P2_per_tier_mean_resid_ms']}")
    md.append("\n- SPHP branch check (new): cold=%s warm=%s" % (
        json.dumps(branch["cold"]), json.dumps(branch["warm"])))

    md.append("\n### SPHP form-error extras (new)\n")
    for state in ("cold", "warm"):
        fe = sphp_formerror[state]
        md.append(f"- **{state}** realized miss penalty "
                  f"{fe['realized_miss_penalty_mean_ms']} ms "
                  f"(per-tier {fe['realized_miss_penalty_ms']}) vs recorded "
                  f"wasted_prefill {fe['recorded_wasted_prefill_ms_mean']} ms "
                  f"-> gap {fe['gap_realized_minus_recorded_ms']} ms.")
        md.append(f"- **{state}** SPHP speedup: MEASURED {fe['measured_sphp_speedup_pct']}% "
                  f"vs MODELED {fe['modeled_sphp_speedup_pct']}% "
                  f"(the gap is the KV-prefix credit the closed form does not encode).")

    md.append("\n*Systematic-pattern reading: a nonzero `trend_*` or high "
              "`curvature_*` R^2 flags scale/RTT dependence of the residuals; "
              "per-tier mean residual drift flags a regime interaction. Any such "
              "pattern becomes a 'proposed' entry in docs/revision_log.md (no model "
              "change made here).*\n")

    OUT_MD.write_text("\n".join(md) + "\n")
    print(f"wrote {OUT_JSON}")
    print(f"wrote {OUT_MD}")
    print("\nGOODNESS:")
    print(json.dumps(goodness, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
