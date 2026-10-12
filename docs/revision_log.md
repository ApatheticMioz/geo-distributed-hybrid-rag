# Revision Log — Cost Model

**Purpose (per the approved plan, item AB-5):** a change-proposal ledger for the
analytical placement cost model. Each entry names a *systematic misfit* found in
the residual diagnostics, states precisely what functional form would change,
cites the diagnostic numbers as evidence, gives a **like-for-like A/B
validation plan** (old form vs new form fitted on the *identical* data, scored
on held-out regimes), and records the expected paper impact. The **Verdict**
column stays `proposed` — adoption of any new form is a later, explicit
decision by the Revision Board and is *not* made here. No model code or paper
text is modified by this log.

**Sources of evidence (this slice):**
- New data: `experiments/bench/campaigns/campaign_v2_confirm_2node_20261013.jsonl`
  (2,880 records, 0 errors; 120 queries/arm × 3 repeats × 4 RTT tiers × 2 variants).
- Like-for-like old data: `campaign_20260918T130756Z.jsonl` (1,200 records).
- Generated report: `experiments/analysis/results/costmodel_refit_v2_20261013.md`
  (script `experiments/analysis/costmodel_refit_v2_20261013.py`; JSON sibling).
- Protocol throughout: leave-one-tier-out, regime (tier-mean) level — the
  validation protocol the paper's C3 claim uses.

**Headline re-baseline result (no new forms — existing forms only):**

| quantity | published (09-18 fit) | re-baseline (10-13 fit) |
|---|---|---|
| LOTO regime MAPE, P2 (cold / warm) | 1.3% / 2.8% | 2.2% / 4.1% |
| LOTO regime MAPE, P2-SPHP (cold / warm) | 10.6% / 5.8% | 7.8% / 4.4% |
| **TRANSFER error** = old fit applied to new surface (cold / warm, P2) | — | **114.0% / 58.5%** |
| argmin placement policy, warm realized error | ~5% (4.5–8.6%) | **5.57% pooled** (0.7–11.4% per tier) |
| argmin decision (all swept RTT×BW cells) | P2-SPHP | **P2-SPHP (unchanged)** |

The 114%/58.5% transfer error is *not* a model-quality regression: it is the
magnitude by which the dense-leg fix (old cold dense ≈ 2,681 ms → new ≈ 1,092 ms;
old warm dense ≈ 191 ms → new ≈ 114 ms) **shifted the cost surface** between
baselines. A cost model fit to the old surface cannot transfer to the new one
without re-fitting — this is exactly the figure the paper's
testbed-disclosure section needs to disclose.

---

## Proposal ledger

| # | misfit (short) | Verdict |
|---|---|---|
| RP-1 | SPHP miss penalty is a per-state *constant*; realized penalty is RTT-rising | **proposed** |
| RP-2 | SPHP hit path omits a **cold-only** KV-prefix credit (state-dependent) | **proposed** |
| RP-3 | WAN term is analytic `rtt/2 + serialization`; measured RTT slope differs from 0.5 | **proposed** |

---

### RP-1 — SPHP miss penalty should be regime-conditional, not a per-state constant

- **Current form.** `t_sphp_miss_penalty_ms` is a single per-cache-state constant
  (= mean recorded `wasted_prefill_ms`), added to the P2 base on a miss:
  `TTFT_miss = TTFT_P2 + Tmiss` (eq. P2-SPHP miss branch).
- **What to change.** Replace the per-state constant with a regime-conditional
  penalty `Tmiss(T) = a_s + g_s·rtt` (one slope/intercept per cache state `s`),
  fitted from realized miss cost, so the miss branch inherits the same RTT
  dependence the P2 base already carries.
- **Evidence.** (a) The recorded `wasted_prefill_ms` mean (124.5 ms warm,
  1,076.6 ms cold) *understates* the realized miss cost: realized miss penalty
  = miss-TTFT tier-mean − P2 tier-mean = **{0: 263.3, 15: 327.9, 40: 288.8,
  80: 304.3} ms warm** (mean 296.1) — a +171.6 ms gap over the recorded value,
  and it *rises with RTT*, the signature of a re-hydration/re-prefill that the
  link makes more expensive, which a constant cannot represent. (b) The branch
  form check with the constant penalty returns **21.9% warm / 58.0% cold MAPE**,
  far worse than the hit branch (32.8% / 8.7%), i.e. the miss branch is the
  dominant SPHP form error. (c) It also inflates the modeled SPHP expected
  cost relative to the realized one in warm (see RP-2).
- **Like-for-like A/B plan.** On the *identical* 10-13 records, fit (A) the
  existing constant-`Tmiss` form and (B) the `a_s + g_s·rtt` form; score both
  by (i) SPHP-branch MAPE (warm and cold) and (ii) the measured-vs-modeled SPHP
  expected-value MAPE via leave-one-tier-out. Adopt B only if it reduces both
  without degrading P2.
- **Paper impact.** §VI P2-SPHP equation and the "miss penalty" sentence
  (currently "194 ms warm, 2,698 ms cold, measured"); the SPHP expected-value
  LOTO MAPE would be re-stated. Affects the C3 model claim and Fig. 8.

### RP-2 — SPHP hit path omits a cold-only KV-prefix credit (state-dependent)

- **Current form.** On a hit the model emits the first token at
  `max(t_fused_arrival, t_prefill_finish)`; it reuses the provisional prefill
  *compute* but does **not** credit reuse of the provisional **KV prefix**
  (the paper's Discussion already concedes this: "a hit also preserves the
  provisional KV prefix — a credit the closed form does not encode").
- **What to change.** Add a state-dependent hit credit `C_s` to the hit branch:
  `TTFT_hit = max(t_f, t_p) − C_s`, with `C_s = 0` for the warm state (prefix
  already resident, nothing extra to save) and `C_c > 0` for cold, fitted per
  state. This makes the missing term explicit and *state-conditioned* rather
  than an unexplained residual.
- **Evidence.** Measured SPHP speedup (SPHP TTFT vs P2 TTFT) is
  **25.18% cold** but only **2.81% warm**; the fitted model predicts
  **19.48% cold** and **3.24% warm**. So the model *over-predicts* warm
  (≈ +0.4 pp) and *under-predicts* cold (≈ −5.7 pp) — the gap is concentrated
  in the cold state, exactly where a retained provisional KV prefix saves real
  re-prefill. The old baseline showed the same shape (17.4% measured vs
  7.8% modeled cold; warm within noise), so this is a pre-existing,
  state-asymmetric form error that the re-baseline re-confirms rather than a
  re-baseline artifact.
- **Like-for-like A/B plan.** On the identical records, fit (A) the current
  hit form and (B) the form with `C_s` (set to 0 in warm, fitted in cold);
  compare the measured-vs-modeled SPHP *speedup* in each state and the SPHP
  expected-value LOTO MAPE. Adoption criterion: (B) must shrink the cold gap
  (25.2%→~modeled) without moving the warm prediction off its (already
  accurate) value.
- **Paper impact.** §VI SPHP equation (eq. P2-SPHP), the crossover-section
  sentence "the model … underestimates the measured cold-state SPHP benefit
  (7.8% modeled vs 17.4% measured)", and the C3 claim. Tightens the
  "conservative lower bound on the cold-start benefit" disclosure into a
  quantified term. Affects Fig. 6 and Fig. 8.

### RP-3 — WAN term is analytic `rtt/2 + serialization`; the measured RTT slope is not 0.5

- **Current form.** `Twan(p, rtt, bw) = rtt/2 + 8p/(bw·10^6)·10^3` (eq. WAN):
  a *fixed* half-RTT propagation slope (0.5 ms/ms per one-way crossing) plus
  byte serialization.
- **What to change.** Let the WAN term be a **fitted** linear-in-RTT form
  `Twan_s(T) = b_s + g_s·rtt` (+ the existing serialization term), with slope
  `g_s` and intercept `b_s` estimated per cache state from the campaign's
  RTT tiers, instead of hard-coding `g_s = 0.5`. The paper's RQ1 already
  reports an *empirical* TTFT-vs-RTT slope (0.73 ms/ms old); the model's own
  WAN slope should be reconciled with that measurement, not left analytic.
- **Evidence.** The old-fit-on-new **per-tier transfer residuals** are the
  cleanest tell: they drift monotonically rather than staying flat — warm
  P2 residual goes **339 → 325 → 308 → 302 ms** (0→80 ms RTT) and old-warm
  SPHP **339 → 325 → 308 → 302**, i.e. the error *shrinks* with RTT, the
  signature of a **slope mismatch** (the model climbs faster than reality at
  high RTT). Measured warm TTFT-vs-RTT slope on the re-baseline is
  **≈ 0.95 ms/ms** (old ≈ 0.73) for the P2 baseline, but the SPHP path makes
  *two* one-way crossings (hint + fused-context), so the analytic P2-SPHP WAN
  slope is ≈ 1.0 ms/ms — it fits the measured slope *by accident* while the
  single-crossing P2 form (0.5) is far flatter than measured. Cold is flat /
  slightly *negative* (≈ −0.13 ms/ms) because the retrieval barrier dominates
  and swamps the network term. So the analytic `rtt/2` slope is not a free
  parameter the data support; it happens to be reasonable for SPHP-warm and is
  flat-out wrong in sign for cold.
- **Like-for-like A/B plan.** On the identical records, fit (A) the analytic
  `rtt/2 + serialization` WAN and (B) a per-state `b_s + g_s·rtt` WAN (same
  serialization term); score by (i) the P2 component-residual MAPE and
  (ii) the per-tier transfer-residual drift (B should flatten it) and (iii) the
  P2 LOTO regime MAPE. Adoption criterion: (B) must reduce both the component
  MAPE and the per-tier residual drift without inflating SPHP error.
- **Paper impact.** §VI WAN equation (eq. WAN) and the RQ1 slope sentence; the
  crossover map (Fig. 6) RTT axis becomes data-fit rather than analytic.
  This is the highest-leverage change for the transfer-error disclosure: it is
  the reason an unrefit (old) model cannot predict the new surface.

---

## What is *not* a misfit (checked and documented, no proposal)

- **Hydration term (per-doc / linearity).** The hydration residual
  (TTFT − retrieval barrier − fusion − WAN − prefill) averages **−16.9 ms with
  ~87–149 ms std** and is *not* a positive per-doc term: `top_k` is constant
  (=10) across the campaign, so doc-count linearity is degenerate by
  construction, and the hydration-vs-token-count slope is insignificant
  (R² < 0 in both states). The fitted 5-doc coefficient is negative (~−3.4
  ms/doc), i.e. hydration is absorbed into the prefill residual — it is *not*
  adding measurable, separable cost on this corpus at top-k=10. No functional
  change is warranted; the term is correctly near-zero and the residual it
  leaves is small relative to the prefill term. (If a future campaign varies
  `top_k`, this becomes a real linearity test; logged here so that is not
  re-derived by hand.)
- **Decode TPS constancy.** Decoded rate is **stable** across states and
  baselines (new 35.8 cold / 35.3 warm tok/s; old 35.6 / 35.2), well inside the
  1–500 sanity band with 0 degenerate exclusions. The decode term
  (`N_tok / Rdecode`) is a valid constant per state; no change.
- **Cache stratification.** Cold vs warm are genuinely distinct regimes
  (retrieval barrier ~2–4× higher cold) and the model already fits them
  separately; the re-baseline preserves that split and the stratification
  holds. No change.
- **Fusion term.** `~0.015–0.02 ms` in both baselines — the H1 "location-
  invariant / identifier-sized" claim re-confirms; no change.

---

*Verdict column is `proposed` for all three entries. Adoption (and any
re-generation of paper figures / the C3 numbers) is a subsequent, separate
decision and is out of scope for this slice.*
