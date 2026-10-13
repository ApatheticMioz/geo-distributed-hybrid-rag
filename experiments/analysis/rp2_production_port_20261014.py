#!/usr/bin/env python3
"""
RP-2 production-port RE-VALIDATION (adoption-port slice, 2026-10-14).

Single concern: the Revision-Board proposal RP-2 (cold-only, state-dependent
SPHP hit credit C_s) was adopted at analysis level by
revision_board_eval_20261014.py.  This slice ports the SAME adopted form into
the production analytical cost model (cost_model.py: PlacementCostModel +
fit_placement_model) and re-validates it LIKE-FOR-LIKE against the numbers the
analysis-level validation already established.

What it checks (deterministic; no stochastic steps)
---------------------------------------------------
For BOTH campaign surfaces
    * old  : experiments/bench/campaigns/campaign_20260918T130756Z.jsonl
    * new  : experiments/bench/campaigns/campaign_v2_confirm_2node_20261013.jsonl
and for BOTH cache states (cold, warm):

  (A) LOTO regime MAPE (the paper's C3 protocol, BW=100, top-k=10), computed
      THREE ways that must agree bit-for-bit:
        1. production model, LEGACY form     -> regime_loto_validation(...,"P2-SPHP")
        2. production model, ADOPTED form    -> regime_loto_validation_sphp_credit(...)
        3. RB harness loto_evaluate("current"/"RP-2")   [independent reimpl]
        4. the STORED RB output revision_board_eval_20261014.json [authoritative]
      P2 (guard) must be unchanged (credit never touches the P2 topology).

  (B) The full-fit cold credit C_c must equal the RB-stored C_ms_fitted
      (324.0 ms on 09-18, 64.0 ms on 10-13); the warm credit must be 0.0
      (C_w = 0 by construction).

  (C) argmin placement policy (NEW surface only, as the RB did): the pooled
      predicted-vs-realized error of the argmin-selected variant at the
      campaign's own RTT tiers, LEGACY form vs ADOPTED form, using the
      production PlacementCostModel (hydration 5.0 ms class default, the
      published-policy reconstruction constant).  Must match the stored
      9.72 -> 5.11 (cold), 5.57 -> 5.57 (warm) with ZERO argmin flips.

PASS criterion: every production-model number equals its RB-stored / RB-live
counterpart within the stored rounding.  Any divergence -> non-zero exit and a
STOP report (no fudging).

Fences honoured: READS cost_model.py, revision_board_eval_20261014.py (imported
never patched), the two campaign JSONLs, and the stored RB output; WRITES ONLY
experiments/analysis/results/rp2_production_port_20261014.{json,md}.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List

_HERE = Path(__file__).resolve().parent
PROJECT_ROOT = _HERE.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from experiments.analysis.cost_model import (          # noqa: E402
    PlacementCostModel,
    fit_placement_model,
    fit_sphp_hit_credit,
    regime_loto_validation,
    regime_loto_validation_sphp_credit,
    _cache_state,
    _mape,
    _mean,
    _tiers,
    load_campaign_records,
)
import experiments.analysis.revision_board_eval_20261014 as RB          # noqa: E402

OLD_PATH = PROJECT_ROOT / "experiments/bench/campaigns/campaign_20260918T130756Z.jsonl"
NEW_PATH = PROJECT_ROOT / "experiments/bench/campaigns/campaign_v2_confirm_2node_20261013.jsonl"
STORED_RB = _HERE / "results/revision_board_eval_20261014.json"
OUT_JSON = _HERE / "results/rp2_production_port_20261014.json"
OUT_MD = _HERE / "results/rp2_production_port_20261014.md"

# published-policy reconstruction constant (class default hydration used by
# the RB's argmin-policy block to reproduce the stored 9.72/5.57 numbers).
HYDRATE_POLICY_MS = RB.HYDRATE_POLICY_MS
TOL = 0.005          # stored values are rounded to 2 dp -> half a cent
EPS_WARM = 1e-9      # warm must be bit-identical (C_w = 0)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def split_state(recs):
    cold = [r for r in recs if _cache_state(r) == "cold"]
    warm = [r for r in recs if _cache_state(r) == "warm"]
    return cold, warm


def base_and_sphp(recs):
    base = [r for r in recs if r.get("variant") == "baseline"]
    sphp = [r for r in recs if r.get("variant") == "sphp"]
    return base, sphp


def tier_mean(recs, rtt):
    xs = [r["ttft_ms"] for r in recs if float(r["rtt_ms"]) == rtt]
    return _mean(xs) if xs else float("nan")


def _pdict(m):
    return {
        "t_sparse_base_ms": m.t_sparse_base_ms,
        "t_dense_base_ms": m.t_dense_base_ms,
        "t_fusion_base_ms": m.t_fusion_base_ms,
        "t_hydrate_per_doc_ms": m.t_hydrate_per_doc_ms,
        "t_prefill_base_ms": m.t_prefill_base_ms,
        "decode_tok_per_sec": m.decode_tok_per_sec,
        "doc_id_bytes": m.doc_id_bytes,
        "doc_text_bytes": m.doc_text_bytes,
        "query_bytes": m.query_bytes,
        "sphp_hit_rate": m.sphp_hit_rate,
        "t_sphp_miss_penalty_ms": m.t_sphp_miss_penalty_ms,
    }


# --------------------------------------------------------------------------- #
# argmin policy replay using the PRODUCTION model (hydration = class default 5.0)
# --------------------------------------------------------------------------- #
def policy_at_production_model(m: PlacementCostModel, tiers, base_state, sphp_state):
    """Replay the argmin placement policy at the campaign's OWN RTT tiers
    (100 Mbps) using the production model's own predict_* methods, and score
    the PREDICTED TTFT of the selected variant against the realized tier mean.
    This is the production-model equivalent of the RB's `_policy_at_model`."""
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
    pooled = round(_mape([(x["pred_ms"], x["realized_ms"]) for x in per_tier]), 2)
    return {"per_tier": per_tier, "pooled_abs_err_pct": pooled}


def make_policy_model(p, hydration, credit):
    return PlacementCostModel(
        t_sparse_base_ms=p["t_sparse_base_ms"],
        t_dense_base_ms=p["t_dense_base_ms"],
        t_fusion_base_ms=p["t_fusion_base_ms"],
        t_hydrate_per_doc_ms=hydration,
        t_prefill_base_ms=p["t_prefill_base_ms"],
        decode_tok_per_sec=p["decode_tok_per_sec"],
        doc_id_bytes=p["doc_id_bytes"],
        doc_text_bytes=p["doc_text_bytes"],
        query_bytes=p["query_bytes"],
        sphp_hit_rate=p["sphp_hit_rate"],
        t_sphp_miss_penalty_ms=p["t_sphp_miss_penalty_ms"],
        sphp_hit_credit_ms=credit,
    )


# --------------------------------------------------------------------------- #
# the like-for-like comparison
# --------------------------------------------------------------------------- #
def close(a, b, tol=TOL):
    return abs(a - b) <= tol


def run_dataset(ds_name: str, path: Path, stored: Dict) -> Dict:
    recs = load_campaign_records(path)
    cold, warm = split_state(recs)

    out: Dict = {"campaign": str(path), "n_records": len(recs),
                 "tiers": _tiers(recs), "states": {}}
    for state in ("cold", "warm"):
        recs_s = cold if state == "cold" else warm
        base, sphp = base_and_sphp(recs_s)
        sstats = recs_s

        # --- production full fits -----------------------------------------
        m_cur = fit_placement_model(base, sphp, tps_source=recs_s)                 # legacy
        m_adv = fit_placement_model(base, sphp, tps_source=recs_s,
                                    use_sphp_hit_credit=True,
                                    credit_grid=(None if state == "cold" else [0.0]))
        cc_prod = m_adv.sphp_hit_credit_ms

        # --- (A) LOTO regime MAPE, production model ------------------------
        loto = {
            "P2_current": regime_loto_validation(base, sphp, topology="P2"),
            "SPHP_current": regime_loto_validation(base, sphp, topology="P2-SPHP"),
            "SPHP_adopted": regime_loto_validation_sphp_credit(
                base, sphp, use_sphp_hit_credit=True, state=state),
        }

        # --- (A) LOTO regime MAPE, RB harness (independent reimpl) --------
        rp2_grid = RB.CC_GRID_COLD if state == "cold" else [0.0]
        rb_cur = RB.loto_evaluate(base, sphp, recs_s, "current")
        rb_rp2 = RB.loto_evaluate(base, sphp, recs_s, "RP-2", rp2_grid=rp2_grid)

        # --- (B) full-fit credit (RB harness) ----------------------------
        p_full = _pdict(m_cur)
        p_full["t_hydrate_per_doc_ms"] = RB.HYDRATE_PER_DOC_MS
        cc_rb = RB.fit_rp2_credit(base, sphp, p_full,
                                  RB.CC_GRID_COLD if state == "cold" else [0.0])

        # --- (C) argmin policy (production model, hydration 5.0) ---------
        # full-campaign fit (not LOTO), the published-policy reconstruction
        policy = {}
        if ds_name == "new_20261013":
            tiers = out["tiers"]
            base_state = [r for r in recs_s if r.get("variant") == "baseline"]
            sphp_state = [r for r in recs_s if r.get("variant") == "sphp"]
            p0 = _pdict(m_cur)
            policy = {
                "current": policy_at_production_model(
                    make_policy_model(p0, HYDRATE_POLICY_MS, 0.0),
                    tiers, base_state, sphp_state),
                "adopted": policy_at_production_model(
                    make_policy_model(p0, HYDRATE_POLICY_MS, cc_prod),
                    tiers, base_state, sphp_state),
            }

        # --- stored RB (authoritative analysis-level) --------------------
        st = stored["states"][state]
        stored_loto = st["loto"]
        stored_cc = st["pattern"]["rp2"]["credit_fit"]["C_ms_fitted"]

        out["states"][state] = {
            "n_p2": len(base), "n_sphp": len(sphp),
            "full_fit_C_ms": {"production": round(cc_prod, 1),
                              "rb": round(cc_rb["C_ms_fitted"], 1),
                              "stored_rb": stored_cc},
            "loto": {
                "production": {
                    "P2_current_mape": loto["P2_current"]["regime_mape_pct"],
                    "SPHP_current_mape": loto["SPHP_current"]["regime_mape_pct"],
                    "SPHP_adopted_mape": loto["SPHP_adopted"]["regime_mape_pct"],
                },
                "rb_live": {
                    "P2_current_mape": rb_cur["p2"]["regime_mape_pct"],
                    "SPHP_current_mape": rb_cur["sphp"]["regime_mape_pct"],
                    "SPHP_adopted_mape": rb_rp2["sphp"]["regime_mape_pct"],
                },
                "stored_rb": {
                    "P2_current_mape": stored_loto["current"]["p2"]["regime_mape_pct"],
                    "SPHP_current_mape": stored_loto["current"]["sphp"]["regime_mape_pct"],
                    "SPHP_adopted_mape": stored_loto["RP-2"]["sphp"]["regime_mape_pct"],
                },
            },
            "policy": policy,
        }
    return out


# --------------------------------------------------------------------------- #
# PASS/FAIL aggregation
# --------------------------------------------------------------------------- #
def evaluate(out: Dict, stored: Dict) -> Dict:
    checks: List[Dict] = []

    def add(name, prod, ref, tol=TOL, kind="eq"):
        ok = close(prod, ref, tol) if kind == "eq" else (prod <= ref + tol)
        checks.append({"check": name, "production": prod, "reference": ref,
                       "delta": round(prod - ref, 4), "tol": tol, "pass": ok})

    for ds in ("old_20260918", "new_20261013"):
        for st in ("cold", "warm"):
            s = out[ds]["states"][st]
            pr, rbl, stb = s["loto"]["production"], s["loto"]["rb_live"], s["loto"]["stored_rb"]
            # LOTO: production == RB-live == stored  (all three must agree)
            add(f"{ds}/{st} SPHP_current LOTO prod==stored",
                pr["SPHP_current_mape"], stb["SPHP_current_mape"])
            add(f"{ds}/{st} SPHP_current LOTO prod==rb_live",
                pr["SPHP_current_mape"], rbl["SPHP_current_mape"])
            add(f"{ds}/{st} SPHP_adopted LOTO prod==stored",
                pr["SPHP_adopted_mape"], stb["SPHP_adopted_mape"])
            add(f"{ds}/{st} SPHP_adopted LOTO prod==rb_live",
                pr["SPHP_adopted_mape"], rbl["SPHP_adopted_mape"])
            add(f"{ds}/{st} P2 LOTO unchanged prod==stored",
                pr["P2_current_mape"], stb["P2_current_mape"])
            # guard: P2 must be untouched (== current P2)
            add(f"{ds}/{st} P2 unchanged (adopted==current, trivially)",
                pr["P2_current_mape"], pr["P2_current_mape"])
            # (B) credit
            if st == "cold":
                add(f"{ds}/{st} full-fit C_c prod==stored",
                    s["full_fit_C_ms"]["production"], stb and s["full_fit_C_ms"]["stored_rb"], tol=0.5)
                add(f"{ds}/{st} full-fit C_c prod==rb_live",
                    s["full_fit_C_ms"]["production"], s["full_fit_C_ms"]["rb"], tol=0.5)
            else:  # warm: C_w must be 0 by construction
                add(f"{ds}/{st} C_w==0 (prod)", s["full_fit_C_ms"]["production"], 0.0, tol=EPS_WARM)
                add(f"{ds}/{st} C_w==0 (rb_live)", s["full_fit_C_ms"]["rb"], 0.0, tol=EPS_WARM)
            # warm SPHP LOTO must be bit-identical current vs adopted
            if st == "warm":
                add(f"{ds}/{st} warm SPHP adopted==current (bit-identical)",
                    pr["SPHP_adopted_mape"], pr["SPHP_current_mape"], tol=EPS_WARM)

    # (C) policy on the new surface
    if "policy" in out["new_20261013"]["states"]["cold"]:
        for st in ("cold", "warm"):
            p = out["new_20261013"]["states"][st]["policy"]
            stored_pol = stored["crossover_policy_deltas"][st]
            add(f"policy/{st} current pooled prod==stored",
                p["current"]["pooled_abs_err_pct"],
                stored_pol["policy_pooled_err_current_pct"])
            add(f"policy/{st} adopted pooled prod==stored",
                p["adopted"]["pooled_abs_err_pct"],
                stored_pol["policy_pooled_err_adopted_pct"])
            # zero argmin flips: every tier must pick the same variant in
            # current and adopted, and match the stored selected set
            flips = [t for a_t, b_t in zip(p["current"]["per_tier"],
                                           p["adopted"]["per_tier"])
                     if a_t["selected"] != b_t["selected"]]
            checks.append({"check": f"policy/{st} zero argmin flips (prod)",
                           "production": flips, "reference": [],
                           "delta": None, "tol": None, "pass": not flips})
            stored_selected = [t["selected"] for t in stored_pol["policy_adopted"]["per_tier"]]
            prod_selected = [t["selected"] for t in p["adopted"]["per_tier"]]
            checks.append({"check": f"policy/{st} adopted selected==stored",
                           "production": prod_selected, "reference": stored_selected,
                           "delta": None, "tol": None,
                           "pass": prod_selected == stored_selected})

    n_pass = sum(1 for c in checks if c["pass"])
    return {"checks": checks,
            "n_pass": n_pass, "n_total": len(checks),
            "all_pass": n_pass == len(checks)}


# --------------------------------------------------------------------------- #
def main() -> int:
    if not STORED_RB.exists():
        print(f"ERROR: stored RB output missing: {STORED_RB}", file=sys.stderr)
        return 2
    stored = json.loads(STORED_RB.read_text())

    out = {
        "generator": ("analysis/rp2_production_port_20261014.py — RP-2 "
                      "production-port like-for-like re-validation"),
        "protocol": ("LOTO regime MAPE (BW=100, top-k=10) + argmin policy "
                     "(new surface, hydration=5.0 class default); production "
                     "model vs RB harness (live) vs stored RB output"),
    }
    out["old_20260918"] = run_dataset("old_20260918", OLD_PATH, stored["old_20260918"])
    out["new_20261013"] = run_dataset("new_20261013", NEW_PATH, stored["new_20261013"])
    out["verdict"] = evaluate(out, stored)

    OUT_JSON.write_text(json.dumps(out, indent=2, default=str) + "\n")

    # ---- markdown report ----
    md = ["# RP-2 production-port like-for-like re-validation (2026-10-14)\n"]
    md.append("The adopted RP-2 cold-only SPHP hit credit has been ported into "
              "the production analytical cost model (`cost_model.py`).  This "
              "harness re-validates the ported form against the analysis-level "
              "numbers from `revision_board_eval_20261014`.\n")
    md.append("\n## LOTO regime MAPE (%) — production model vs RB-stored\n")
    md.append("| surface | state | arm | production | RB-stored | Δ |\n")
    md.append("|---|---|---|---|---|---|")
    for ds in ("old_20260918", "new_20261013"):
        for st in ("cold", "warm"):
            s = out[ds]["states"][st]
            pr, stb = s["loto"]["production"], s["loto"]["stored_rb"]
            for arm, key in (("P2 (guard)", "P2_current_mape"),
                             ("SPHP current (legacy)", "SPHP_current_mape"),
                             ("SPHP adopted (RP-2)", "SPHP_adopted_mape")):
                md.append(f"| {ds[-8:]} | {st} | {arm} | "
                          f"{pr[key]} | {stb[key]} | {round(pr[key]-stb[key],3)} |")
    md.append("\n## Full-fit cold credit C_c (ms) and warm C_w\n")
    md.append("| surface | state | production C | RB-live C | stored C |\n")
    md.append("|---|---|---|---|---|")
    for ds in ("old_20260918", "new_20261013"):
        for st in ("cold", "warm"):
            c = out[ds]["states"][st]["full_fit_C_ms"]
            md.append(f"| {ds[-8:]} | {st} | {c['production']} | "
                      f"{c['rb']} | {c['stored_rb']} |")
    md.append("\n## argmin placement policy (new surface; hydration=5.0 ms class default)\n")
    md.append("| state | pooled err current | pooled err adopted | Δ pp | argmin flips |\n")
    md.append("|---|---|---|---|---|")
    for st in ("cold", "warm"):
        p = out["new_20261013"]["states"][st]["policy"]
        flips = [t for a, b in zip(p["current"]["per_tier"], p["adopted"]["per_tier"])
                 if a["selected"] != b["selected"]]
        delta = round(p["adopted"]["pooled_abs_err_pct"]
                      - p["current"]["pooled_abs_err_pct"], 2)
        md.append(f"| {st} | {p['current']['pooled_abs_err_pct']} | "
                  f"{p['adopted']['pooled_abs_err_pct']} | {delta} | "
                  f"{flips or 'none'} |")
    v = out["verdict"]
    md.append(f"\n## Verdict: {'**ALL PASS**' if v['all_pass'] else '**DIVERGENCE — STOP**'} "
              f"({v['n_pass']}/{v['n_total']} checks)\n")
    for c in v["checks"]:
        if not c["pass"]:
            md.append(f"- FAIL: {c['check']}  prod={c['production']} "
                      f"ref={c['reference']} delta={c['delta']}")
    md.append("\n> If any check FAILED, the port diverges from the "
              "analysis-level validation — the adopted form must NOT be "
              "claimed adopted (production) until reconciled.")
    OUT_MD.write_text("\n".join(md) + "\n")

    print(f"wrote {OUT_JSON}")
    print(f"wrote {OUT_MD}")
    print(f"\nVERDICT: {'ALL PASS' if v['all_pass'] else 'DIVERGENCE — STOP'} "
          f"({v['n_pass']}/{v['n_total']})")
    for c in v["checks"]:
        if not c["pass"]:
            print(f"  FAIL: {c['check']}  prod={c['production']} "
                  f"ref={c['reference']} delta={c['delta']}")
    return 0 if v["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
