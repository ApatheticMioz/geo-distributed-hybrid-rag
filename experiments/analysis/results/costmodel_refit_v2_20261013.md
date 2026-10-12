# Cost-model refit + residual diagnostics on the v2 confirmatory re-baseline (AB-5)

New data: `campaign_v2_confirm_2node_20261013.jsonl` (N=2880, 0 errors); like-for-like old data: `campaign_20260918T130756Z.jsonl` (N=1200). Protocol: leave-one-tier-out, regime (tier-mean) level.


## 1. Per-term parameters (old vs new)

| term | old (09-18) | new (10-13) |
|---|---|---|
| cold t_sparse_base_ms | 1711.294 | 603.152 |
| cold t_dense_base_ms | 2680.853 | 1091.668 |
| cold t_fusion_base_ms | 0.018 | 0.016 |
| cold t_prefill_base_ms | 850.713 | 568.135 |
| cold decode_tok_per_sec | 35.56 | 35.78 |
| cold sphp_hit_rate | 0.84 | 0.9 |
| cold t_sphp_miss_penalty_ms | 2697.8 | 1076.6 |
| warm t_sparse_base_ms | 74.803 | 79.197 |
| warm t_dense_base_ms | 190.803 | 113.958 |
| warm t_fusion_base_ms | 0.019 | 0.015 |
| warm t_prefill_base_ms | 633.275 | 423.608 |
| warm decode_tok_per_sec | 35.18 | 35.26 |
| warm sphp_hit_rate | 0.84 | 0.9 |
| warm t_sphp_miss_penalty_ms | 193.6 | 124.5 |


### Hydration term (linearity checks, new data)

- **cold**: mean hydration residual -16.88 ms (std 86.56), t_per_doc=-3.375 ms/doc (5-doc fit); token-count slope 0.99148 ms/tok (R^2=-0.0636); top_k=[10].
- **warm**: mean hydration residual -16.87 ms (std 149.08), t_per_doc=-3.375 ms/doc (5-doc fit); token-count slope -2.45176 ms/tok (R^2=-0.243); top_k=[10].


### SPHP hit/miss (new vs old)

- **cold** new: hit=0.9 ([0.8699, 0.9237]), Tmiss=1076.6 ms; old: hit=0.84, Tmiss=2697.8 ms.
- **warm** new: hit=0.9 ([0.8794, 0.9174]), Tmiss=124.5 ms; old: hit=0.84, Tmiss=193.6 ms.


## 2. Goodness (regime LOTO MAPE)

| fit | P2 cold | P2 warm | SPHP cold | SPHP warm |
|---|---|---|---|---|
| old-fit-on-old | 1.28 | 2.76 | 10.55 | 5.81 |
| refit-on-new | 2.15 | 4.13 | 7.83 | 4.37 |
| **old-fit-on-new (TRANSFER)** | 114.02 | 58.46 | — | — |

> The transfer row (old fit applied to the new surface) is the cost-surface shift induced by the dense-leg fix -- this is the number the testbed-disclosure text needs.


## 3. Crossover + policy

- New SPHP advantage (RTT×BW sweep): min 2.84%, max 3.35%; argmin counts {'P2-SPHP': 35, 'P2': 0}.
- Old SPHP advantage: min 7.0%, max 7.83%.

- **warm** argmin demo (predicted vs realized warm TTFT of the selected variant), pooled abs error = **5.57%**:
  - rtt 0.0: selected P2-SPHP, pred 543.7 ms vs realized 488.1 ms (11.4%, n=240)
  - rtt 15.0: selected P2-SPHP, pred 551.2 ms vs realized 524.0 ms (5.2%, n=240)
  - rtt 40.0: selected P2-SPHP, pred 563.7 ms vs realized 536.8 ms (5.01%, n=240)
  - rtt 80.0: selected P2-SPHP, pred 583.7 ms vs realized 579.8 ms (0.68%, n=240)

- **cold** argmin demo (predicted vs realized cold TTFT of the selected variant), pooled abs error = **9.72%**:
  - rtt 0.0: selected P2-SPHP, pred 1352.8 ms vs realized 1258.4 ms (7.5%, n=120)
  - rtt 15.0: selected P2-SPHP, pred 1360.3 ms vs realized 1204.9 ms (12.9%, n=120)
  - rtt 40.0: selected P2-SPHP, pred 1372.8 ms vs realized 1313.0 ms (4.55%, n=120)
  - rtt 80.0: selected P2-SPHP, pred 1392.8 ms vs realized 1222.5 ms (13.93%, n=120)

> Published warm error was 4.5–8.6% (mean ~5.5%). The refit-on-new warm number is the like-for-like comparison.


## 4. Residual diagnostics


### refit on new cold
- P2: {"n": 480, "mean_resid_ms": 31.44, "std_resid_ms": 265.77, "bias_pct_of_obs": 4.36, "mape_pct": 12.41, "trend_resid_on_rtt_pct_per_ms": 0.0508, "trend_resid_on_tok_pct_per_tok": -0.168225, "curvature_resid_on_prefill_r2": -2.5152, "curvature_resid_on_tok_r2": 0.0082}
- P2SPHP overall: {"n": 480, "mean_resid_ms": 119.99, "std_resid_ms": 311.06, "bias_pct_of_obs": 16.994, "mape_pct": 25.45, "trend_resid_on_rtt_pct_per_ms": 0.0947, "trend_resid_on_tok_pct_per_tok": -0.045996, "curvature_resid_on_prefill_r2": -0.0041, "curvature_resid_on_tok_r2": 0.0002}
- P2 per-tier mean residual (ms): {'0': 28.1, '15': 28.5, '40': -15.1, '80': 84.3}

### refit on new warm
- P2: {"n": 960, "mean_resid_ms": 31.9, "std_resid_ms": 175.22, "bias_pct_of_obs": 17.978, "mape_pct": 35.38, "trend_resid_on_rtt_pct_per_ms": -0.1621, "trend_resid_on_tok_pct_per_tok": 0.631094, "curvature_resid_on_prefill_r2": -6.1099, "curvature_resid_on_tok_r2": 0.0557}
- P2SPHP overall: {"n": 960, "mean_resid_ms": 28.44, "std_resid_ms": 200.52, "bias_pct_of_obs": 20.67, "mape_pct": 37.48, "trend_resid_on_rtt_pct_per_ms": -0.1836, "trend_resid_on_tok_pct_per_tok": 0.509624, "curvature_resid_on_prefill_r2": -3.2108, "curvature_resid_on_tok_r2": 0.0146}
- P2 per-tier mean residual (ms): {'0': 52.6, '15': 38.4, '40': 21.7, '80': 14.9}

### old fit on new cold
- P2: {"n": 480, "mean_resid_ms": 1903.2, "std_resid_ms": 265.77, "bias_pct_of_obs": 119.143, "mape_pct": 119.14, "trend_resid_on_rtt_pct_per_ms": 0.0725, "trend_resid_on_tok_pct_per_tok": -0.343999, "curvature_resid_on_prefill_r2": -2.7179, "curvature_resid_on_tok_r2": 0.008}
- P2SPHP overall: {"n": 480, "mean_resid_ms": 2019.82, "std_resid_ms": 311.06, "bias_pct_of_obs": 179.249, "mape_pct": 179.25, "trend_resid_on_rtt_pct_per_ms": 0.1655, "trend_resid_on_tok_pct_per_tok": -0.119783, "curvature_resid_on_prefill_r2": -0.0042, "curvature_resid_on_tok_r2": 0.0002}
- P2 per-tier mean residual (ms): {'0': 1899.8, '15': 1900.3, '40': 1856.6, '80': 1956.1}

### old fit on new warm
- P2: {"n": 960, "mean_resid_ms": 318.41, "std_resid_ms": 175.22, "bias_pct_of_obs": 76.412, "mape_pct": 76.57, "trend_resid_on_rtt_pct_per_ms": -0.2914, "trend_resid_on_tok_pct_per_tok": 0.951705, "curvature_resid_on_prefill_r2": -6.1147, "curvature_resid_on_tok_r2": 0.0561}
- P2SPHP overall: {"n": 960, "mean_resid_ms": 267.31, "std_resid_ms": 200.52, "bias_pct_of_obs": 72.187, "mape_pct": 74.84, "trend_resid_on_rtt_pct_per_ms": -0.3065, "trend_resid_on_tok_pct_per_tok": 0.725649, "curvature_resid_on_prefill_r2": -3.2075, "curvature_resid_on_tok_r2": 0.0145}
- P2 per-tier mean residual (ms): {'0': 339.1, '15': 324.9, '40': 308.2, '80': 301.5}

- SPHP branch check (new): cold={"hit_branch": {"n": 432, "mape_pct": 8.67}, "miss_branch": {"n": 48, "mape_pct": 58.01}, "note": "branch-conditional form check with per-record measured components"} warm={"hit_branch": {"n": 864, "mape_pct": 32.8}, "miss_branch": {"n": 96, "mape_pct": 21.86}, "note": "branch-conditional form check with per-record measured components"}

### SPHP form-error extras (new)

- **cold** realized miss penalty 86.2 ms (per-tier {'0': -2.0, '15': 49.8, '40': 138.7, '80': 158.4}) vs recorded wasted_prefill 1076.6 ms -> gap -990.4 ms.
- **cold** SPHP speedup: MEASURED 25.18% vs MODELED 19.48% (the gap is the KV-prefix credit the closed form does not encode).
- **warm** realized miss penalty 296.1 ms (per-tier {'0': 263.3, '15': 327.9, '40': 288.8, '80': 304.3}) vs recorded wasted_prefill 124.5 ms -> gap 171.6 ms.
- **warm** SPHP speedup: MEASURED 2.81% vs MODELED 3.24% (the gap is the KV-prefix credit the closed form does not encode).

*Systematic-pattern reading: a nonzero `trend_*` or high `curvature_*` R^2 flags scale/RTT dependence of the residuals; per-tier mean residual drift flags a regime interaction. Any such pattern becomes a 'proposed' entry in docs/revision_log.md (no model change made here).*

