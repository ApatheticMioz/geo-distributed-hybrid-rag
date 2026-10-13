# RP-2 production-port like-for-like re-validation (2026-10-14)

The adopted RP-2 cold-only SPHP hit credit has been ported into the production analytical cost model (`cost_model.py`).  This harness re-validates the ported form against the analysis-level numbers from `revision_board_eval_20261014`.


## LOTO regime MAPE (%) — production model vs RB-stored

| surface | state | arm | production | RB-stored | Δ |

|---|---|---|---|---|---|
| 20260918 | cold | P2 (guard) | 1.28 | 1.28 | 0.0 |
| 20260918 | cold | SPHP current (legacy) | 10.55 | 10.55 | 0.0 |
| 20260918 | cold | SPHP adopted (RP-2) | 1.72 | 1.72 | 0.0 |
| 20260918 | warm | P2 (guard) | 2.76 | 2.76 | 0.0 |
| 20260918 | warm | SPHP current (legacy) | 5.81 | 5.81 | 0.0 |
| 20260918 | warm | SPHP adopted (RP-2) | 5.81 | 5.81 | 0.0 |
| 20261013 | cold | P2 (guard) | 2.15 | 2.15 | 0.0 |
| 20261013 | cold | SPHP current (legacy) | 7.83 | 7.83 | 0.0 |
| 20261013 | cold | SPHP adopted (RP-2) | 6.42 | 6.42 | 0.0 |
| 20261013 | warm | P2 (guard) | 4.13 | 4.13 | 0.0 |
| 20261013 | warm | SPHP current (legacy) | 4.37 | 4.37 | 0.0 |
| 20261013 | warm | SPHP adopted (RP-2) | 4.37 | 4.37 | 0.0 |

## Full-fit cold credit C_c (ms) and warm C_w

| surface | state | production C | RB-live C | stored C |

|---|---|---|---|---|
| 20260918 | cold | 324.0 | 324.0 | 324.0 |
| 20260918 | warm | 0.0 | 0.0 | 0.0 |
| 20261013 | cold | 64.0 | 64.0 | 64.0 |
| 20261013 | warm | 0.0 | 0.0 | 0.0 |

## argmin placement policy (new surface; hydration=5.0 ms class default)

| state | pooled err current | pooled err adopted | Δ pp | argmin flips |

|---|---|---|---|---|
| cold | 9.72 | 5.11 | -4.61 | none |
| warm | 5.57 | 5.57 | 0.0 | none |

## Verdict: **ALL PASS** (42/42 checks)


> If any check FAILED, the port diverges from the analysis-level validation — the adopted form must NOT be claimed adopted (production) until reconciled.
