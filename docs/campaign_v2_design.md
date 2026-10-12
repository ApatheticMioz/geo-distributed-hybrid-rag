# Campaign v2 — Pre-registered Design

**Status:** PRE-REGISTERED (frozen before execution; any deviation after run-start
must be logged in §9 with justification — the v1 data files are the only
unmodified reference).
**Date:** 2026-10-12 · **Branch:** `feat/three-node-study`
**Sources of record:** `docs/campaign_methodology.md` (LR-3 stats plan +
empirical CV validation), `docs/related_work_landscape.md` LR-7 (methodology
package) and LR-11 (regime grounding), `experiments/bench/campaign.py`
(current driver), `experiments/analysis/stats.py`
(commits `a498fa4`/`5eac3ca`), `.scratch/issue1_wrapup_comment.md` (workstream
tracker). This document is the sole spec from which the v2 driver extension
must be implementable; the driver changes themselves are a later workstream.

---

## 1. Hypotheses & RQ mapping

Hypotheses H1–H4 are the claim set of `proposal/research_proposal.tex` /
`paper/main.tex` (related-work verdict keys in `docs/related_work_landscape.md`
RQ-3; the H-keyed novelty rows), mapped to the README research questions:

| H | Claim (paper form) | RQ | v2 role |
|---|---|---|---|
| H1 | Fusion placement is ~irrelevant (fusion cost is identifier-sized and location-invariant) | **RQ1** (placement) | Secondary — descriptive check (fusion_ms per placement) |
| H2 | Remote generation is nearly free under streaming (0.73 ms TTFT / ms RTT; identifier-sized A→B payload) | **RQ1** | **PRIMARY** — RTT slope re-confirmed at powered N |
| H3 | SPHP cuts cold-prefix TTFT (v1: −17.4% pooled, hit rate h≈0.84) | **RQ1** | **PRIMARY** — the load-bearing confirmatory claim |
| H4 | Placement crossover: an RTT × bandwidth phase map; P2-SPHP optimal in every swept cell | **RQ3** (policy) | **SECONDARY** — exploratory regime sweep |
| — | Graceful degradation: node dropout cost in latency and retrieval quality | **RQ2** | **SECONDARY (WS-CM)** — designed now, executed in the WS-CM workstream (issue #1 tracker), **not** in the v2 core run |

**Multiplicity declaration (frozen now, per LR-7 element 9 and methodology
RQ-d1):**

- **PRIMARY family (Holm / FWER, α = 0.05):** H3 — "SPHP reduces cold-prefix
  TTFT" in the 4 RTT tiers of the 2-node P2 vs P2-SPHP comparison
  (4 one-sided permutation tests, Holm-adjusted; one-sided because the
  alternative is directional — SPHP *reducing* cold TTFT — and the v1 effect
  is large and direction-consistent across all 4 tiers). This is the small,
  pre-specified family where a single false positive is costly.
- **SECONDARY family (BH / FDR, α = 0.05):** H2 (RTT-slope / warm-strata
  tests), H4 (loss × RTT exploratory sweep, per-cell arm comparisons), and all
  3-node/dropout comparisons. Controlled false-discovery fraction, not FWER.
- **Post-hoc:** anything not named in this document is *hypothesis-generating*
  and reported without confirmatory framing.

## 2. Arms

### 2.1 Core (executed in v2, 2-node — confirmatory + powered)

| Arm key | Arm | Notes |
|---|---|---|
| `P2` | 2-node baseline: sparse + dense + fusion on Node B; hydrate/prefill/decode on Node A; doc-ids across the link (driver: `--modes hybrid --sphp-axis`, `variant=baseline`) | Confirmatory control for H2/H3/H4 |
| `P2-SPHP` | Same placement with the speculative prefill + reconciliation enabled (`variant=sphp`) | Treatment for H3 |

Both arms run per regime cell of §3. This is the *confirmatory* matrix of v1,
restated at powered N (§4).

### 2.2 3-node arms (designed now; **executed in WS-CM** — not in the v2 core run)

Node C (`10.8.0.3`) hosts the Tantivy sparse index + user-facing tier
(`scripts/node_c_prep/NODE_C_CHECKLIST.md`); dense + fusion remain on B,
generation on A. Client queries enter at C.

| Arm key | Arm | Notes |
|---|---|---|
| `3node-P2` | 3-node baseline (sparse on C) | RQ1: adds a genuine three-way placement split |
| `3node-SPHP-from-C` | SPHP with the provisional sparse hint **originating on C** (the user-facing node), reconciled against the fused context on B | Primary 3-node deliverable: does moving the hint one hop toward the user reshape the SPHP cold/warm delta? (see checklist SPHP note) |
| `3node-P2-SPHP-from-B` | Counterpart arm: 3-node topology but the hint generated on B (legacy behavior) | Isolates the "hint origin" variable; the 3node-SPHP-from-C vs 3node-P2-SPHP-from-B contrast is a **paired** comparison (same queries, same cache state) — the only place the paired design of §4 is used, and it is defensible there because both measurements are on the same cache state (methodology RQ-b1) |

**RQ2 dropout arms (designed now; executed in WS-CM):**

| Arm key | Arm | Notes |
|---|---|---|
| `D-B` | Node B down; pipeline consolidates to A (sparse/dense/fusion/hydrate/decode all on A) | RQ2 latency + quality cost of gateway dropout |
| `D-C` | Node C down; sparse leg fails over to B's local index (2-node behavior) | RQ2 cost of sparse-tier dropout; the `3node` → `D-C` transition measures adaptation cost (time-to-degrade + first-query-after-failure TTFT) |

Each dropout arm measures: pre-dropout warm baseline (the arm's W = 5
warmups, same cache state → paired with the post-failure stratum), then
R = 5 post-failure repeats per query (first = immediate post-failure, the
remaining 4 = warm stratum). Retrieval quality (qrels) is scored per query
for the RQ2 quality-cost claim.

### 2.3 Exploratory 2-node arms (v2 secondary run)

| Arm key | Arm | Notes |
|---|---|---|
| `dense`, `sparse` | single-leg retrieval-only arms, per loss/RTT cell | H4 loss-sensitivity decomposition (which leg breaks first under loss) |

## 3. Regime matrix

### 3.1 2-node matrix: RTT {0, 15, 40, 80} × loss {0, 0.5, 1, 2} (16 cells)

Shaping: `scripts/netem/apply.sh` bidirectional on Node A (qdisc egress +
ifb0 ingress; RTT and loss values are full round-trip, halved per
direction by the script). The driver applies one regime at a time and clears
after every regime block.

**LR-11 framing caveats — inline, frozen (these must appear verbatim in the
paper's methods section):**

| Tier | Framing (citable per LR-11 RQ-5) |
|---|---|
| RTT 0 | **No-WAN control** (loopback/WG idle, sub-3 ms baseline), *not* a "real Internet" value (a real metro last hop is 7–25 ms, `fcc2022fixed`) |
| RTT 15 | **Metro-class** last hop incl. access (`fcc2022fixed`; `martinez2023rtt` intercepts 10–14 ms) |
| RTT 40 | **Cross-country low end / 4G-typical** (NA fit 0.015 ms/km × 1,500–2,000 km + 14 ≈ 36–44 ms; `landa2013geography`, `martinez2023rtt`) |
| RTT 80 | **Best-case transoceanic** — the *lower bound* of the 100–260 ms transoceanic class, not its median; caveat must be stated explicitly |
| loss 0 | control |
| loss 0.5 | **realistic** wired mid-band (between the ≤0.4% / 0.4–1% FCC consumer bands) |
| loss 1 | **realistic as onset of measurable impairment** — the ITU-derived industry degradation threshold (cited secondarily inside `fcc2022fixed`), a band boundary not a path median |
| loss 2 | **wireless/congestion tail-stress** — beyond wired P95, anchored by the 3G-emulation reference (2.1%, `hounsel2020dns`); *not* wired-typical |

**Execution split (pre-registered):**
- **Confirmatory core run:** RTT {0,15,40,80} × loss {0} only — this is the
  powered H2/H3 matrix (§4). Loss does not touch the primary family.
- **Exploratory loss sweep (secondary BH family):** P2 only (no SPHP axis),
  **retrieval-only**, N=50 per cell. The full 16-cell matrix is the design
  space; the v2 execution triages it to the 6 cells where the effect is
  expected to be resolvable — RTT {0, 40} × loss {0.5, 1, 2} → 6 arms
  (RTT 15/80 × non-zero loss are out of budget: loss effects compound RTT
  monotonically, so the 0/40 pair brackets the class).
  Loss interacts with retrieval latency (retransmits), not with decode, so
  this is measured without full generation; full-generation loss cells are out
  of scope (budget; the 2% tier is a stress condition, not a confirmatory one).

### 3.2 3-node per-leg shaping spec

The per-leg contract (issue #3, now in `apply.sh`/`validate.sh`): **explicit
link targeting is required — no implicit defaults.**

```
# 3-node regime (B and C shaped independently):
sudo bash scripts/netem/apply.sh \
    --link-b <RTT_B_MS> <LOSS_B_PCT> [RATE_B] \
    --link-c <RTT_C_MS> <LOSS_C_PCT> [RATE_C]
```

- `--link-b` → Node B (10.8.0.2) leaves (u32 dst/src 10.8.0.2, netem 10:);
  `--link-c` → Node C (10.8.0.3, netem 20:); unshaped default class 1:30
  keeps SSH/Tailscale/LAN clean.
- **Pre-registered 3-node regime cells:** (B, C) ∈ {(15, 15), (40, 15),
  (80, 15)} — B carries the dense/generation legs, C the user-facing sparse
  leg; the (40, 15) and (80, 15) cells test whether the added A↔B hop (vs the
  user-facing C leg) dominates TTFT. The 3-node unshaped control is the
  2-node (0, 0) baseline arm, so the 3-node matrix runs 3 cells × 2 arms
  (`3node-P2`, `3node-SPHP-from-C`) = 6 arms. The driver must call
  `apply.sh --link-b … --link-c …` for 3-node arms and `--link-b` alone for
  2-node arms; the record gains `netem_link_c_applied` + per-leg targets.

### 3.3 Genuine-WAN characterization leg (netem-off; one-time, pre-registered)

A single characterization leg over the **real cross-site route** (the public
Internet path of the 3-node mesh, per the roadmap) with **all netem removed**
(`clear.sh` verified via `show.sh`):

- **What to record:** per-query wall timings (the `X-` timing headers:
  sparse/dense/fusion/ttft/total), and **continuous ping probes** (11 pings
  every 5 s) to both peer IPs, logging per-ping RTT, inter-ping jitter, and
  loss fraction over the whole leg; plus the node placement + network metadata
  with every record (the proposal's lesson from the lost 100-query log).
- **Sample:** 100 queries per variant (P2, P2-SPHP) = 200 queries total,
  3 repeats each (600 records; not powered — characterization only).
- **Use:** report the realized RTT/loss/jitter distribution *alongside* the
  emulated-leg numbers at the same tier values; the emulated-vs-real gap is
  the LR-7 element-6 fidelity metric (`calf2026` pattern (a)+(b); `chen2026rdmawan`).
  It is **not** part of the Holm/BH families; it calibrates the emulation
  claim.

## 4. Sample sizes (powered from the methodology CVs)

Power formulas (methodology RQ-a2/a4; `stats.py::sample_size`, `mde`,
`achieved_power`): two-sample `n = 15.68·CV²/rel_d²`, paired
`n = 3.136·CV²/rel_d²` at ρ = 0.8; α = 0.05, power 0.80.

**CV inputs (methodology empirical CV validation, 2026-09-18 campaign):**
SPHP TTFT 0.198–0.282 (cold 0.198–0.243; warm 0.209–0.282); total_ms
0.49–1.25 (cold 0.49–0.72; warm 0.95–1.25).

### 4.1 Power table (two-sample / paired ρ=0.8)

| Metric (CV range) | MDE | N two-sample | N paired |
|---|---|---|---|
| SPHP TTFT (0.198–0.282) | 10% | **62–125** | 13–25 |
| SPHP TTFT (0.198–0.282) | 5% | 246–499 | 49–100 |
| total_ms (0.49–1.25) | 10% | 377–2,450 | 75–490 |
| total_ms (0.49–1.25) | 5% | 1,506–9,800 | 301–1,960 |

### 4.2 Confirmatory N (frozen): **N = 120 per arm**

- Justification: worst-case two-sample requirement at 10% MDE is
  `15.68 · 0.282² / 0.10² ≈ 124.7`; the methodology's recommendation
  (N = 120/arm, covering N₁₀ = 115.4) plus the measured max-CV cell
  (0.282) gives 120 as the smallest round number that (i) matches the
  methodology recommendation and (ii) keeps achieved power at the worst CV
  within 0.02 of 0.80 (achieved power at N=120, CV=0.282: 0.784
  two-sample / 1.000 paired). We accept 0.784 at the worst CV: the v1
  failure mode was N=50 → 0.47–0.66 power in warm cells, and N=120 restores
  ≥0.97 power at the *median* SPHP CV (0.198–0.216) across all tiers.
- **What the paired design buys:** a paired design (same queries, same cache
  state) at ρ = 0.8 needs only 13–25 per arm for the 10% MDE — a 5× gain
  (coefficient ratio `15.68/3.136 = 5`, the `2(1−ρ)` variance factor).
  It is **not** used for the
  primary P2 vs P2-SPHP family because disjoint slices are the defensible
  default when cache state can confound (methodology RQ-b1 contested
  decision, kept). It **is** used where the same-condition condition holds:
  the 3-node `SPHP-from-C` vs `SPHP-from-B` contrast (§2.2) and the dropout
  pre/post transition (§2.2), where both measurements share cache state —
  there N = 100 per arm suffices even for the 5% MDE on SPHP TTFT (N ≤ 100).
- **v1 evidence motivating the powered N:** `stats_sanity_20260918campaign.json`
  — under proper inference (permutation + Holm), the warm-stratum SPHP delta
  is **not significant in 3 of 4 RTT tiers** (raw p = 0.102 / 0.344 / 0.393 at
  rtt 15/40/80; only rtt 0 passes), while the cold stratum is significant in
  all 4 (p ≤ 1e-4). At N=120 the warm family is at ≥0.78 power (vs ~0.4 at
  N=50) — the powered N is what makes the warm-stratum question answerable.

### 4.3 Exploratory N (frozen)

- **2-node loss sweep (§3.1):** retrieval-only, N = 50 per cell (6 arms) —
  descriptive/BH only; CV-based powered claims on retrieval-only cells are
  not made (that is a characterization of leg behavior, not a confirmatory
  effect).
- **3-node arms (§2.2):** N = 80 per arm (10 warmups + 80 measured per
  disjoint slice) — the SPHP-from-C vs SPHP-from-B *paired* contrast runs at
  N = 100 paired queries (same queries, so no extra warmups); dropout arms
  N = 60, each with 1 pre-dropout warm + 4 post-dropout warm repeats per
  query (§2.2).
- **Genuine-WAN leg (§3.3):** 100 queries — characterization only.

**Totals (driver must print these in the dry-run plan):**

| Run | Arms | N/arm | R | W/arm | Measured records | Total requests |
|---|---|---|---|---|---|---|
| v2 confirmatory core (2-node) | 8 (4 RTT × {P2, P2-SPHP}) | 120 | 3 | 10 | 28,800 | 28,880 |
| v2 loss sweep (2-node, retrieval-only) | 6 | 50 | 3 | 5 | 900 | 930 |
| **v2 core total** | | | | | **29,700** | **29,810** |
| WS-CM 3-node | 6 (3 (B,C) cells × 2 arms) | 80 | 3 | 10 | 14,400 | 15,000 |
| WS-CM dropout (3-node) | 2 | 60 | 5 | 5 | 600 | 610 |
| Genuine-WAN leg | 2 | 100 | 3 | 5 | 600 | 610 |

The current driver (`build_arm_query_plan`) already produces disjoint seeded
slices and fails fast on pool exhaustion; v2 adds `--query-sample 120`
support, the per-leg `--link-c` argument, retrieval-only mode, and the
manifest writer (§5). Runtime estimate: full-generation requests run at
≈ total_ms + overhead (~3 s each, v1 measured 2.3–3.2 s total_ms) ⇒ the
confirmatory core ≈ 28,800 × 3 s ≈ **24 h (two overnight batches**); the
retrieval-only loss sweep ≈ 900 × ~1.5 s ≈ 25 min.

## 5. Query pools

### 5.1 Naming & files

| File | Content | Use |
|---|---|---|
| `experiments/bench/queries/campaign_v2_dev2250_seed42.txt` | **2,250** distinct MS MARCO dev queries, seeded shuffle (seed 42) — the **core confirmatory pool** = the first 1,040 lines; the **master exploratory pool** = lines 1,041–2,250 | v2 core: 8 arms × (120+10) = 1,040 (lines 1–1,040); loss sweep 6 × (50+5) = 330 (lines 1,041–1,370); WS-CM 3-node 6 × (80+10) = 540 (lines 1,371–1,910); WS-CM dropout 2 × (60+5) = 130 (lines 1,911–2,040); genuine-WAN 2 × (100+5) = 210 (lines 2,041–2,250) |

- Generated by `scripts/make_latency_sample.py --n 2250 --seed 42` from
  `data/msmarco/queries.dev.tsv` (the 6,980-query dev set with qrels; the
  existing `latency_dev480_seed42.txt` and `latency_dev60_seed42.txt` remain
  as v1 artifacts and are **not** consumed by v2).
- **Disjointness manifest:** the driver writes
  `experiments/bench/campaigns/campaign_v2_manifest.json` before any request:
  per arm → `pool_start/pool_end`, the query list, the sha256 of the pool
  file, seed, and the disjointness assertion (pairwise intersection of all
  arm slices, including warmups, == ∅). The manifest is part of the
  AE-ready artifact (LR-7 element 8) and the analysis handoff.

### 5.2 Cold/warm strata handling & warmup policy

- **Strata, not replicates** (methodology RQ-b2/RQ-b3): per measured query —
  repeat 0 = cold-prefix stratum, repeats 1..R-1 = warm stratum. The driver
  does not flush the engine (no flush endpoint exists); this is reported as a
  property of the measurement, per v1.
- **Warmups:** W = 10 per arm for the powered core run (v1 used 5; with
  N = 120 the warmup share is 7.7% vs 9.1%, keeping the cold reference clean
  while amortizing engine warm-start; warmup queries are arm-disjoint and
  **executed but not recorded**). Loss-sweep, 3-node, dropout, and
  genuine-WAN arms: W = 5 as tabled (§4.3).
  tabled.
- Disjoint per-arm slices guarantee no arm's repeat-0 prefixes are
  pre-cached by another arm (the v1 invariant, unchanged).

## 6. Procedure

**Run order (one campaign driver invocation per row; netem cleared between
regimes as in v1):**

1. **Dry run + pre-flight (≤ 1 h):**
   - `campaign.py --dry-run` per run above; manifest disjointness printed.
   - `sudo bash scripts/netem/validate.sh` per tier — with the **11-ping
     median** gate (fixed 2026-10-12; ±30% window on added RTT per leg;
     the A→B validation of 2026-10-12 passed all 3 tiers,
     `scripts/netem/validation_ab_20261012T145653Z.md`).
   - **Dense-leg sustained probe gate:** re-run the dense sustained-load
     probe (the `dense_sustained_probe_20261012T155752Z` recipe: 60 queries,
     retrieval-only, `latency_dev60_seed42.txt`) — **abort the campaign if
     p95 > 3,000 ms or max > 10,000 ms or errors > 0** (post-fix PASS:
     p50 ≈ 1,023 / p95 ≈ 1,495 ms, 0 errors; the v1-era dense leg was
     degenerate — cold mean ≈ 2,650–2,742 ms, max 4,139 ms,
     `.scratch/metrology_audit.md`).
   - Gateway `/health` on A, B (and C for WS-CM).
2. **Retrieval-only pre-pass** (loss sweep, §3.1): overnight, first batch —
   retrieval-only is 3–5× cheaper than full generation, so the loss
   findings are in hand even if the full-generation batch must be interrupted.
3. **Full-generation confirmatory core** (8 arms × 120 × 3): overnight
   batching — the driver batches per regime (4 RTT regimes sequentially,
   P2 → P2-SPHP per regime), with the 11-ping median fidelity gate
   **re-validated at regime boundaries (pre- and post-per-tier)** and a
   `/health` check before each arm; on a mid-campaign gateway restart, the
   arm resumes from the last written JSONL line (idempotent by
   (arm, query, repeat) — records are append-only per arm).
4. **0-error assertion (campaign gate):** the campaign **fails** if
   `n_errors > 0` in any core-run arm (v1 achieved 0/1200; a non-zero
   error rate invalidates the strata and the run must be re-run, not
   patched). The WS-CM 3-node arms relax this to ≤1% error rate (real
   Internet legs) but the 2-node core stays at 0.
5. **WS-CM 3-node + dropout + genuine-WAN leg** (separate workstream, same
   manifest discipline; §3.3 leg runs last, netem verified off).
6. **Post-campaign:** `clear.sh`, `show.sh` snapshot, then the §7 analysis
   script; all outputs land in `experiments/bench/campaigns/` and
   `experiments/analysis/results/`.

**Fidelity gates (LR-7 element 6, frozen):**
- **Pre-run, per tier:** `validate.sh` 11-ping median, ±30% added-RTT
  window, per leg (B for 2-node; B and C for 3-node).
- **Per-run, at every regime boundary:** 11-ping median to the shaped peer(s),
  logged to the campaign JSONL as `rtt_ping_median_ms` per regime transition;
  a drift outside ±30% of the tier **aborts the campaign** (no re-shaping
  mid-arm).
- **Real-WAN leg:** the §3.3 recorded distribution is the post-hoc
  emulated-vs-real gap report.

## 7. Analysis handoff

Exact functions from `experiments/analysis/stats.py`
(commits `a498fa4` statistics engine, `5eac3ca` sanity gate), per comparison.
Fixed seeds: **BCa B = 1,000, seed 42; permutation n = 10,000, seed 42**
(matches `stats_sanity_20260918campaign.json`; note LR-7's B = 2,000
proposal is superseded by this frozen value for comparability with the v1
sanity artifact).

| Comparison | Estimate | Test | Multiplicity |
|---|---|---|---|
| **H3 primary:** P2-SPHP vs P2, **cold** TTFT (repeat 0), each RTT tier | `bootstrap_delta_ci(sphp_cold, base_cold, stat="mean", method="bca", B=1000, seed=42)` + `bootstrap_delta_ci(…, stat="median")` | `permutation_test(sphp_cold, base_cold, stat="mean", alternative="less", n_resamples=10000, seed=42)` and `stat="median"` | **Holm** over the 4 cold cells (`holm`) |
| H2 / warm: P2-SPHP vs P2, warm TTFT (repeats 1+), each RTT tier | same `bootstrap_delta_ci` | same `permutation_test` | **BH** (`benjamini_hochberg`) over the 4 warm cells |
| H2 RTT slope: P2 TTFT vs RTT tier | descriptive: per-tier `bootstrap_ci(vals, stat="mean"/"p95", method="bca")`, slope reported with CI (regression on the cell means, flagged exploratory) | n/a | — |
| H4 loss sweep: P2 retrieval (sparse_ms/dense_ms/total_ms) per (rtt, loss) cell vs loss=0 cell | `bootstrap_ci` per metric per cell; `bootstrap_delta_ci` for leg vs its loss=0 counterpart | `permutation_test` where a directional claim is made | **BH** over the sweep family |
| 3-node: SPHP-from-C vs SPHP-from-B (paired) | `bootstrap_delta_ci(a, b, paired=True, stat="mean")` | `permutation_test` on the difference vector (paired) | **BH** |
| Dropout: post-dropout vs pre-dropout (paired, same queries) | `bootstrap_delta_ci(…, paired=True)` for ttft_ms/total_ms; quality delta from qrels scorer | `permutation_test` paired | **BH** |
| SPHP hit rate (h) | `wilson_ci(k, n)` per arm/stratum | — | — |
| Effect sizes | `cohens_d(a, b, paired=…)`, `cliffs_delta(a, b)` (via `effect_size`) | — | — |
| Descriptive reporting | `percentile` (type-7 linear) + `bootstrap_ci` on **p50/p95/p99** for every reported cell (LR-7 element 1–2) | — | — |

Headline effect size: the **relative delta with BCa CI** (RQ-e1), reported as
e.g. "−17.4% [−25.7, −12.4]". Power audit per cell: `achieved_power(n,
rel_d, cv, design)` and `mde(n, cv, design)` in the results JSON, so the
frozen N above is auditable post-run.

## 8. Deviations from campaign v1 (2026-09-18)

| # | Deviation | Why (one line) |
|---|---|---|
| 1 | **Dense-leg fix adopted as the v2 baseline** (Qdrant gRPC `prefer_grpc` + host default, `e4fc9a3`/`65b6dd2`): the dense sustained probe post-fix shows p50 ≈ 1,023 / p95 ≈ 1,495 ms (0 errors) vs the v1-era degenerate dense leg (cold mean ≈ 2,650–2,742 ms, max 4,139 ms, `.scratch/metrology_audit.md`) | The v1 campaign ran on a half-broken dense leg, inflating every dense-dependent metric; v2 measures the system as it actually serves (`dense_sustained_probe_20261012T155752Z.json` is the acceptance gate, §6) |
| 2 | **Powered N: 50 → 120/arm for the SPHP-TTFT confirmatory family** | v1's N=50 was underpowered at the measured CVs (0.198–0.282): warm-stratum SPHP deltas were non-significant in 3 of 4 RTT tiers under proper inference (`stats_sanity_20260918campaign.json`) — the powered N restores ≥0.78 power at the worst CV and makes the warm question answerable |
| 3 | **BCa bootstrap CIs (B=1,000, seed 42) on every percentile and delta** | v1 reported bare point estimates; the LR-7 package (Treadmill/NIST) requires CIs, and BCa corrects the percentile interval for the skew of heavy-tailed TTFT |
| 4 | **Regime framing caveats made explicit** (0 ms = no-WAN control; 80 ms = best-case transoceanic, not median; 2% loss = wireless tail-stress) | LR-11 grounded every tier in citable measurements and flagged exactly these three framing risks; stating them now preempts the reviewer objection that the tiers are "arbitrary" |
| 5 | **11-ping median fidelity gate, pre- and post-per-tier** (`validate.sh` fix, `7943cca`/`6231317`) | the 5-ping median was high-variance at the 15 ms tier (2026-10-12 A–B validation) and the gate had silently never run because cold-start `apply.sh` aborted before shaping (idempotent ifb0 bring-up fix); the fix makes the closed-loop gate real, per the LR-7 element-6 pattern |
| 6 | **Loss axis added (0/0.5/1/2%), retrieval-only sweep** | v1 only swept RTT×{loss 0}; LR-11 grounded the loss tiers and the loss axis is the unexplored dimension of the H4 phase map |
| 7 | **3-node arms + RQ2 dropout arms designed, deferred to WS-CM** | Node C was down during v1 and its bring-up is a separate workstream (issue #1 tracker); designing now freezes the arms/manifest so WS-CM cannot reshape the pre-registration |

**Out-of-scope this cycle (frozen):** loaded/Poisson arrival sweeps, bandwidth
cells (crossover analysis already covers {10…1000} Mbps per LR-11 RQ-3),
P0/P1/P3 placements (2-node P2/P2-SPHP + 3-node arms span the load-bearing
placement space).

---

## 9. Deviation log

*(empty at pre-registration; post-run-start changes are appended here with
date, actor, and justification.)*
