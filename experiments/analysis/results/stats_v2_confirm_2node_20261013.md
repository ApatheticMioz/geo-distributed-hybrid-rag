# CI'd Headline Analysis — v2 2-node Confirmatory Re-baseline (2026-10-13)

**Inputs:** NEW `campaign_v2_confirm_2node_20261013.jsonl` (N=120/arm, 2880 records, 0 errors); OLD `campaign_20260918T130756Z.jsonl` (N=50/arm, 1200 records). Arms: P2-SPHP vs P2 (baseline), RTT tiers [0, 15, 40, 80] ms. Cold = repeat_index 0, warm = repeats ≥ 1 (strata, not replicates).

**Methods (pre-registered, design §7 / LR-3):** BCa bootstrap delta CI (B=1000, seed=42); one-sided permutation test (statistic = mean and median, n=10000 resamples, seed=42, +1 continuity correction); Cliff's δ and Cohen's d; Holm FWER for the PRIMARY (cold) family, BH FDR for the SECONDARY (warm) family; α = 0.05. Wilson 95% CIs for the SPHP hit rate; power audit per cell at the observed CVs (two-sample primary; paired ρ=0.8 counterfactual). Effect sizes (Cohen's d, Cliff's δ) are computed on the full samples (mean-based d) per cell, independent of which statistic (mean/median) the delta test targets.

**Pre-registration deviation notes:**
- Design §2 lists the H3 primary family as '4 one-sided permutation tests, Holm-adjusted', while §7's pre-registered battery prescribes the delta CI + permutation test on BOTH mean and median, and the dispatch runs the identical battery on both statistics. Holm is applied over the 8-test family (primary adjusted_p); the 4-test reading (mean only) is reported alongside as adjusted_p_4test_reading on each cold-mean cell. In this data both readings give identical verdicts: all 4 cold-mean cells have p = 1/10001, Holm(8) = 0.0008, Holm(4) = 0.0004, all < 0.05.
- Warm strata contribute 2 records per query (repeats 1-2), so the warm battery runs at n = 240/arm (new) and n = 100/arm (old); the frozen design N = 120/arm refers to distinct measured queries. The pre-registered §7 battery (repeats 1+) is run as-is.
- Old campaign: 3 SPHP records (rtt 0: query 280a3955 repeat 0, eba80711 repeat 1, f775d1e4 repeat 2) have sphp_hit=null and are excluded from the hit-rate denominators (n=147/150 at rtt 0; the NEW campaign has zero nulls, n=1440). Their valid TTFT values remain in the latency battery.

## NEW (N=120/arm)

**PRIMARY (cold, Holm)** — P2-SPHP vs P2, cold TTFT, 4 tiers × {mean, median}:

| Cell | Δ TTFT (ms) | BCa 95% CI | Rel. Δ [CI] | p (perm, one-sided less) | Adj. p | Effect (d, δ) | Verdict |
|---|---|---|---|---|---|---|---|
| rtt0_cold_ttft_mean | -398.4 | [-470.0, -333.5] | -24.05% [-28.37, -20.13] | 9.999e-05 | 0.0008 | d=-1.50, δ=-0.71 | **SIG** |
| rtt0_cold_ttft_median | -442.4 | [-504.7, -377.6] | -27.02% [-30.83, -23.06] | 9.999e-05 | 0.0008 | d=-1.50, δ=-0.71 | **SIG** |
| rtt15_cold_ttft_mean | -458.9 | [-529.1, -383.4] | -27.58% [-31.80, -23.05] | 9.999e-05 | 0.0008 | d=-1.57, δ=-0.74 | **SIG** |
| rtt15_cold_ttft_median | -494.6 | [-578.5, -419.9] | -29.87% [-34.93, -25.36] | 9.999e-05 | 0.0008 | d=-1.57, δ=-0.74 | **SIG** |
| rtt40_cold_ttft_mean | -406.9 | [-472.7, -335.4] | -23.66% [-27.48, -19.50] | 9.999e-05 | 0.0008 | d=-1.53, δ=-0.75 | **SIG** |
| rtt40_cold_ttft_median | -429.6 | [-486.6, -361.6] | -25.31% [-28.67, -21.31] | 9.999e-05 | 0.0008 | d=-1.53, δ=-0.75 | **SIG** |
| rtt80_cold_ttft_mean | -418.0 | [-506.7, -338.6] | -25.48% [-30.88, -20.64] | 9.999e-05 | 0.0008 | d=-1.29, δ=-0.65 | **SIG** |
| rtt80_cold_ttft_median | -412.9 | [-519.5, -312.8] | -25.28% [-31.80, -19.15] | 9.999e-05 | 0.0008 | d=-1.29, δ=-0.65 | **SIG** |

**SECONDARY (warm, BH)** — P2-SPHP vs P2, warm TTFT, 4 tiers × {mean, median}:

| Cell | Δ TTFT (ms) | BCa 95% CI | Rel. Δ [CI] | p (perm, one-sided less) | Adj. p | Effect (d, δ) | Verdict |
|---|---|---|---|---|---|---|---|
| rtt0_warm_ttft_mean | -21.9 | [-52.9, 11.2] | -4.30% [-10.38, +2.19] | 0.09329 | 0.3732 | d=-0.12, δ=-0.12 | ns |
| rtt0_warm_ttft_median | 10.8 | [-110.9, 120.5] | +2.30% [-23.56, +25.61] | 0.5596 | 0.6684 | d=-0.12, δ=-0.12 | ns |
| rtt15_warm_ttft_mean | -7.7 | [-42.9, 25.8] | -1.45% [-8.07, +4.85] | 0.3299 | 0.6003 | d=-0.04, δ=-0.11 | ns |
| rtt15_warm_ttft_median | 27.6 | [-93.0, 112.6] | +5.51% [-18.59, +22.51] | 0.6684 | 0.6684 | d=-0.04, δ=-0.11 | ns |
| rtt40_warm_ttft_mean | -24.1 | [-56.0, 10.0] | -4.29% [-9.99, +1.78] | 0.08139 | 0.3732 | d=-0.13, δ=-0.13 | ns |
| rtt40_warm_ttft_median | -19.2 | [-146.8, 102.0] | -3.57% [-27.25, +18.94] | 0.3752 | 0.6003 | d=-0.13, δ=-0.13 | ns |
| rtt80_warm_ttft_mean | -7.9 | [-39.2, 27.1] | -1.34% [-6.67, +4.62] | 0.3184 | 0.6003 | d=-0.04, δ=-0.10 | ns |
| rtt80_warm_ttft_median | 26.6 | [-87.6, 127.7] | +4.79% [-15.79, +23.02] | 0.6052 | 0.6684 | d=-0.04, δ=-0.10 | ns |

## OLD (N=50/arm)

**Old cold (Holm, same battery)** — P2-SPHP vs P2, cold TTFT, 4 tiers × {mean, median}:

| Cell | Δ TTFT (ms) | BCa 95% CI | Rel. Δ [CI] | p (perm, one-sided less) | Adj. p | Effect (d, δ) | Verdict |
|---|---|---|---|---|---|---|---|
| rtt0_cold_ttft_mean | -685.4 | [-934.9, -450.7] | -18.86% [-25.73, -12.40] | 9.999e-05 | 0.0008 | d=-1.13, δ=-0.61 | **SIG** |
| rtt0_cold_ttft_median | -768.1 | [-981.9, -500.1] | -20.90% [-26.72, -13.61] | 9.999e-05 | 0.0008 | d=-1.13, δ=-0.61 | **SIG** |
| rtt15_cold_ttft_mean | -677.6 | [-876.2, -469.6] | -18.98% [-24.54, -13.15] | 9.999e-05 | 0.0008 | d=-1.26, δ=-0.63 | **SIG** |
| rtt15_cold_ttft_median | -714.5 | [-935.4, -486.8] | -19.79% [-25.91, -13.49] | 9.999e-05 | 0.0008 | d=-1.26, δ=-0.63 | **SIG** |
| rtt40_cold_ttft_mean | -558.9 | [-776.6, -337.5] | -15.72% [-21.84, -9.49] | 9.999e-05 | 0.0008 | d=-1.00, δ=-0.55 | **SIG** |
| rtt40_cold_ttft_median | -457.3 | [-685.1, -250.9] | -12.98% [-19.44, -7.12] | 9.999e-05 | 0.0008 | d=-1.00, δ=-0.55 | **SIG** |
| rtt80_cold_ttft_mean | -562.5 | [-812.5, -314.2] | -15.87% [-22.92, -8.86] | 9.999e-05 | 0.0008 | d=-0.92, δ=-0.51 | **SIG** |
| rtt80_cold_ttft_median | -579.6 | [-871.9, -192.0] | -16.47% [-24.77, -5.46] | 0.0002 | 0.0008 | d=-0.92, δ=-0.51 | **SIG** |

**Old warm (BH, same battery)** — P2-SPHP vs P2, warm TTFT, 4 tiers × {mean, median}:

| Cell | Δ TTFT (ms) | BCa 95% CI | Rel. Δ [CI] | p (perm, one-sided less) | Adj. p | Effect (d, δ) | Verdict |
|---|---|---|---|---|---|---|---|
| rtt0_warm_ttft_mean | -79.0 | [-127.1, -28.9] | -9.66% [-15.54, -3.53] | 0.0005 | 0.0032 | d=-0.44, δ=-0.29 | **SIG** |
| rtt0_warm_ttft_median | -92.8 | [-147.3, -29.1] | -11.35% [-18.02, -3.56] | 0.0007999 | 0.0032 | d=-0.44, δ=-0.29 | **SIG** |
| rtt15_warm_ttft_mean | -37.7 | [-79.9, 10.7] | -4.62% [-9.80, +1.31] | 0.05249 | 0.105 | d=-0.23, δ=-0.20 | ns |
| rtt15_warm_ttft_median | -69.1 | [-132.4, -4.4] | -8.46% [-16.21, -0.54] | 0.005899 | 0.01573 | d=-0.23, δ=-0.20 | **SIG** |
| rtt40_warm_ttft_mean | 25.1 | [-26.1, 75.9] | +3.07% [-3.19, +9.29] | 0.8307 | 0.8307 | d=+0.13, δ=+0.01 | ns |
| rtt40_warm_ttft_median | -8.4 | [-66.7, 48.7] | -1.04% [-8.29, +6.06] | 0.4224 | 0.5631 | d=+0.13, δ=+0.01 | ns |
| rtt80_warm_ttft_mean | 21.8 | [-28.6, 68.2] | +2.49% [-3.27, +7.79] | 0.7994 | 0.8307 | d=+0.12, δ=-0.03 | ns |
| rtt80_warm_ttft_median | -4.0 | [-48.2, 36.8] | -0.47% [-5.66, +4.32] | 0.416 | 0.5631 | d=+0.12, δ=-0.03 | ns |

## Old (N=50) vs New (N=120) — powered re-answer

Same battery, same seeds; warm family is the open v1 finding (3 of 4 tiers non-significant at N=50).

| Cell | p old | adj. p old | old verdict | p new | adj. p new | new verdict | Δ rel. (old→new) |
|---|---|---|---|---|---|---|---|
| rtt0_warm_ttft_mean | 0.0005 | 0.0032 | **SIG** | 0.09329 | 0.3732 | ns | -9.7% → -4.3% |
| rtt0_warm_ttft_median | 0.0007999 | 0.0032 | **SIG** | 0.5596 | 0.6684 | ns | -11.3% → +2.3% |
| rtt15_warm_ttft_mean | 0.05249 | 0.105 | ns | 0.3299 | 0.6003 | ns | -4.6% → -1.4% |
| rtt15_warm_ttft_median | 0.005899 | 0.01573 | **SIG** | 0.6684 | 0.6684 | ns | -8.5% → +5.5% |
| rtt40_warm_ttft_mean | 0.8307 | 0.8307 | ns | 0.08139 | 0.3732 | ns | +3.1% → -4.3% |
| rtt40_warm_ttft_median | 0.4224 | 0.5631 | ns | 0.3752 | 0.6003 | ns | -1.0% → -3.6% |
| rtt80_warm_ttft_mean | 0.7994 | 0.8307 | ns | 0.3184 | 0.6003 | ns | +2.5% → -1.3% |
| rtt80_warm_ttft_median | 0.416 | 0.5631 | ns | 0.6052 | 0.6684 | ns | -0.5% → +4.8% |

Cold family (for reference — both campaigns):

| Cell | adj. p old | adj. p new |
|---|---|---|
| rtt0_cold_ttft_mean | 0.0008 | 0.0008 |
| rtt0_cold_ttft_median | 0.0008 | 0.0008 |
| rtt15_cold_ttft_mean | 0.0008 | 0.0008 |
| rtt15_cold_ttft_median | 0.0008 | 0.0008 |
| rtt40_cold_ttft_mean | 0.0008 | 0.0008 |
| rtt40_cold_ttft_median | 0.0008 | 0.0008 |
| rtt80_cold_ttft_mean | 0.0008 | 0.0008 |
| rtt80_cold_ttft_median | 0.0008 | 0.0008 |

## SPHP hit rate (Wilson 95% CI) & prefix overlap

**NEW:** overall 1296/1440 = 0.900 [0.883, 0.914]

| Tier | New hits/n | New rate [CI] | Old hits/n | Old rate [CI] |
|---|---|---|---|---|
| rtt 0 | 327/360 | 0.908 [0.874, 0.934] | 120/147 | 0.816 [0.746, 0.871] |
| rtt 15 | 321/360 | 0.892 [0.855, 0.920] | 135/150 | 0.900 [0.842, 0.939] |
| rtt 40 | 330/360 | 0.917 [0.883, 0.941] | 126/150 | 0.840 [0.773, 0.890] |
| rtt 80 | 318/360 | 0.883 [0.846, 0.912] | 123/150 | 0.820 [0.751, 0.873] |
| stratum cold | 432/480 | 0.900 [0.870, 0.924] | 168/199 | 0.844 [0.787, 0.888] |
| stratum warm | 864/960 | 0.900 [0.879, 0.917] | 336/398 | 0.844 [0.805, 0.876] |


Old denominators exclude 3 SPHP records at rtt 0 with `sphp_hit=null` (see deviation notes).

**Mean prefix overlap (new):** 0.636 [0.629, 0.643] over 1440 records (cold 0.636 [n=480]; warm 0.636 [n=960]). Old: 0.624 [n=597].

## Power audit (observed CVs, per cell)

Two-sample (the actual design; control-arm CV) and paired ρ=0.8 counterfactual (design §4.2). MDE = minimum detectable relative delta at α=0.05 / power 0.80; power-at-observed = achieved power at the cell's observed relative delta.

Notes: warm cells run at n = 240/arm (new) / 100/arm (old) — 2 warm repeats per query pooled, per the §7 'repeats 1+' definition. Observed CVs are of the warm *pooled* strata and run above the design's per-query CV inputs (0.209–0.282) because pooling across queries adds between-query variance — the design's §4.2 power table remains valid for per-query effects. For cells where the observed direction is adverse (sphp > baseline, e.g. rtt15/rtt80 warm medians), power-at-observed is reported on the magnitude of the observed |delta| (the direction itself is the null result).

| Cell | n/arm | CV (base) | MDE 2-samp | Power@obs 2-samp | Power@10% 2-samp | MDE paired | Power@obs paired |
|---|---|---|---|---|---|---|---|
| old rtt0_cold_ttft_mean | 50 | 0.1283 | 0.072 | 1.000 | 0.974 | 0.032 | 1.000 |
| old rtt0_warm_ttft_mean | 100 | 0.1789 | 0.071 | 0.969 | 0.977 | 0.032 | 1.000 |
| old rtt0_cold_ttft_median | 50 | 0.1283 | 0.072 | 1.000 | 0.974 | 0.032 | 1.000 |
| old rtt0_warm_ttft_median | 100 | 0.1789 | 0.071 | 0.994 | 0.977 | 0.032 | 1.000 |
| old rtt15_cold_ttft_mean | 50 | 0.139 | 0.078 | 1.000 | 0.949 | 0.035 | 1.000 |
| old rtt15_warm_ttft_mean | 100 | 0.1599 | 0.063 | 0.533 | 0.993 | 0.028 | 0.995 |
| old rtt15_cold_ttft_median | 50 | 0.139 | 0.078 | 1.000 | 0.949 | 0.035 | 1.000 |
| old rtt15_warm_ttft_median | 100 | 0.1599 | 0.063 | 0.963 | 0.993 | 0.028 | 1.000 |
| old rtt40_cold_ttft_mean | 50 | 0.1376 | 0.077 | 1.000 | 0.953 | 0.035 | 1.000 |
| old rtt40_warm_ttft_mean | 100 | 0.178 | 0.070 | 0.230 | 0.978 | 0.032 | 0.779 |
| old rtt40_cold_ttft_median | 50 | 0.1376 | 0.077 | 0.997 | 0.953 | 0.035 | 1.000 |
| old rtt40_warm_ttft_median | 100 | 0.178 | 0.070 | 0.060 | 0.978 | 0.032 | 0.147 |
| old rtt80_cold_ttft_mean | 50 | 0.1835 | 0.103 | 0.991 | 0.778 | 0.046 | 1.000 |
| old rtt80_warm_ttft_mean | 100 | 0.1456 | 0.058 | 0.227 | 0.998 | 0.026 | 0.772 |
| old rtt80_cold_ttft_median | 50 | 0.1835 | 0.103 | 0.994 | 0.778 | 0.046 | 1.000 |
| old rtt80_warm_ttft_median | 100 | 0.1456 | 0.058 | 0.041 | 0.998 | 0.026 | 0.071 |
| new rtt0_cold_ttft_mean | 120 | 0.1523 | 0.055 | 1.000 | 0.999 | 0.025 | 1.000 |
| new rtt0_warm_ttft_mean | 240 | 0.3477 | 0.089 | 0.273 | 0.883 | 0.040 | 0.858 |
| new rtt0_cold_ttft_median | 120 | 0.1523 | 0.055 | 1.000 | 0.999 | 0.025 | 1.000 |
| new rtt0_warm_ttft_median | 240 | 0.3477 | 0.089 | 0.098 | 0.883 | 0.040 | 0.322 |
| new rtt15_cold_ttft_mean | 120 | 0.1515 | 0.055 | 1.000 | 0.999 | 0.025 | 1.000 |
| new rtt15_warm_ttft_mean | 240 | 0.3288 | 0.084 | 0.070 | 0.915 | 0.038 | 0.190 |
| new rtt15_cold_ttft_median | 120 | 0.1515 | 0.055 | 1.000 | 0.999 | 0.025 | 1.000 |
| new rtt15_warm_ttft_median | 240 | 0.3288 | 0.084 | 0.408 | 0.915 | 0.038 | 0.971 |
| new rtt40_cold_ttft_mean | 120 | 0.1548 | 0.056 | 1.000 | 0.999 | 0.025 | 1.000 |
| new rtt40_warm_ttft_mean | 240 | 0.3176 | 0.081 | 0.315 | 0.932 | 0.036 | 0.911 |
| new rtt40_cold_ttft_median | 120 | 0.1548 | 0.056 | 1.000 | 0.999 | 0.025 | 1.000 |
| new rtt40_warm_ttft_median | 240 | 0.3176 | 0.081 | 0.219 | 0.932 | 0.036 | 0.754 |
| new rtt80_cold_ttft_mean | 120 | 0.1743 | 0.063 | 1.000 | 0.994 | 0.028 | 1.000 |
| new rtt80_warm_ttft_mean | 240 | 0.2883 | 0.074 | 0.073 | 0.967 | 0.033 | 0.205 |
| new rtt80_cold_ttft_median | 120 | 0.1743 | 0.063 | 1.000 | 0.994 | 0.028 | 1.000 |
| new rtt80_warm_ttft_median | 240 | 0.2883 | 0.074 | 0.404 | 0.967 | 0.033 | 0.970 |

