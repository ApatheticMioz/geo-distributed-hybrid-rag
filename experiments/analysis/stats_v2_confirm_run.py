#!/usr/bin/env python3
"""CI'd headline analysis — v2 2-node confirmatory re-baseline (2026-10-13).

Runs the pre-registered test battery from docs/campaign_v2_design.md §7
(exact functions from experiments/analysis/stats.py, frozen seeds:
BCa B=1000 / permutation n=10000 / seed=42) on:

  * NEW  campaign_v2_confirm_2node_20261013.jsonl (2,880 recs; N=120/arm)
  * OLD  campaign_20260918T130756Z.jsonl          (1,200 recs; N=50/arm)

Families (per dispatch, following design §7 test/multiplicity rows):
  PRIMARY   (Holm FWER): P2-SPHP vs P2, COLD TTFT (repeat_index==0),
                         4 RTT tiers x {mean, median}, one-sided 'less',
                         Holm over the family.
  SECONDARY (BH FDR):    same battery on WARM TTFT (repeat_index>=1),
                         BH over the family.
  OLD-NEW:   identical battery on the old campaign records.
  Hit rate:  Wilson 95% CI overall / per tier / per stratum + mean
             sphp_overlap with BCa CI.
  Power:     achieved power / MDE per cell at observed CVs (two-sample
             primary; paired rho=0.8 counterfactual per design §4.2).

Outputs:
  experiments/analysis/results/stats_v2_confirm_2node_20261013.json
  experiments/analysis/results/stats_v2_confirm_2node_20261013.md
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import numpy as np

from experiments.analysis.stats import (
    achieved_power,
    benjamini_hochberg,
    bootstrap_ci,
    bootstrap_delta_ci,
    cliffs_delta,
    cohens_d,
    holm,
    mde,
    percentile,
    permutation_test,
    wilson_ci,
)

SEED = 42
B = 1000
N_PERM = 10000
ALPHA = 0.05
RTT_TIERS = (0, 15, 40, 80)

NEW_PATH = ROOT / "experiments/bench/campaigns/campaign_v2_confirm_2node_20261013.jsonl"
OLD_PATH = ROOT / "experiments/bench/campaigns/campaign_20260918T130756Z.jsonl"
NEW_MANIFEST = ROOT / "experiments/bench/campaigns/campaign_v2_manifest.json"
OUT_JSON = ROOT / "experiments/analysis/results/stats_v2_confirm_2node_20261013.json"
OUT_MD = ROOT / "experiments/analysis/results/stats_v2_confirm_2node_20261013.md"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_records(path: Path) -> list[dict]:
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def ttft_by(records: list[dict], rtt: int, variant: str, cold: bool) -> list[float]:
    out = []
    for r in records:
        if r["rtt_ms"] != rtt or r["variant"] != variant:
            continue
        if cold and r["repeat_index"] != 0:
            continue
        if not cold and r["repeat_index"] < 1:
            continue
        if r.get("ttft_ms") is None:
            continue
        out.append(r["ttft_ms"])
    return out


# ---------------------------------------------------------------------------
# Test battery
# ---------------------------------------------------------------------------

def cell_battery(base: list[float], sphp: list[float], tag: str,
                 stat: str) -> dict:
    """One pre-registered test cell: BCa delta CI + one-sided permutation
    test (mean or median) + effect sizes + power audit."""
    delta = bootstrap_delta_ci(sphp, base, stat=stat, method="bca",
                               paired=False, B=B, seed=SEED)
    perm = permutation_test(sphp, base, stat=stat, alternative="less",
                            n_resamples=N_PERM, seed=SEED)
    n_b, n_s = len(base), len(sphp)
    cv_b = float(np.std(base, ddof=1) / np.mean(base))
    cv_s = float(np.std(sphp, ddof=1) / np.mean(sphp))
    # observed relative delta of the treatment effect (fraction)
    rel_d = abs(delta["delta"]) / abs(np.mean(base))
    two = {
        "n_per_arm": {"baseline": n_b, "sphp": n_s},
        "cv_baseline": round(cv_b, 4),
        "cv_sphp": round(cv_s, 4),
        "mde": round(mde(min(n_b, n_s), cv_b, design="two-sample"), 4),
        "achieved_power_at_observed_rel_d": round(
            achieved_power(min(n_b, n_s), rel_d, cv_b, design="two-sample"), 4),
        "achieved_power_at_10pct": round(
            achieved_power(min(n_b, n_s), 0.10, cv_b, design="two-sample"), 4),
    }
    paired = {
        "rho": 0.8,
        "mde": round(mde(min(n_b, n_s), cv_b, design="paired"), 4),
        "achieved_power_at_observed_rel_d": round(
            achieved_power(min(n_b, n_s), rel_d, cv_b, design="paired"), 4),
    }
    return {
        "cell": tag,
        "stat": stat,
        "n_baseline": n_b,
        "n_sphp": n_s,
        "baseline": {
            "mean": round(float(np.mean(base)), 2),
            "median": round(float(np.median(base)), 2),
            "cv": round(cv_b, 4),
        },
        "sphp": {
            "mean": round(float(np.mean(sphp)), 2),
            "median": round(float(np.median(sphp)), 2),
            "cv": round(cv_s, 4),
        },
        "delta": round(delta["delta"], 2),
        "delta_ci_bca": [round(v, 2) for v in delta["ci"]],
        "relative_delta_pct": round(delta["relative_delta_pct"], 2)
        if delta["relative_delta_pct"] is not None else None,
        "relative_delta_ci_pct": (
            [round(v, 2) for v in delta["relative_ci_pct"]]
            if delta["relative_ci_pct"] is not None else None),
        "permutation": {
            "p_one_sided_less": perm["p"],
            "statistic": round(perm["statistic"], 2),
            "n_resamples": N_PERM,
            "seed": SEED,
            "alternative": "less",
            "contiguity": "+1 continuity correction",
        },
        "effect_sizes": {
            "cohens_d": round(cohens_d(sphp, base, paired=False), 3),
            "cliffs_delta": round(cliffs_delta(sphp, base), 3),
        },
        "power": {"two_sample": two, "paired_rho_0p8": paired},
    }


def run_campaign(records: list[dict], name: str) -> dict:
    cells = {}
    cold_pvals, cold_tags = [], []
    warm_pvals, warm_tags = [], []
    for rtt in RTT_TIERS:
        base_c = ttft_by(records, rtt, "baseline", cold=True)
        sphp_c = ttft_by(records, rtt, "sphp", cold=True)
        base_w = ttft_by(records, rtt, "baseline", cold=False)
        sphp_w = ttft_by(records, rtt, "sphp", cold=False)
        for stat in ("mean", "median"):
            tag_c = f"rtt{rtt}_cold_ttft_{stat}"
            tag_w = f"rtt{rtt}_warm_ttft_{stat}"
            cells[tag_c] = cell_battery(base_c, sphp_c, tag_c, stat)
            cells[tag_w] = cell_battery(base_w, sphp_w, tag_w, stat)
            cold_pvals.append(cells[tag_c]["permutation"]["p_one_sided_less"])
            cold_tags.append(tag_c)
            warm_pvals.append(cells[tag_w]["permutation"]["p_one_sided_less"])
            warm_tags.append(tag_w)

    holm_cold = holm(cold_pvals)
    # Pre-registration deviation note: design §2 lists the H3 primary as
    # "4 one-sided permutation tests, Holm-adjusted" (mean only); §7's
    # battery prescribes mean AND median.  Report both Holm readings.
    mean_only_p = [p for tag, p in zip(cold_tags, cold_pvals)
                   if tag.endswith("_mean")]
    holm_cold_4 = holm(mean_only_p)
    bh_warm = benjamini_hochberg(warm_pvals)
    for tag, adj in zip(cold_tags, holm_cold):
        p = cells[tag]["permutation"]["p_one_sided_less"]
        cells[tag]["multiplicity"] = {
            "method": "Holm (FWER, PRIMARY family)",
            "family": f"{len(cold_pvals)} tests = 4 RTT tiers x {{mean, median}} cold TTFT",
            "raw_p": p,
            "adjusted_p": round(adj, 6),
            "alpha": ALPHA,
            "significant": bool(adj < ALPHA),
        }
        if tag.endswith("_mean"):
            adj4 = holm_cold_4[mean_only_p.index(p)]
            cells[tag]["multiplicity"]["adjusted_p_4test_reading"] = round(adj4, 6)
            cells[tag]["multiplicity"]["significant_4test_reading"] = bool(adj4 < ALPHA)
    for tag, adj in zip(warm_tags, bh_warm):
        p = cells[tag]["permutation"]["p_one_sided_less"]
        cells[tag]["multiplicity"] = {
            "method": "Benjamini-Hochberg (FDR, SECONDARY family)",
            "family": f"{len(warm_pvals)} tests = 4 RTT tiers x {{mean, median}} warm TTFT",
            "raw_p": p,
            "adjusted_p": round(adj, 6),
            "alpha": ALPHA,
            "significant": bool(adj < ALPHA),
        }

    # Descriptive strata blocks (LR-7: p50/p95/p99 with BCa CIs per cell)
    descs = {}
    for rtt in RTT_TIERS:
        for variant in ("baseline", "sphp"):
            for cold in (True, False):
                vals = ttft_by(records, rtt, variant, cold=cold)
                descs[f"rtt{rtt}_{'cold' if cold else 'warm'}_{variant}"] = {
                    "n": len(vals),
                    "p50": round(percentile(vals, 50), 2),
                    "ci95_p50_bca": [round(v, 2) for v in bootstrap_ci(
                        vals, stat="p50", method="bca", B=B, seed=SEED)],
                    "p95": round(percentile(vals, 95), 2),
                    "ci95_p95_bca": [round(v, 2) for v in bootstrap_ci(
                        vals, stat="p95", method="bca", B=B, seed=SEED)],
                    "p99": round(percentile(vals, 99), 2),
                    "ci95_p99_bca": [round(v, 2) for v in bootstrap_ci(
                        vals, stat="p99", method="bca", B=B, seed=SEED)],
                }

    # SPHP hit rate + overlap
    hits = {"overall": [], "tiers": {}, "strata": {"cold": [], "warm": []}}
    overlap_by = {"all": [], "cold": [], "warm": [], "tiers": {}}
    for r in records:
        if r["variant"] != "sphp":
            continue
        cold = r["repeat_index"] == 0
        h = r.get("sphp_hit")
        if h is not None:
            hits["overall"].append(bool(h))
            t = hits["tiers"].setdefault(r["rtt_ms"], [])
            t.append(bool(h))
            hits["strata"]["cold" if cold else "warm"].append(bool(h))
        if r.get("sphp_overlap") is not None:
            overlap_by["all"].append(r["sphp_overlap"])
            overlap_by["cold" if cold else "warm"].append(r["sphp_overlap"])
            overlap_by["tiers"].setdefault(r["rtt_ms"], []).append(r["sphp_overlap"])

    def wilson_block(vals: list[bool]) -> dict:
        k, n = sum(vals), len(vals)
        lo, hi = wilson_ci(k, n)
        return {"n": n, "hits": k, "rate": round(k / n, 4),
                "wilson_ci95": [round(lo, 4), round(hi, 4)]}

    hit_block = {
        "definition": "sphp_hit == True among SPHP-arm records "
                      "(null sphp_hit counted as non-hit)",
        "overall": wilson_block(hits["overall"]),
        "by_tier": {f"rtt{rtt}": wilson_block(v)
                    for rtt, v in sorted(hits["tiers"].items())},
        "by_stratum": {s: wilson_block(v) for s, v in hits["strata"].items()},
    }

    def overlap_block(vals: list[float]) -> dict:
        if not vals:
            return {"n": 0}
        return {"n": len(vals),
                "mean": round(float(np.mean(vals)), 4),
                "mean_ci95_bca": [round(v, 4) for v in bootstrap_ci(
                    vals, stat="mean", method="bca", B=B, seed=SEED)],
                "median": round(float(np.median(vals)), 4)}

    overlap_block_out = {
        "definition": "mean of sphp_overlap over SPHP records with "
                      "non-null overlap (cold and warm records both)",
        "overall": overlap_block(overlap_by["all"]),
        "cold": overlap_block(overlap_by["cold"]),
        "warm": overlap_block(overlap_by["warm"]),
        "by_tier": {f"rtt{rtt}": overlap_block(v)
                    for rtt, v in sorted(overlap_by["tiers"].items())},
    }

    return {
        "campaign": name,
        "n_records": len(records),
        "seed": SEED,
        "B_bootstrap": B,
        "n_permutation": N_PERM,
        "alpha": ALPHA,
        "cells": cells,
        "descriptive": descs,
        "sphp_hit_rate": hit_block,
        "sphp_overlap": overlap_block_out,
    }


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

def _verdict_line(tag: str, c: dict) -> str:
    m = c["multiplicity"]
    star = "**SIG**" if m["significant"] else "ns"
    return (f"| {tag} | {c['delta']:.1f} | [{c['delta_ci_bca'][0]:.1f}, "
            f"{c['delta_ci_bca'][1]:.1f}] | {c['relative_delta_pct']:+.2f}% "
            f"[{c['relative_delta_ci_pct'][0]:+.2f}, {c['relative_delta_ci_pct'][1]:+.2f}] "
            f"| {m['raw_p']:.4g} | {m['adjusted_p']:.4g} | "
            f"d={c['effect_sizes']['cohens_d']:+.2f}, "
            f"δ={c['effect_sizes']['cliffs_delta']:+.2f} | {star} |")


def render_md(result: dict) -> str:
    L = []
    L.append("# CI'd Headline Analysis — v2 2-node Confirmatory Re-baseline "
             "(2026-10-13)")
    L.append("")
    ap = result["analysis_plan"]
    L.append(f"**Inputs:** NEW `{ap['new_campaign']}` (N={ap['new_n_per_arm']}/arm, "
             f"{ap['new_n_records']} records, 0 errors); OLD `{ap['old_campaign']}` "
             f"(N={ap['old_n_per_arm']}/arm, {ap['old_n_records']} records). "
             f"Arms: P2-SPHP vs P2 (baseline), RTT tiers {ap['rtt_tiers']} ms. "
             f"Cold = repeat_index 0, warm = repeats ≥ 1 (strata, not replicates).")
    L.append("")
    L.append(f"**Methods (pre-registered, design §7 / LR-3):** BCa bootstrap delta "
             f"CI (B={result['new']['B_bootstrap']}, seed={result['new']['seed']}); "
             f"one-sided permutation test (statistic = mean and median, "
             f"n={result['new']['n_permutation']} resamples, seed="
             f"{result['new']['seed']}, +1 continuity correction); Cliff's δ and "
             f"Cohen's d; Holm FWER for the PRIMARY (cold) family, BH FDR for the "
             f"SECONDARY (warm) family; α = {ALPHA}. Wilson 95% CIs for the SPHP "
             f"hit rate; power audit per cell at the observed CVs "
             f"(two-sample primary; paired ρ=0.8 counterfactual). Effect sizes "
             f"(Cohen's d, Cliff's δ) are computed on the full samples "
             f"(mean-based d) per cell, independent of which statistic "
             f"(mean/median) the delta test targets.")

    if ap.get("deviations"):
        L.append("")
        L.append("**Pre-registration deviation notes:**")
        for d in ap["deviations"]:
            L.append(f"- {d}")
    L.append("")

    for label, key, family in (("NEW (N=120/arm)", "new", None),
                               ("OLD (N=50/arm)", "old", None)):
        run = result[key]
        L.append(f"## {label}")
        L.append("")
        L.append(f"**{'PRIMARY (cold, Holm)' if key=='new' else 'Old cold (Holm, same battery)'}** "
                 "— P2-SPHP vs P2, cold TTFT, 4 tiers × {mean, median}:")
        L.append("")
        L.append("| Cell | Δ TTFT (ms) | BCa 95% CI | Rel. Δ [CI] | p (perm, one-sided less) | "
                 "Adj. p | Effect (d, δ) | Verdict |")
        L.append("|---|---|---|---|---|---|---|---|")
        for rtt in RTT_TIERS:
            for stat in ("mean", "median"):
                tag = f"rtt{rtt}_cold_ttft_{stat}"
                L.append(_verdict_line(tag, run["cells"][tag]))
        L.append("")
        L.append(f"**{'SECONDARY (warm, BH)' if key=='new' else 'Old warm (BH, same battery)'}** "
                 "— P2-SPHP vs P2, warm TTFT, 4 tiers × {mean, median}:")
        L.append("")
        L.append("| Cell | Δ TTFT (ms) | BCa 95% CI | Rel. Δ [CI] | p (perm, one-sided less) | "
                 "Adj. p | Effect (d, δ) | Verdict |")
        L.append("|---|---|---|---|---|---|---|---|")
        for rtt in RTT_TIERS:
            for stat in ("mean", "median"):
                tag = f"rtt{rtt}_warm_ttft_{stat}"
                L.append(_verdict_line(tag, run["cells"][tag]))
        L.append("")

    # Old vs new comparison
    L.append("## Old (N=50) vs New (N=120) — powered re-answer")
    L.append("")
    L.append("Same battery, same seeds; warm family is the open v1 finding "
             "(3 of 4 tiers non-significant at N=50).")
    L.append("")
    L.append("| Cell | p old | adj. p old | old verdict | p new | adj. p new | new verdict | Δ rel. (old→new) |")
    L.append("|---|---|---|---|---|---|---|---|")
    for rtt in RTT_TIERS:
        for stat in ("mean", "median"):
            tag = f"rtt{rtt}_warm_ttft_{stat}"
            co = result["old"]["cells"][tag]
            cn = result["new"]["cells"][tag]
            vo = "**SIG**" if co["multiplicity"]["significant"] else "ns"
            vn = "**SIG**" if cn["multiplicity"]["significant"] else "ns"
            L.append(f"| {tag} | {co['multiplicity']['raw_p']:.4g} | "
                     f"{co['multiplicity']['adjusted_p']:.4g} | {vo} | "
                     f"{cn['multiplicity']['raw_p']:.4g} | "
                     f"{cn['multiplicity']['adjusted_p']:.4g} | {vn} | "
                     f"{co['relative_delta_pct']:+.1f}% → {cn['relative_delta_pct']:+.1f}% |")
    L.append("")
    L.append("Cold family (for reference — both campaigns):")
    L.append("")
    L.append("| Cell | adj. p old | adj. p new |")
    L.append("|---|---|---|")
    for rtt in RTT_TIERS:
        for stat in ("mean", "median"):
            tag = f"rtt{rtt}_cold_ttft_{stat}"
            L.append(f"| {tag} | {result['old']['cells'][tag]['multiplicity']['adjusted_p']:.4g} | "
                     f"{result['new']['cells'][tag]['multiplicity']['adjusted_p']:.4g} |")
    L.append("")

    # Hit rate
    L.append("## SPHP hit rate (Wilson 95% CI) & prefix overlap")
    L.append("")
    h = result["new"]["sphp_hit_rate"]
    L.append(f"**NEW:** overall {h['overall']['hits']}/{h['overall']['n']} = "
             f"{h['overall']['rate']:.3f} "
             f"[{h['overall']['wilson_ci95'][0]:.3f}, {h['overall']['wilson_ci95'][1]:.3f}]")
    L.append("")
    L.append("| Tier | New hits/n | New rate [CI] | Old hits/n | Old rate [CI] |")
    L.append("|---|---|---|---|---|")
    ho = result["old"]["sphp_hit_rate"]
    for rtt in RTT_TIERS:
        tn, to = h["by_tier"][f"rtt{rtt}"], ho["by_tier"][f"rtt{rtt}"]
        L.append(f"| rtt {rtt} | {tn['hits']}/{tn['n']} | {tn['rate']:.3f} "
                 f"[{tn['wilson_ci95'][0]:.3f}, {tn['wilson_ci95'][1]:.3f}] | "
                 f"{to['hits']}/{to['n']} | {to['rate']:.3f} "
                 f"[{to['wilson_ci95'][0]:.3f}, {to['wilson_ci95'][1]:.3f}] |")
    L.append(f"| stratum cold | {h['by_stratum']['cold']['hits']}/"
             f"{h['by_stratum']['cold']['n']} | {h['by_stratum']['cold']['rate']:.3f} "
             f"[{h['by_stratum']['cold']['wilson_ci95'][0]:.3f}, "
             f"{h['by_stratum']['cold']['wilson_ci95'][1]:.3f}] | "
             f"{ho['by_stratum']['cold']['hits']}/{ho['by_stratum']['cold']['n']} | "
             f"{ho['by_stratum']['cold']['rate']:.3f} "
             f"[{ho['by_stratum']['cold']['wilson_ci95'][0]:.3f}, "
             f"{ho['by_stratum']['cold']['wilson_ci95'][1]:.3f}] |")
    L.append(f"| stratum warm | {h['by_stratum']['warm']['hits']}/"
             f"{h['by_stratum']['warm']['n']} | {h['by_stratum']['warm']['rate']:.3f} "
             f"[{h['by_stratum']['warm']['wilson_ci95'][0]:.3f}, "
             f"{h['by_stratum']['warm']['wilson_ci95'][1]:.3f}] | "
             f"{ho['by_stratum']['warm']['hits']}/{ho['by_stratum']['warm']['n']} | "
             f"{ho['by_stratum']['warm']['rate']:.3f} "
             f"[{ho['by_stratum']['warm']['wilson_ci95'][0]:.3f}, "
             f"{ho['by_stratum']['warm']['wilson_ci95'][1]:.3f}] |")
    L.append("")
    L.append("")
    L.append("Old denominators exclude 3 SPHP records at rtt 0 with "
             "`sphp_hit=null` (see deviation notes).")
    L.append("")
    on, oo = result["new"]["sphp_overlap"], result["old"]["sphp_overlap"]
    L.append(f"**Mean prefix overlap (new):** {on['overall']['mean']:.3f} "
             f"[{on['overall']['mean_ci95_bca'][0]:.3f}, "
             f"{on['overall']['mean_ci95_bca'][1]:.3f}] over {on['overall']['n']} "
             f"records (cold {on['cold']['mean']:.3f} [n={on['cold']['n']}]; "
             f"warm {on['warm']['mean']:.3f} [n={on['warm']['n']}]). "
             f"Old: {oo['overall']['mean']:.3f} [n={oo['overall']['n']}].")
    L.append("")

    # Power
    L.append("## Power audit (observed CVs, per cell)")
    L.append("")
    L.append("Two-sample (the actual design; control-arm CV) and paired "
             "ρ=0.8 counterfactual (design §4.2). MDE = minimum detectable "
             "relative delta at α=0.05 / power 0.80; power-at-observed = "
             "achieved power at the cell's observed relative delta.")
    L.append("")
    L.append("Notes: warm cells run at n = 240/arm (new) / 100/arm (old) — "
             "2 warm repeats per query pooled, per the §7 'repeats 1+' "
             "definition. Observed CVs are of the warm *pooled* strata and "
             "run above the design's per-query CV inputs (0.209–0.282) "
             "because pooling across queries adds between-query variance — "
             "the design's §4.2 power table remains valid for per-query "
             "effects. For cells where the observed direction is adverse "
             "(sphp > baseline, e.g. rtt15/rtt80 warm medians), "
             "power-at-observed is reported on the magnitude of the "
             "observed |delta| (the direction itself is the null result).")
    L.append("")
    L.append("| Cell | n/arm | CV (base) | MDE 2-samp | Power@obs 2-samp | Power@10% 2-samp | MDE paired | Power@obs paired |")
    L.append("|---|---|---|---|---|---|---|---|")
    for key, label in (("old", "old"), ("new", "new")):
        run = result[key]
        for rtt in RTT_TIERS:
            for stat in ("mean", "median"):
                for cold in ("cold", "warm"):
                    tag = f"rtt{rtt}_{cold}_ttft_{stat}"
                    pw = run["cells"][tag]["power"]
                    t, p = pw["two_sample"], pw["paired_rho_0p8"]
                    nlab = f"{t['n_per_arm']['baseline']}"
                    L.append(f"| {label} {tag} | {nlab} | {t['cv_baseline']} | "
                             f"{t['mde']:.3f} | {t['achieved_power_at_observed_rel_d']:.3f} | "
                             f"{t['achieved_power_at_10pct']:.3f} | "
                             f"{p['mde']:.3f} | {p['achieved_power_at_observed_rel_d']:.3f} |")
    L.append("")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------

def main() -> int:
    print(f"loading NEW {NEW_PATH}")
    new_recs = load_records(NEW_PATH)
    print(f"  {len(new_recs)} records")
    assert len(new_recs) == 2880, f"expected 2880, got {len(new_recs)}"
    n_err = sum(1 for r in new_recs if r.get("error") or r.get("ttft_ms") is None)
    assert n_err == 0, f"non-zero error/missing records: {n_err}"

    print(f"loading OLD {OLD_PATH}")
    old_recs = load_records(OLD_PATH)
    print(f"  {len(old_recs)} records")
    assert len(old_recs) == 1200, f"expected 1200, got {len(old_recs)}"

    print("running NEW battery (8 arms x 4 tiers x {cold,warm} x {mean,median})...")
    new_run = run_campaign(new_recs, "campaign_v2_confirm_2node_20261013.jsonl")
    print("  done")
    print("running OLD battery...")
    old_run = run_campaign(old_recs, "campaign_20260918T130756Z.jsonl")
    print("  done")

    # Manifest cross-check
    manifest = json.load(open(NEW_MANIFEST, encoding="utf-8"))
    n_per_arm = manifest.get("n_per_arm")
    # Manifest cross-check: 120 measured queries/arm; the cold stratum
    # (repeat_index 0) must therefore be exactly n_per_arm per arm.  The
    # warm stratum is 2 repeats/query (240 records/arm) — see deviation note.
    rep_ok = all(
        len(ttft_by(new_recs, rtt, v, cold=True)) == n_per_arm
        for rtt in RTT_TIERS for v in ("baseline", "sphp")
    )

    result = {
        "analysis_plan": {
            "design_doc": "docs/campaign_v2_design.md",
            "design_commit": manifest.get("design_commit"),
            "stats_engine": "experiments/analysis/stats.py (unmodified)",
            "new_campaign": NEW_PATH.name,
            "old_campaign": OLD_PATH.name,
            "new_n_per_arm": n_per_arm,
            "old_n_per_arm": 50,
            "new_n_records": len(new_recs),
            "old_n_records": len(old_recs),
            "rtt_tiers": list(RTT_TIERS),
            "cold_definition": "repeat_index == 0",
            "warm_definition": "repeat_index >= 1",
            "primary_family": "Holm FWER over 8 tests (4 RTT tiers x {mean, median}) on cold TTFT",
            "secondary_family": "Benjamini-Hochberg FDR over 8 tests (4 RTT tiers x {mean, median}) on warm TTFT",
            "seeds": {"bootstrap": SEED, "permutation": SEED, "B": B, "n_resamples": N_PERM},
            "manifest_cold_n_per_arm_check": rep_ok,
            "deviations": [
                "Design §2 lists the H3 primary family as '4 one-sided permutation "
                "tests, Holm-adjusted', while §7's pre-registered battery prescribes "
                "the delta CI + permutation test on BOTH mean and median, and the "
                "dispatch runs the identical battery on both statistics. Holm is "
                "applied over the 8-test family (primary adjusted_p); the 4-test "
                "reading (mean only) is reported alongside as adjusted_p_4test_reading "
                "on each cold-mean cell. In this data both readings give identical "
                "verdicts: all 4 cold-mean cells have p = 1/10001, Holm(8) = 0.0008, "
                "Holm(4) = 0.0004, all < 0.05.",
                "Warm strata contribute 2 records per query (repeats 1-2), so the "
                "warm battery runs at n = 240/arm (new) and n = 100/arm (old); the "
                "frozen design N = 120/arm refers to distinct measured queries. "
                "The pre-registered §7 battery (repeats 1+) is run as-is.",
                "Old campaign: 3 SPHP records (rtt 0: query 280a3955 repeat 0, "
                "eba80711 repeat 1, f775d1e4 repeat 2) have sphp_hit=null and "
                "are excluded from the hit-rate denominators (n=147/150 at "
                "rtt 0; the NEW campaign has zero nulls, n=1440). Their valid "
                "TTFT values remain in the latency battery.",
            ],
        },
        "new": new_run,
        "old": old_run,
    }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(f"wrote {OUT_JSON}")

    md = render_md(result)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"wrote {OUT_MD}")

    # Console verdict summary
    for key in ("new", "old"):
        run = result[key]
        for fam in ("cold", "warm"):
            sig = [t for t in run["cells"] if t.startswith(f"rtt") and f"_{fam}_ttft_" in t
                   and run["cells"][t]["multiplicity"]["significant"]]
            print(f"  {key} {fam}: significant = {sig}")
    h = new_run["sphp_hit_rate"]["overall"]
    print(f"  NEW hit rate overall: {h['hits']}/{h['n']} = {h['rate']} CI {h['wilson_ci95']}")
    print(f"  NEW mean overlap: {new_run['sphp_overlap']['overall']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
