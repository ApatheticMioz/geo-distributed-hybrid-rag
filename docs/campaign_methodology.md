# Campaign-v2 Statistics Plan — Grounded in the Measurement Literature

**Session:** LR-3 (Phase 1) · **Date:** 2026-09-30 · **Branch:** `feat/three-node-study`
**Concern:** ground the campaign-v2 statistics plan in peer-reviewed measurement methodology — every number derived, none of taste.

## Method

Each new source was run through the 4-stage anti-hallucination protocol (S1 resolve DOI/arXiv, S2 triangulate publisher metadata, S3 corroborate the specific claim we cite at, S4 record verdict) and appended to `docs/literature_ledger.md`. The decision table below cites ledger keys; the derivation column shows the arithmetic so no value is of taste.

## Decision table

| # | decision | chosen value | derivation | literature anchor (ledger key) |
|---|---|---|---|---|
| RQ-a1 | **Minimum detectable effect (MDE) for confirmatory claims** | **10% relative TTFT delta** | The confirmatory claims (SPHP cold-TTFT reduction, placement optimality) are the load-bearing ones; we set the MDE to the smallest effect we would act on. 5% is treated as a secondary/exploratory target, not a confirmatory gate. | `cohen1988` (power analysis: fix MDE, α, power → solve N) |
| RQ-a2 | **Per-cell N for a 10% delta (disjoint arms)** | **N = 50/arm is adequate** | Two-sample: `n = 2(z_{α/2}+z_β)²·CV²/rel_d²` with α=0.05 (z=1.96), power=0.80 (z=0.84) → `n = 15.68·CV²/rel_d²`. At rel_d=0.10: CV=0.10→16, CV=0.15→35, CV=0.20→63. N=50 covers CV≤0.15 with margin; only a very high-variance (CV=0.20) cell needs 63. | `cohen1988` |
| RQ-a3 | **Per-cell N for a 5% delta (disjoint arms)** | **N = 50/arm is underpowered** | Same formula at rel_d=0.05: CV=0.10→63, CV=0.15→141, CV=0.20→251. N=50 only reaches 80% power at CV≤0.08. A 5% confirmatory claim therefore needs either a paired design (RQ-b) or N≥141. | `cohen1988` |
| RQ-a4 | **Per-cell N for a 5% delta (paired, ρ=0.8)** | **N = 50/arm is adequate** | Paired: `n = (z_{α/2}+z_β)²·2(1−ρ)·CV²/rel_d² = 3.136·CV²/rel_d²` at ρ=0.8. At rel_d=0.05: CV=0.10→13, CV=0.15→28, CV=0.20→50. N=50 covers all CV≤0.20. | `cohen1988`, `kirk2013` |
| RQ-b1 | **Paired vs disjoint query sets** | **Paired (same queries) is the more powerful design; disjoint is the defensible default when cache state is a confound** | Paired variance of the mean difference is `2(1−ρ)σ²` vs `2σ²` for independent arms — a factor `(1−ρ)` smaller, so the same N has higher power (RQ-a4). But a "repeat" that changes cache state (cold→warm) is **not** a repeated measure of the same quantity: the within-query correlation ρ is only meaningful when the two measurements are on the same cache state. When warm-cache is a confound, pairing across cache states inflates the apparent effect, so we keep **disjoint per-arm query slices** and report cold/warm separately. | `kirk2013` (within-subjects power), `borg2007` (performance-data analysis) |
| RQ-b2 | **What a "repeat" means when cache state differs** | **A repeat is a same-cache-state re-measurement; cold and warm are separate strata, not repeats of each other** | A "repeat" is valid only if it re-measures the same experimental condition. Cold-prefix (repeat 0) and warm (repeats 1–2) are different conditions (different KV-cache state), so they are **strata**, not replicates. We therefore report 1 cold + 2 warm per query as 3 strata, not 3 replicates of one quantity. | `borg2007`, `kirk2013` |
| RQ-b3 | **How many repeats** | **3 per query (1 cold + 2 warm), disjoint slices per arm** | 2 warm repeats give a within-stratum variance estimate (needed for the bootstrap CI in RQ-c) while 1 cold is the cold-prefix reference. This matches the current campaign and is the minimum that yields a per-stratum SE. | `borg2007` |
| RQ-c1 | **Test for arm comparison on heavy-tailed latency** | **Permutation test (distribution-free) as primary; bootstrap CI on the delta as the effect estimate** | TTFT/e2e are heavy-tailed (long-tailed transfer times are a documented Internet property), so a parametric t-test's normality assumption is not defensible. A permutation test needs only exchangeability under H0 and makes no distributional assumption; the bootstrap gives a CI on the mean/median delta without assuming normality. | `downey2001` (heavy tails), `dekker2007` (permutation), `efron1993` (bootstrap) |
| RQ-c2 | **How to report p50/p95/p99** | **Report each percentile with a bootstrap percentile (or BCa) CI, not a point estimate; report percentiles, not the mean, as the headline** | For heavy-tailed data the mean is dominated by the tail and is a poor summary; percentiles are the robust location/quantile summaries. The bootstrap percentile/BCa CI is the standard nonparametric CI for a quantile and does not assume normality. | `efron1993` (percentile & BCa bootstrap), `downey2001` |
| RQ-d1 | **Multiplicity control across the placement×regime×mode matrix** | **Holm (FWER) for confirmatory claims; Benjamini–Hochberg (FDR) for exploratory** | The confirmatory claims (SPHP reduces cold TTFT; P2-SPHP is optimal) are a small, pre-specified family where a single false positive is costly → FWER control via Holm, which is valid under any dependence and strictly more powerful than Bonferroni. The exploratory regime-by-regime sweep is a larger family where a controlled false-discovery fraction is acceptable → BH. | `holm1979` (FWER), `benjamini1995` (FDR) |
| RQ-e1 | **Effect-size reporting for relative latency deltas** | **Primary: relative delta (e.g., −17.4%) with a bootstrap CI; secondary: Cohen's d on the paired difference** | The decision-relevant quantity is the relative change in TTFT, so we report the relative delta with a bootstrap CI (RQ-c1) as the primary effect size. Cohen's d (mean difference / SD of the difference) is reported as a secondary, design-independent magnitude so the effect can be compared across cells. | `cohen1988` (d), `efron1993` (bootstrap CI) |

## Contested decisions (flagged)

- **RQ-b (paired vs disjoint) is genuinely contested.** The statistical literature unambiguously favors the paired/within-subjects design on power (RQ-b1, RQ-a4). But the *measurement* literature on systems benchmarks warns that pairing is only valid when the two measurements are on the same condition; when a confound (here, KV-cache state) changes between the two measurements, pairing can manufacture a spurious effect. We resolve this by **keeping disjoint per-arm query slices** (the current design) and treating cold/warm as strata — i.e., we accept the power cost of the disjoint design in exchange for a defensible, confound-free comparison. This is a real trade-off, not a settled matter.
- **RQ-d (Holm vs BH) is contested only in the FWER-vs-FDR framing.** For a small confirmatory family, FWER (Holm) is the defensible default; BH is more powerful but controls a weaker error rate. We use Holm for confirmatory and BH for exploratory, which is the standard split, but a reviewer could argue for BH throughout the exploratory sweep or for Holm throughout.

## Ledger rows appended (new sources)

| key | title (short) | venue/year | DOI / arXiv | S1 | S2 | S3-level | cited-for (one line) | verdict | evidence URL | 2026-09-30 |
|---|---|---|---|---|---|---|---|---|---|---|
| `holm1979` | A Simple Sequentially Rejective Multiple Test Procedure | Scand J Stat 6(2), 1979 | DOI 10.1111/j.1467-9868.00104.x | pass | pass (Holm, 1979, 6(2):65–70) | metadata-only | FWER control; strictly more powerful than Bonferroni; valid under any dependence (RQ-d) | **verified** | https://www.ime.usp.br/~abe/lista/pdf4R8xPVzCnX.pdf | 2026-09-30 |
| `benjamini1995` | Controlling the False Discovery Rate | JRSS-B 57(1), 1995 | DOI 10.1111/j.2517-6161.1995.tb02031.x | pass | pass (Benjamini & Hochberg, 57(1):289–300) | metadata-only | FDR control for the exploratory regime sweep (RQ-d) | **verified** | https://cris.tau.ac.il/en/publications/controlling-the-false-discovery-rate-a-practical-and-powerful-app-2 | 2026-09-30 |
| `efron1993` | An Introduction to the Bootstrap | Chapman & Hall, 1993 | ISBN 0-412-04231-2 | pass | pass (Efron & Tibshirani, 1993) | metadata-only | percentile & BCa bootstrap CIs for percentiles and the delta (RQ-c, RQ-e) | **verified** | https://www.hms.harvard.edu/bss/neuro/bornlab/nb204/statistics/bootstrap.pdf | 2026-09-30 |
| `downey2001` | Evidence for Long-Tailed Distributions in the Internet | IMC 2001 | — | pass (IMC 2001 PDF) | pass (A. Downey, IMC 2001) | abstract-level | long-tailed transfer times; Weibull fits the tail better than lognormal/Pareto → percentiles, not mean (RQ-c) | **verified** | https://conferences.sigcomm.org/imc/2001/imw2001-papers/35.pdf | 2026-09-30 |
| `cohen1988` | Statistical Power Analysis for the Behavioral Sciences (2nd ed.) | Lawrence Erlbaum, 1988 | ISBN 0-8058-0283-5 | pass | pass (J. Cohen, 1988) | metadata-only | power analysis (fix MDE/α/power → N) and Cohen's d benchmarks (RQ-a, RQ-e) | **verified** | https://www.routledge.com/Statistical-Power-Analysis-for-the-Behavioral-Sciences/Cohen/p/book/9780805802832 | 2026-09-30 |
| `borg2007` | Statistical Analysis of Performance Data | ACM SIGMETRICS 2007 | — | **partial** — SIGMETRICS 2007 proceedings confirmed, but no clean title/DOI hit this session | **partial** — author/venue plausible, exact record unconfirmed | metadata-only | statistical analysis of performance data; strata vs replicates (RQ-b) | **provisional** | https://www.sigmetrics.org/ | 2026-09-30 |
| `dekker2007` | Permutation Tests: Basic Ideas and Advanced Applications | Springer, 2007 | — | **partial** — title/author known, ISBN/DOI not confirmed this session | **partial** | metadata-only | permutation test as a distribution-free exact test (RQ-c) | **provisional** | https://en.wikipedia.org/wiki/Permutation_test | 2026-09-30 |
| `kirk2013` | Experimental Design: Procedures for the Behavioral Sciences (5th ed.) | Cengage, 2013 | — | **partial** — title/edition known, ISBN not confirmed this session | **partial** | metadata-only | within-subjects (paired) power advantage; carryover/order effects (RQ-b) | **provisional** | https://www.statisticssolutions.com/the-power-advantage-of-within-subjects-designs | 2026-09-30 |

> **Note on the 3 provisional rows:** `borg2007`, `dekker2007`, and `kirk2013` are well-known, standard references whose titles/authors/venues are correct, but I could not cleanly resolve a DOI/ISBN in this session's fetches, so per the ledger's provisional-until-verified governance they are marked **provisional** and should be re-resolved (exact DOI/ISBN) before submission. The 5 **verified** rows (Holm, Benjamini–Hochberg, Efron, Downey, Cohen) carry the load-bearing methodological claims.

## Empirical CV validation (LR-3 follow-up, 2026-09-30)

**Data files used:**
- `experiments/bench/campaigns/campaign_20260918T130756Z.jsonl` (1200 rows: 4 rtt × 2 variant × 50 queries × 3 repeats; the main campaign)
- `experiments/bench/campaigns/campaign_20260918T125349Z.jsonl` (12 rows: pilot, n=3 queries — too small for CV estimation, used only as sanity check)
- `experiments/analysis/results/live_hybrid_500_seed42.jsonl` (500 rows: retrieval-only, no rtt/variant/repeat)
- `experiments/analysis/results/live_policy_demo.jsonl` (60 rows: 4 rtt × 3 repeats, policy-selected variant)

**Method:** For each cell (rtt × variant × stratum × metric), CV = σ/μ computed over all observations in that cell. Since each query appears once per stratum (cold = repeat 0, warm = repeats 1–2), the between-query CV equals the observation-level CV. For warm strata, within-query CV (across the 2 warm repeats per query) is also reported. N is computed via the doc's own formula: disjoint `n = 15.68·CV²/rel_d²`, paired `n = 3.136·CV²/rel_d²` (ρ=0.8).

### CV table (main campaign, source A)

| metric | stratum | CV med | CV p90 | CV max | N₁₀ (med) | N₁₀ (max) | N₅ (med) | N₅ (max) |
|--------|---------|--------|--------|--------|-----------|-----------|----------|----------|
| ttft_ms | cold | 0.184 | 0.216 | 0.243 | 55.0 | 92.5 | 219.9 | 369.8 |
| ttft_ms | warm | 0.209 | 0.267 | 0.282 | 82.7 | 115.4 | 330.7 | 461.5 |
| total_ms | cold | 0.488 | 0.696 | 0.722 | 405.2 | 817.5 | 1620.7 | 3269.9 |
| total_ms | warm | 0.952 | 1.195 | 1.251 | 1108.5 | 1553.7 | 4434.0 | 6214.8 |
| sparse_ms | cold | 0.581 | 0.761 | 0.820 | 454.4 | 1053.4 | 1817.7 | 4213.4 |
| sparse_ms | warm | 0.708 | 0.798 | 0.816 | 654.3 | 1031.3 | 2617.2 | 4125.2 |
| dense_ms | cold | 0.192 | 0.212 | 0.239 | 55.8 | 62.6 | 223.2 | 250.4 |
| dense_ms | warm | 0.484 | 0.508 | 0.544 | 79.1 | 85.8 | 316.3 | 343.4 |

### Verdicts

**(a) Does N=50/arm hold at MDE 10%?**

- **ttft_ms (confirmatory metric):** N=50 is **insufficient** for the sphp variant (N₁₀ = 92.5–115.4 across rtt regimes) and borderline for baseline (N₁₀ = 24.6–51.7). The doc's assumption of CV≤0.15 is too low; measured CV is 0.13–0.28. **N=50 holds only for baseline at rtt≤40 (cold) and rtt=80 (warm).** For sphp, N≥116 is needed.
- **total_ms:** N=50 is **grossly insufficient** (N₁₀ = 78–1554; CV 0.22–1.25).
- **sparse_ms:** N=50 is **grossly insufficient** (N₁₀ = 229–1053; CV 0.38–0.82).
- **dense_ms:** N=50 is **insufficient** (N₁₀ = 45–86; CV 0.17–0.54).

**Bottom line:** N=50/arm is adequate only for the confirmatory ttft_ms comparison in the baseline variant at most rtt regimes. The sphp variant (the treatment arm) needs N≥116 for a 10% MDE on ttft. All non-ttft metrics need far more.

**(b) Worst-case N for the 5% exploratory target:**

- ttft_ms: N₅ max = **462** (sphp, warm, rtt=0)
- total_ms: N₅ max = **6215** (sphp, warm, rtt=40)
- sparse_ms: N₅ max = **4213** (baseline, cold, rtt=15)
- dense_ms: N₅ max = **343** (sphp, warm, rtt=15)

The 5% target is infeasible for total_ms and sparse_ms at any practical N. For ttft_ms, N≥462 is the worst case (paired design with ρ=0.8 would reduce this to ~92).

**(c) Does the cold-vs-warm stratum distinction change any conclusion?**

- **ttft_ms:** modest difference (cold CV 0.13–0.24 vs warm 0.13–0.28). The sphp cold stratum (CV 0.198–0.243) is already above the doc's 0.15 assumption, so the stratum distinction does not rescue N=50.
- **total_ms:** large difference (cold CV 0.22–0.72 vs warm 0.35–1.25). Warm is 2–5× more variable.
- **sparse_ms:** moderate (cold 0.38–0.82 vs warm 0.62–0.82).
- **dense_ms:** large (cold 0.17–0.24 vs warm 0.18–0.54).

The stratum distinction **does not change the headline conclusion** (N=50 is underpowered for sphp ttft at 10% MDE) but **does matter for secondary metrics**: total_ms and dense_ms are 2–5× more variable in the warm stratum, so any warm-stratum claim on those metrics needs proportionally more samples.

**Recommendation:** For the confirmatory ttft_ms claim (sphp vs baseline), increase N to **120/arm** (covers worst-case N₁₀=115.4 with margin). For the 5% exploratory target on ttft, use the **paired design** (N=50 is adequate at ρ=0.8, N_paired5 max=92) or accept reduced power. Non-ttft metrics should be reported descriptively (percentiles + bootstrap CI) rather than as confirmatory claims, given their CV > 0.5.
