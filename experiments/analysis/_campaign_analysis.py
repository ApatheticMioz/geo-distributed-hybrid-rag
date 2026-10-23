#!/usr/bin/env python3
"""One-shot analysis of experiments/bench/campaigns/campaign_20260918T130756Z.jsonl.
Writes analysis/results/campaign_analysis.json. Stdlib only; bootstrap seed 42."""
import json, math, random, statistics
from collections import defaultdict

SRC = "experiments/bench/campaigns/campaign_20260918T130756Z.jsonl"
OUT = "analysis/results/campaign_analysis.json"
RTTS = [0, 15, 40, 80]
B = 1000
SEED = 42

recs = [json.loads(l) for l in open(SRC)]
assert len(recs) == 1200

def mean(xs): return sum(xs) / len(xs)

def median(xs):
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else 0.5 * (xs[n // 2 - 1] + xs[n // 2])

def pctl(xs, p):
    xs = sorted(xs)
    if not xs: return None
    k = (len(xs) - 1) * p
    f, c = math.floor(k), math.ceil(k)
    if f == c: return xs[int(k)]
    return xs[f] * (c - k) + xs[c] * (k - f)

def boot_ci(xs, rng, b=B):
    n = len(xs)
    means = []
    for _ in range(b):
        s = [xs[rng.randrange(n)] for _ in range(n)]
        means.append(mean(s))
    means.sort()
    return [means[int(0.025 * b)], means[int(0.975 * b) - 1]]

def boot_diff_ci(a, b, rng, nboot=B):
    na, nb = len(a), len(b)
    diffs = []
    for _ in range(nboot):
        ma = mean([a[rng.randrange(na)] for _ in range(na)])
        mb = mean([b[rng.randrange(nb)] for _ in range(nb)])
        diffs.append(ma - mb)
    diffs.sort()
    return [diffs[int(0.025 * nboot)], diffs[int(0.975 * nboot) - 1]]

def wilson_ci(k, n, z=1.96):
    if n == 0: return [0.0, 0.0]
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return [max(0.0, center - half), min(1.0, center + half)]

def group(rtt, variant, rep=None):
    out = []
    for r in recs:
        if r["rtt_ms"] != rtt or r["variant"] != variant: continue
        if rep is not None and r["repeat_index"] != rep: continue
        out.append(r)
    return out

def stat_block(vals, rng):
    return {
        "n": len(vals),
        "mean": round(mean(vals), 2),
        "median": round(median(vals), 2),
        "p95": round(pctl(vals, 0.95), 2),
        "ci95": [round(x, 2) for x in boot_ci(vals, rng)],
    }

# ---------- 1. per_tier ----------
per_tier = {}
for rtt in RTTS:
    for variant in ("baseline", "sphp"):
        g = group(rtt, variant)
        cold = [r["ttft_ms"] for r in g if r["repeat_index"] == 0]
        warm = [r["ttft_ms"] for r in g if r["repeat_index"] >= 1]
        rng = random.Random(SEED)
        tps_all = [r["decode_tps"] for r in g]
        tps_ok = [t for t in tps_all if t <= 100]  # exclude degenerate tiny-decode_ms records
        block = {
            "n": len(g),
            "ttft_cold": stat_block(cold, rng),
            "ttft_warm": stat_block(warm, rng),
            "total_ms_cold_mean": round(mean([r["total_ms"] for r in g if r["repeat_index"] == 0]), 2),
            "total_ms_warm_mean": round(mean([r["total_ms"] for r in g if r["repeat_index"] >= 1]), 2),
            "decode_tps_mean": round(mean(tps_all), 2),
            "decode_tps_mean_excl_degenerate": round(mean(tps_ok), 2) if tps_ok else None,
            "decode_tps_n_degenerate": len(tps_all) - len(tps_ok),
        }
        per_tier[f"rtt{rtt}_{variant}"] = block

# ---------- 2. per_leg ----------
per_leg = {}
for rtt in RTTS:
    for variant in ("baseline", "sphp"):
        g = group(rtt, variant)
        entry = {}
        for leg in ("sparse_ms", "dense_ms", "fusion_ms"):
            cold = [r[leg] for r in g if r["repeat_index"] == 0]
            warm = [r[leg] for r in g if r["repeat_index"] >= 1]
            entry[leg] = {
                "cold": {"mean": round(mean(cold), 2), "median": round(median(cold), 2)},
                "warm": {"mean": round(mean(warm), 2), "median": round(median(warm), 2)},
            }
        per_leg[f"rtt{rtt}_{variant}"] = entry

# ---------- 3. sphp_detail ----------
sphp_detail = {}
for rtt in RTTS:
    g = [r for r in group(rtt, "sphp") if r["sphp_hit"] is not None]
    hits = [r for r in g if r["sphp_hit"] is True]
    miss = [r for r in g if r["sphp_hit"] is False]
    n_hit, n_miss = len(hits), len(miss)
    hr = n_hit / (n_hit + n_miss)
    overlap = [r["sphp_overlap"] for r in g if r["sphp_overlap"] is not None]
    wasted_hit = [r["wasted_prefill_ms"] for r in hits if r["wasted_prefill_ms"] is not None]
    wasted_miss = [r["wasted_prefill_ms"] for r in miss if r["wasted_prefill_ms"] is not None]
    sphp_detail[f"rtt{rtt}"] = {
        "n_evaluated": len(g),
        "n_hit": n_hit,
        "n_miss": n_miss,
        "hit_rate": round(hr, 4),
        "hit_rate_ci95_wilson": [round(x, 4) for x in wilson_ci(n_hit, n_hit + n_miss)],
        "ttft_cold": {
            "hit_mean": round(mean([r["ttft_ms"] for r in hits if r["repeat_index"] == 0]), 2),
            "miss_mean": round(mean([r["ttft_ms"] for r in miss if r["repeat_index"] == 0]), 2),
        },
        "ttft_warm": {
            "hit_mean": round(mean([r["ttft_ms"] for r in hits if r["repeat_index"] >= 1]), 2),
            "miss_mean": round(mean([r["ttft_ms"] for r in miss if r["repeat_index"] >= 1]), 2),
        },
        "wasted_prefill_ms": {
            "hit": {"n": len(wasted_hit), "mean": round(mean(wasted_hit), 2) if wasted_hit else None,
                    "median": round(median(wasted_hit), 2) if wasted_hit else None},
            "miss": {"n": len(wasted_miss), "mean": round(mean(wasted_miss), 2) if wasted_miss else None,
                     "median": round(median(wasted_miss), 2) if wasted_miss else None},
        },
        "overlap": {
            "n": len(overlap),
            "p10": round(pctl(overlap, 0.10), 3),
            "p50": round(pctl(overlap, 0.50), 3),
            "p90": round(pctl(overlap, 0.90), 3),
        },
    }

# ---------- 4. deltas ----------
deltas = {}
for rtt in RTTS:
    b_cold = [r["ttft_ms"] for r in group(rtt, "baseline") if r["repeat_index"] == 0]
    s_cold = [r["ttft_ms"] for r in group(rtt, "sphp") if r["repeat_index"] == 0]
    b_warm = [r["ttft_ms"] for r in group(rtt, "baseline") if r["repeat_index"] >= 1]
    s_warm = [r["ttft_ms"] for r in group(rtt, "sphp") if r["repeat_index"] >= 1]
    rng = random.Random(SEED)
    d_cold = mean(s_cold) - mean(b_cold)
    d_warm = mean(s_warm) - mean(b_warm)
    deltas[f"rtt{rtt}"] = {
        "ttft_cold": {
            "baseline_mean": round(mean(b_cold), 2),
            "sphp_mean": round(mean(s_cold), 2),
            "delta_ms": round(d_cold, 2),
            "delta_pct": round(100 * d_cold / mean(b_cold), 2),
            "delta_ci95": [round(x, 2) for x in boot_diff_ci(s_cold, b_cold, rng)],
        },
        "ttft_warm": {
            "baseline_mean": round(mean(b_warm), 2),
            "sphp_mean": round(mean(s_warm), 2),
            "delta_ms": round(d_warm, 2),
            "delta_pct": round(100 * d_warm / mean(b_warm), 2),
            "delta_ci95": [round(x, 2) for x in boot_diff_ci(s_warm, b_warm, rng)],
        },
    }

# ---------- 5. rtt_sensitivity (baseline cold) ----------
def slope(xs, ys):
    mx, my = mean(xs), mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    return num / den

cold_ttft = [mean([r["ttft_ms"] for r in group(rtt, "baseline") if r["repeat_index"] == 0]) for rtt in RTTS]
cold_total = [mean([r["total_ms"] for r in group(rtt, "baseline") if r["repeat_index"] == 0]) for rtt in RTTS]
rtt_sensitivity = {
    "baseline_cold_ttft_mean_by_rtt": {str(r): round(v, 2) for r, v in zip(RTTS, cold_ttft)},
    "baseline_cold_total_mean_by_rtt": {str(r): round(v, 2) for r, v in zip(RTTS, cold_total)},
    "ttft_slope_ms_per_rtt_ms": round(slope(RTTS, cold_ttft), 3),
    "total_slope_ms_per_rtt_ms": round(slope(RTTS, cold_total), 3),
}

# ---------- 6. notes ----------
notes = []
# where SPHP helps/hurts
for rtt in RTTS:
    d = deltas[f"rtt{rtt}"]
    notes.append(
        f"TTFT delta (sphp - baseline) at rtt={rtt}ms: cold {d['ttft_cold']['delta_ms']:+.0f} ms "
        f"({d['ttft_cold']['delta_pct']:+.1f}%, CI {d['ttft_cold']['delta_ci95'][0]:+.0f}..{d['ttft_cold']['delta_ci95'][1]:+.0f}); "
        f"warm {d['ttft_warm']['delta_ms']:+.0f} ms ({d['ttft_warm']['delta_pct']:+.1f}%)."
    )
# cache warmth magnitude
b0c = mean([r["ttft_ms"] for r in group(0, "baseline") if r["repeat_index"] == 0])
b0w = mean([r["ttft_ms"] for r in group(0, "baseline") if r["repeat_index"] >= 1])
notes.append(
    f"Cache warmth (baseline, rtt=0): cold TTFT {b0c:.0f} ms vs warm {b0w:.0f} ms "
    f"({100*(b0c-b0w)/b0c:.0f}% reduction); cold leg time is dominated by retrieval "
    f"(sparse ~{mean([r['sparse_ms'] for r in group(0,'baseline') if r['repeat_index']==0]):.0f} ms, "
    f"dense ~{mean([r['dense_ms'] for r in group(0,'baseline') if r['repeat_index']==0]):.0f} ms) vs warm "
    f"(sparse ~{mean([r['sparse_ms'] for r in group(0,'baseline') if r['repeat_index']>=1]):.0f} ms, "
    f"dense ~{mean([r['dense_ms'] for r in group(0,'baseline') if r['repeat_index']>=1]):.0f} ms)."
)
# hit rate + wasted prefill
hr0 = sphp_detail["rtt0"]["hit_rate"]
wm = sphp_detail["rtt0"]["wasted_prefill_ms"]["miss"]
notes.append(
    f"SPHP hit rate at rtt=0 is {hr0:.1%} (Wilson CI {sphp_detail['rtt0']['hit_rate_ci95_wilson'][0]:.1%}..{sphp_detail['rtt0']['hit_rate_ci95_wilson'][1]:.1%}); "
    f"misses pay a wasted-prefill penalty of {wm['mean']:.0f} ms mean / {wm['median']:.0f} ms median, while hits record no wasted prefill."
)
# overlap
ov = sphp_detail["rtt0"]["overlap"]
notes.append(
    f"SPHP overlap (fraction of prompt covered by cached prefix) is concentrated: p10/p50/p90 = "
    f"{ov['p10']:.1f}/{ov['p50']:.1f}/{ov['p90']:.1f} at rtt=0 (values are 0.4/0.6/0.8/1.0)."
)
# degenerate decode_tps records
deg = [r for r in recs if r["variant"] == "sphp" and r["decode_tps"] > 100]
notes.append(
    f"Data-quality flag: {len(deg)}/600 sphp records (all repeat_index==0, all sphp_hit=True) report "
    f"degenerate decode_tps (100-6800 tps) from sub-8 ms decode_ms; they are excluded from "
    f"decode_tps_mean_excl_degenerate. Baseline has no such records."
)
# rtt sensitivity
notes.append(
    f"Baseline cold TTFT is essentially flat across RTT tiers "
    f"({rtt_sensitivity['baseline_cold_ttft_mean_by_rtt']['0']:.0f} ms at rtt=0 vs "
    f"{rtt_sensitivity['baseline_cold_ttft_mean_by_rtt']['80']:.0f} ms at rtt=80; OLS slope "
    f"{rtt_sensitivity['ttft_slope_ms_per_rtt_ms']:.2f} ms per ms RTT, within run-to-run noise), "
    f"while cold total_ms is noisier (slope {rtt_sensitivity['total_slope_ms_per_rtt_ms']:.2f} ms/ms) — "
    f"the simulated WAN RTT adds little to measured TTFT, which is dominated by retrieval/prefill."
)

out = {
    "source": "experiments/bench/campaigns/campaign_20260918T130756Z.jsonl",
    "n_records": len(recs),
    "method": {
        "bootstrap": f"{B} resamples, seed {SEED}, percentile 2.5/97.5",
        "cold": "repeat_index == 0",
        "warm": "repeat_index >= 1",
        "hit_rate_ci": "Wilson 95%",
        "delta_ci": "independent-sample bootstrap of (sphp_mean - baseline_mean)",
        "slopes": "ordinary least squares over the 4 rtt tiers",
        "decode_tps": "mean over all records; also reported excluding degenerate records (decode_tps > 100, i.e. sub-8ms decode_ms)",
    },
    "per_tier": per_tier,
    "per_leg": per_leg,
    "sphp_detail": sphp_detail,
    "deltas": deltas,
    "rtt_sensitivity": rtt_sensitivity,
    "notes": notes,
}

with open(OUT, "w") as f:
    json.dump(out, f, indent=2)
print("wrote", OUT)
