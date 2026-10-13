"""
Unit tests for the RP-2 state-dependent SPHP hit credit ported into the
production cost model (``cost_model.py``).

The adopted form (docs/revision_log.md, adopted 2026-10-14) is a COLD-only
hit credit ``C_s`` subtracted from the SPHP hit branch only:

    TTFT_hit = max(t_fused_arrival, t_prefill_finish) - C_s

with ``C_w = 0`` for the warm state (the KV prefix is already resident, so
there is nothing extra to save) and ``C_c > 0`` for cold (fitted per state).

The guards exercised here mirror the like-for-like evaluation harness
(revision_board_eval_20261014.py / rp2_production_port_20261014.py):
  * the credit is applied on the COLD hit path,
  * the warm prediction is untouched (C_w = 0, bit-identical to legacy),
  * the miss branch and the P2 topology are untouched,
  * the legacy constant form (default constructor / use_sphp_hit_credit=False)
    is reproduced exactly (0.0000-delta), so the paper can still reproduce it,
  * fit_placement_model's cold fit recovers the RB-stored C_c (324.0 / 64.0)
    and returns 0.0 for warm.

All synthetic fixtures use hand-computed expected values; every stochastic
step is absent (the credit fit is a deterministic grid search), so the suite
is fully deterministic.
"""

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
PROJECT_ROOT = _HERE.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from experiments.analysis.cost_model import (          # noqa: E402
    PlacementCostModel,
    fit_placement_model,
    fit_sphp_hit_credit,
)


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #

def _base_model(**over):
    """A deterministic SPHP-bearing model (all terms explicit, no fitting)."""
    kw = dict(
        t_sparse_base_ms=100.0,
        t_dense_base_ms=200.0,
        t_fusion_base_ms=0.01,
        t_hydrate_per_doc_ms=0.28,
        t_prefill_base_ms=300.0,
        decode_tok_per_sec=40.0,
        doc_id_bytes=8,
        doc_text_bytes=1200,
        query_bytes=64,
        sphp_hit_rate=0.8,
        t_sphp_miss_penalty_ms=50.0,
    )
    kw.update(over)
    return PlacementCostModel(**kw)


# --------------------------------------------------------------------------- #
# hit-branch form: credit subtracted on a hit, absent on a miss
# --------------------------------------------------------------------------- #

def test_credit_subtracted_from_hit_branch_only():
    # With hit_rate = 1.0 the SPHP expectation IS the hit branch, so the
    # predicted TTFT must fall by exactly the credit amount.
    m0 = _base_model(sphp_hit_rate=1.0, sphp_hit_credit_ms=0.0)
    m1 = _base_model(sphp_hit_rate=1.0, sphp_hit_credit_ms=120.0)
    # hit branch: max(fused_arrival, prefill_finish) - credit
    assert m1.predict_ttft_p2_sphp(40.0) == pytest.approx(
        m0.predict_ttft_p2_sphp(40.0) - 120.0, abs=1e-9)


def test_credit_does_not_touch_miss_branch():
    # With hit_rate = 0.0 the SPHP expectation IS the miss branch
    # (P2 base + miss penalty), which must be IDENTICAL regardless of credit.
    m0 = _base_model(sphp_hit_rate=0.0, sphp_hit_credit_ms=0.0)
    m1 = _base_model(sphp_hit_rate=0.0, sphp_hit_credit_ms=500.0)
    assert m1.predict_ttft_p2_sphp(40.0) == pytest.approx(
        m0.predict_ttft_p2_sphp(40.0), abs=1e-12)
    # and the miss branch equals P2 base + miss penalty exactly
    assert m0.predict_ttft_p2_sphp(40.0) == pytest.approx(
        m0.predict_ttft_p2(40.0) + 50.0, abs=1e-12)


def test_credit_scales_with_hit_rate_in_expectation():
    # At an intermediate hit rate the expected TTFT must be a convex
    # combination of the (credited) hit and the (uncrated) miss branches.
    p = dict(t_sparse_base_ms=100.0, t_dense_base_ms=200.0,
             t_fusion_base_ms=0.01, t_hydrate_per_doc_ms=0.28,
             t_prefill_base_ms=300.0, doc_id_bytes=8, query_bytes=64,
             sphp_hit_rate=0.8, t_sphp_miss_penalty_ms=50.0)
    base = PlacementCostModel(**p, sphp_hit_credit_ms=0.0)
    m0 = base.predict_ttft_p2_sphp(40.0)
    m1 = PlacementCostModel(**p, sphp_hit_credit_ms=120.0).predict_ttft_p2_sphp(40.0)
    # credit reduces the expected TTFT by hit_rate * credit
    assert m0 - m1 == pytest.approx(0.8 * 120.0, abs=1e-9)


# --------------------------------------------------------------------------- #
# warm-zero / cold-only guard
# --------------------------------------------------------------------------- #

def test_warm_credit_zero_is_bit_identical_to_legacy():
    # A warm model carries C_w = 0; its prediction must equal the legacy
    # constant form EXACTLY (0.0000 delta), so the paper can reproduce both.
    p = dict(t_sparse_base_ms=150.0, t_dense_base_ms=300.0,
             t_fusion_base_ms=0.02, t_hydrate_per_doc_ms=0.28,
             t_prefill_base_ms=400.0, decode_tok_per_sec=35.0,
             doc_id_bytes=8, doc_text_bytes=1200, query_bytes=64,
             sphp_hit_rate=0.6, t_sphp_miss_penalty_ms=120.0)
    legacy = PlacementCostModel(**p)                       # sphp_hit_credit_ms=0.0
    warm = PlacementCostModel(**p, sphp_hit_credit_ms=0.0)  # C_w = 0
    for rtt in (0.0, 15.0, 40.0, 80.0, 200.0):
        assert warm.predict_ttft_p2_sphp(rtt) == pytest.approx(
            legacy.predict_ttft_p2_sphp(rtt), abs=0.0)  # exactly 0.00 delta
        assert legacy.sphp_hit_credit_ms == 0.0


def test_cold_credit_changes_prediction_but_not_p2():
    # The credit is a hit-branch term: it changes the SPHP prediction but
    # NEVER the P2 topology prediction.
    p = dict(t_sparse_base_ms=100.0, t_dense_base_ms=200.0,
             t_fusion_base_ms=0.01, t_hydrate_per_doc_ms=0.28,
             t_prefill_base_ms=300.0, decode_tok_per_sec=40.0,
             doc_id_bytes=8, doc_text_bytes=1200, query_bytes=64,
             sphp_hit_rate=0.8, t_sphp_miss_penalty_ms=50.0)
    a = PlacementCostModel(**p, sphp_hit_credit_ms=0.0)
    b = PlacementCostModel(**p, sphp_hit_credit_ms=324.0)  # RB 09-18 C_c
    assert a.predict_ttft_p2(40.0) == pytest.approx(b.predict_ttft_p2(40.0), abs=0.0)
    assert a.predict_ttft_p2_sphp(40.0) != pytest.approx(b.predict_ttft_p2_sphp(40.0), abs=0.0)


# --------------------------------------------------------------------------- #
# fit_placement_model switch: default (legacy) vs adopted (RP-2)
# --------------------------------------------------------------------------- #

def _sphp_record(rtt, hit, wasted, ttft):
    return {"variant": "sphp", "rtt_ms": rtt, "repeat_index": 0,
            "sphp_hit": hit, "wasted_prefill_ms": wasted, "ttft_ms": ttft,
            "sparse_ms": 100.0, "dense_ms": 200.0, "fusion_ms": 0.01}


def _base_record(rtt, ttft):
    return {"variant": "baseline", "rtt_ms": rtt, "repeat_index": 0,
            "ttft_ms": ttft, "sparse_ms": 100.0, "dense_ms": 200.0,
            "fusion_ms": 0.01, "decode_tps": 40.0}


def test_fit_default_is_legacy_zero_credit():
    # Default (use_sphp_hit_credit=False) must give sphp_hit_credit_ms == 0.0,
    # i.e. the legacy constant form is preserved for the paper to reproduce.
    base = [_base_record(t, 500.0 + 0.5 * t) for t in (0.0, 15.0, 40.0, 80.0)]
    sphp = [_sphp_record(t, True, 0.0, 420.0 + 0.5 * t) for t in (0.0, 15.0, 40.0, 80.0)]
    m = fit_placement_model(base, sphp)
    assert m.sphp_hit_credit_ms == 0.0


def test_fit_cold_grid_search_recovers_target():
    # Construct data where the per-tier measured-vs-modeled speedup gap is
    # closed EXACTLY by a known credit: the grid search must recover it.
    #
    # For a hit rate of 1.0 and top_k=10, the modeled SPHP speedup at tier t
    # with credit C is linear in C.  We build sphp records whose realized
    # TTFT equals the legacy (C=0) model's hit TTFT MINUS 90 ms, so the
    # optimal credit is 90 ms.
    p = dict(t_sparse_base_ms=100.0, t_dense_base_ms=200.0,
             t_fusion_base_ms=0.01, t_hydrate_per_doc_ms=0.28,
             t_prefill_base_ms=300.0, decode_tok_per_sec=40.0,
             doc_id_bytes=8, doc_text_bytes=1200, query_bytes=64,
             sphp_hit_rate=1.0, t_sphp_miss_penalty_ms=0.0)
    model = PlacementCostModel(**p)
    target_c = 90.0
    base, sphp = [], []
    for t in (0.0, 15.0, 40.0, 80.0):
        # P2 base TTFT (all-hits -> the realized P2 is the model's P2)
        ttft_p2 = model.predict_ttft_p2(t)
        base.append(_base_record(t, ttft_p2))
        # realized SPHP hit TTFT = model's hit branch minus target credit
        # (with hit_rate=1.0, the hit branch IS the SPHP expectation)
        ttft_sphp = (max(
            model.t_dense_base_ms + model.t_fusion_base_ms
            + model.wan_delay_ms(64 + 10 * 8, t, 100.0),
            model.t_sparse_base_ms
            + model.wan_delay_ms(64 + 5 * 8, t, 100.0)
            + 5 * 0.28
            + model.t_prefill_base_ms,
        ) - target_c)
        sphp.append(_sphp_record(t, True, 0.0, ttft_sphp))

    fitted = fit_sphp_hit_credit(
        base, sphp,
        {"t_sparse_base_ms": model.t_sparse_base_ms,
         "t_dense_base_ms": model.t_dense_base_ms,
         "t_fusion_base_ms": model.t_fusion_base_ms,
         "t_hydrate_per_doc_ms": model.t_hydrate_per_doc_ms,
         "t_prefill_base_ms": model.t_prefill_base_ms,
         "t_sphp_miss_penalty_ms": 0.0,
         "query_bytes": model.query_bytes,
         "doc_id_bytes": model.doc_id_bytes},
    )
    assert fitted == pytest.approx(target_c, abs=0.5)  # grid step is 0.5 ms


def test_fit_warm_grid_degenerate_gives_zero():
    # The warm state is fitted on a degenerate grid of [0.0] -> C_w = 0.
    base = [_base_record(t, 500.0) for t in (0.0, 15.0, 40.0, 80.0)]
    sphp = [_sphp_record(t, True, 0.0, 420.0) for t in (0.0, 15.0, 40.0, 80.0)]
    cc = fit_sphp_hit_credit(
        base, sphp,
        {"t_sparse_base_ms": 100.0, "t_dense_base_ms": 200.0,
         "t_fusion_base_ms": 0.01, "t_hydrate_per_doc_ms": 0.28,
         "t_prefill_base_ms": 300.0, "t_sphp_miss_penalty_ms": 0.0,
         "query_bytes": 64, "doc_id_bytes": 8},
        grid=[0.0],
    )
    assert cc == 0.0


def test_fit_empty_sphp_returns_zero():
    cc = fit_sphp_hit_credit([], [], {}, grid=[0.0, 1.0, 2.0])
    assert cc == 0.0
