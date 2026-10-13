# Revision-Board like-for-like evaluation of RP-1 / RP-2 / RP-3

Script: `analysis/revision_board_eval_20261014.py` (seed 42; deterministic — no stochastic steps). Protocol: leave-one-RTT-tier-out, regime (tier-mean) level, identical data and identical baseline-term fit recipe in both arms of every A/B. BW = 100 Mbps, top_k = 10.


> **Reading the R² column.** R² is reported for completeness but is *not* the operative metric here: the regime-level target is the 4 RTT-tier means of TTFT, whose between-tier spread is dominated by the large, nearly-constant prefill term (hence large |Δ| in the denominator and often-negative R², exactly as in the published re-baseline, which carries R² = −2.27 / −62.17 / … for the identical current form). **MAPE is the metric used for every verdict.** The current arm reproduces the stored re-baseline LOTO values (MAPE and R²) bit-for-bit, so the like-for-like comparison is exact.


## Verdicts

| proposal | verdict | wins on 09-18 | wins on 10-13 |
|---|---|---|---|
| RP-1 | **deferred** | True | False |
| RP-2 | **adopted (analysis-level)** | True | True |
| RP-3 | **rejected** | False | False |


### RP-1


**old_20260918** — LOTO regime MAPE% (R²) and the win check:

| regime | current | proposed | Δ pp |
|---|---|---|---|
| cold P2 | 1.28 (-2.2672) | 1.28 (-2.2672) | 0.0 |
| cold P2-SPHP | 10.55 (-62.1645) | 3.88 (-8.3005) | -6.67 |
| warm P2 | 2.76 (0.076) | 2.76 (0.076) | 0.0 |
| warm P2-SPHP | 5.81 (-0.2015) | 4.72 (0.3504) | -1.09 |

- targeted Δ: {'sphp_cold': -6.67, 'sphp_warm': -1.09}  (all strictly lower: True)
- guard Δ: {'p2_cold': 0.0, 'p2_warm': 0.0}  (all ≤ 0.25 pp: True)
- targeted residual pattern removed: True
- **win on old_20260918: True**

  - cold miss-penalty fit: Tmiss(T) = 21.01 + 0.4268·rtt (R²=0.0928); realized per-tier {'0': 22.3, '15': -14.1, '40': 102.9, '80': 30.5} ms
    - miss-branch MAPE: current 75.84% (n=31) → proposed 3.81%; miss-branch resid slope on RTT -0.0204 → -0.0134 pp/ms
  - warm miss-penalty fit: Tmiss(T) = 206.47 + 2.3986·rtt (R²=0.6417); realized per-tier {'0': 144.7, '15': 274.0, '40': 374.6, '80': 356.3} ms
    - miss-branch MAPE: current 14.77% (n=62) → proposed 13.33%; miss-branch resid slope on RTT -0.2074 → -0.01 pp/ms

**new_20261013** — LOTO regime MAPE% (R²) and the win check:

| regime | current | proposed | Δ pp |
|---|---|---|---|
| cold P2 | 2.15 (-1.2586) | 2.15 (-1.2586) | 0.0 |
| cold P2-SPHP | 7.83 (-5.583) | 3.34 (-0.3235) | -4.49 |
| warm P2 | 4.13 (0.2533) | 4.13 (0.2533) | 0.0 |
| warm P2-SPHP | 4.37 (0.1897) | 5.98 (-0.2116) | 1.61 |

- targeted Δ: {'sphp_cold': -4.49, 'sphp_warm': 1.61}  (all strictly lower: False)
- guard Δ: {'p2_cold': 0.0, 'p2_warm': 0.0}  (all ≤ 0.25 pp: True)
- targeted residual pattern removed: False
- **win on new_20261013: False**

  - cold miss-penalty fit: Tmiss(T) = 18.82 + 1.9965·rtt (R²=0.857); realized per-tier {'0': -2.0, '15': 49.8, '40': 138.7, '80': 158.4} ms
    - miss-branch MAPE: current 58.01% (n=48) → proposed 4.36%; miss-branch resid slope on RTT -0.1025 → 0.0743 pp/ms
  - warm miss-penalty fit: Tmiss(T) = 288.28 + 0.23·rtt (R²=0.0879); realized per-tier {'0': 263.3, '15': 327.9, '40': 288.8, '80': 304.3} ms
    - miss-branch MAPE: current 21.86% (n=96) → proposed 25.82%; miss-branch resid slope on RTT -0.054 → -0.059 pp/ms

> Deferred condition: mixed evidence: the proposed form WINS on old_20260918 but FAILS on new_20261013. A future data set (a successor campaign surface) must re-run this identical A/B and win on it, i.e. show (i) targeted LOTO regime MAPE strictly lower in every targeted regime, (ii) all guard regimes within 0.25 pp, and (iii) the targeted residual pattern removed, as defined in this script's verdict rules. The failure on the current surface is the specific regime noted below — that is the physical condition a future surface must change.


### RP-2


**old_20260918** — LOTO regime MAPE% (R²) and the win check:

| regime | current | proposed | Δ pp |
|---|---|---|---|
| cold P2 | 1.28 (-2.2672) | 1.28 (-2.2672) | 0.0 |
| cold P2-SPHP | 10.55 (-62.1645) | 1.72 (-1.6386) | -8.83 |
| warm P2 | 2.76 (0.076) | 2.76 (0.076) | 0.0 |
| warm P2-SPHP | 5.81 (-0.2015) | 5.81 (-0.2015) | 0.0 |

- targeted Δ: {'sphp_cold': -8.83}  (all strictly lower: True)
- guard Δ: {'p2_cold': 0.0, 'p2_warm': 0.0, 'sphp_warm': 0.0}  (all ≤ 0.25 pp: True)
- targeted residual pattern removed: True
- **win on old_20260918: True**

  - cold hit-credit C_s: fitted 324.0 ms (implied per-tier {'0': 55331.5, '15': 16803.2, '40': 32392.0, '80': 46071.8}); measured vs modeled SPHP speedup: current 17.37% vs 8.0% (gap 9.37 pp) → proposed 17.37% vs 15.66% (gap 1.71 pp)
  - warm hit-credit C_s: fitted 0.0 ms (implied per-tier {'0': 4062.8, '15': -3345.9, '40': -7517.0, '80': -11174.3}); measured vs modeled SPHP speedup: current 2.1% vs 7.86% (gap -5.76 pp) → proposed 2.1% vs 7.86% (gap -5.76 pp)

**new_20261013** — LOTO regime MAPE% (R²) and the win check:

| regime | current | proposed | Δ pp |
|---|---|---|---|
| cold P2 | 2.15 (-1.2586) | 2.15 (-1.2586) | 0.0 |
| cold P2-SPHP | 7.83 (-5.583) | 6.42 (-2.8775) | -1.41 |
| warm P2 | 4.13 (0.2533) | 4.13 (0.2533) | 0.0 |
| warm P2-SPHP | 4.37 (0.1897) | 4.37 (0.1897) | 0.0 |

- targeted Δ: {'sphp_cold': -1.41}  (all strictly lower: True)
- guard Δ: {'p2_cold': 0.0, 'p2_warm': 0.0, 'sphp_warm': 0.0}  (all ≤ 0.25 pp: True)
- targeted residual pattern removed: True
- **win on new_20261013: True**

  - cold hit-credit C_s: fitted 64.0 ms (implied per-tier {'0': 6360.6, '15': 16247.0, '40': 1103.1, '80': 19563.6}); measured vs modeled SPHP speedup: current 25.18% vs 19.75% (gap 5.43 pp) → proposed 25.18% vs 23.17% (gap 2.01 pp)
  - warm hit-credit C_s: fitted 0.0 ms (implied per-tier {'0': 3381.1, '15': 558.2, '40': 70.2, '80': -1922.2}); measured vs modeled SPHP speedup: current 2.81% vs 3.37% (gap -0.56 pp) → proposed 2.81% vs 3.37% (gap -0.56 pp)


### RP-3


**old_20260918** — LOTO regime MAPE% (R²) and the win check:

| regime | current | proposed | Δ pp |
|---|---|---|---|
| cold P2 | 1.28 (-2.2672) | 1.6 (-3.7639) | 0.32 |
| cold P2-SPHP | 10.55 (-62.1645) | 9.93 (-55.3865) | -0.62 |
| warm P2 | 2.76 (0.076) | 1.89 (0.4218) | -0.87 |
| warm P2-SPHP | 5.81 (-0.2015) | 6.55 (-0.475) | 0.74 |

- targeted Δ: {'p2_cold': 0.32, 'p2_warm': -0.87}  (all strictly lower: False)
- guard Δ: {'sphp_cold': -0.62, 'sphp_warm': 0.74}  (all ≤ 0.25 pp: False)
- targeted residual pattern removed: False
- **win on old_20260918: False**

  - cold WAN fit: Twan_s = -23.42 + 0.6524·rtt (+serialization); measured P2 tier-mean RTT slope = -0.9282 vs analytic 0.5
    - P2 per-record residual slope on rtt: current 0.0632 (R²=0.0128) → proposed 0.0673 (R²=0.0146) pp/ms
    - per-tier mean resid (ms): current {'0': -0.94, '15': 1.27, '40': 1.82, '80': 4.59} → proposed {'0': -1.59, '15': 0.66, '40': 1.33, '80': 4.27}
  - warm WAN fit: Twan_s = -26.43 + 0.7415·rtt (+serialization); measured P2 tier-mean RTT slope = 0.7294 vs analytic 0.5
    - P2 per-record residual slope on rtt: current -0.0417 (R²=0.005) → proposed -0.0103 (R²=0.0003) pp/ms
    - per-tier mean resid (ms): current {'0': 4.33, '15': 4.92, '40': 6.7, '80': 0.95} → proposed {'0': 0.99, '15': 2.05, '40': 4.59, '80': 0.12}

**new_20261013** — LOTO regime MAPE% (R²) and the win check:

| regime | current | proposed | Δ pp |
|---|---|---|---|
| cold P2 | 2.15 (-1.2586) | 2.72 (-2.0524) | 0.57 |
| cold P2-SPHP | 7.83 (-5.583) | 6.37 (-3.8497) | -1.46 |
| warm P2 | 4.13 (0.2533) | 2.59 (0.6902) | -1.54 |
| warm P2-SPHP | 4.37 (0.1897) | 3.91 (0.5222) | -0.46 |

- targeted Δ: {'p2_cold': 0.57, 'p2_warm': -1.54}  (all strictly lower: False)
- guard Δ: {'sphp_cold': -1.46, 'sphp_warm': -0.46}  (all ≤ 0.25 pp: True)
- targeted residual pattern removed: False
- **win on new_20261013: False**

  - cold WAN fit: Twan_s = -32.78 + 0.9297·rtt (+serialization); measured P2 tier-mean RTT slope = -0.1332 vs analytic 0.5
    - P2 per-record residual slope on rtt: current 0.0506 (R²=0.0092) → proposed 0.0771 (R²=0.0216) pp/ms
    - per-tier mean resid (ms): current {'0': 2.4, '15': 2.48, '40': -0.0, '80': 6.77} → proposed {'0': 0.38, '15': 0.87, '40': -0.93, '80': 6.87}
  - warm WAN fit: Twan_s = -31.38 + 0.8884·rtt (+serialization); measured P2 tier-mean RTT slope = 0.9542 vs analytic 0.5
    - P2 per-record residual slope on rtt: current -0.1514 (R²=0.0152) → proposed -0.0658 (R²=0.0031) pp/ms
    - per-tier mean resid (ms): current {'0': 19.72, '15': 15.16, '40': 10.69, '80': 7.08} → proposed {'0': 12.75, '15': 9.78, '40': 7.56, '80': 7.02}


## Adopted-form crossover / policy deltas (analysis-level, 10-13 surface)

- **cold** SPHP advantage: 18.6–19.71% (current) → 21.83–23.12% (adopted); argmin {'P2-SPHP': 35, 'P2': 0} → {'P2-SPHP': 35, 'P2': 0}
- **cold** argmin policy pooled error: 9.72% → 5.11% (Δ -4.61 pp); argmin flips vs current: none
  - rtt 0.0: selected P2-SPHP, pred 1295.2 ms vs realized 1258.4 ms (2.93%)
  - rtt 15.0: selected P2-SPHP, pred 1302.7 ms vs realized 1204.9 ms (8.12%)
  - rtt 40.0: selected P2-SPHP, pred 1315.2 ms vs realized 1313.0 ms (0.17%)
  - rtt 80.0: selected P2-SPHP, pred 1335.2 ms vs realized 1222.5 ms (9.22%)
- **warm** SPHP advantage: 2.85–3.35% (current) → 2.85–3.35% (adopted); argmin {'P2-SPHP': 35, 'P2': 0} → {'P2-SPHP': 35, 'P2': 0}
- **warm** argmin policy pooled error: 5.57% → 5.57% (Δ 0.0 pp); argmin flips vs current: none
  - rtt 0.0: selected P2-SPHP, pred 543.7 ms vs realized 488.1 ms (11.4%)
  - rtt 15.0: selected P2-SPHP, pred 551.2 ms vs realized 524.0 ms (5.2%)
  - rtt 40.0: selected P2-SPHP, pred 563.7 ms vs realized 536.8 ms (5.01%)
  - rtt 80.0: selected P2-SPHP, pred 583.7 ms vs realized 579.8 ms (0.68%)

> Analysis-level only: the production cost model and systems/ are untouched by this script; adoption-port is a separate slice.
