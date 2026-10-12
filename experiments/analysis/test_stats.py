"""Pytest suite for the campaign-v2 statistics engine (``stats.py``).

Style follows ``experiments/eval/test_metrics.py``: synthetic fixtures
with hand-computed expected values, ``pytest.approx`` for float
comparisons.  Every stochastic function is seeded so results are
deterministic; coverage / p-uniformity tests use a fixed seed and a
moderate resample budget so the suite stays fast while still
meaningful.

Hand-computed anchors:
  * Holm on [0.01,0.02,0.04,0.08] (already ascending, n=4):
        rank1 4*0.01=0.04, rank2 3*0.02=0.06, rank3 2*0.04=0.08,
        rank4 1*0.08=0.08, then monotonic (cummax) => [0.04,0.06,0.08,0.08].
  * BH on the same family:
        scaled 0.01*4/1=0.04, 0.02*4/2=0.04, 0.04*4/3=0.05333,
        0.08*4/4=0.08, then monotonic (right-to-left cummin) =>
        [0.04,0.04,0.05333,0.08].
  * Wilson (z=1.96) for k=10,n=20: (0.2993, 0.7007) — hand-derived from
        p=0.5, denom=1+1.96^2/20=1.19216,
        center=(0.5+1.96^2/40)/1.19216, half=1.96*sqrt(0.25/20+1.96^2/1600)/1.19216.
  * Cliff's delta on a=[1,2,3,4,5], b=[2,3,4,5,6]:
        20 of 25 cross-pairs are b>a, 0 a>b, 5 ties => -20/25 = -0.8
        ... but our estimator counts strict a>b minus strict b>a over
        all cross pairs: a>b for (a=2,b=2?no), compute below in test.
  * Power round-trip: n = 15.68·CV²/rel_d², so achieved_power(n, rel_d,
        CV) = Φ(0.84) = 0.7995 (the doc's 0.80 to 4dp).
"""

import math

import numpy as np
import pytest

from experiments.analysis.stats import (
    achieved_power,
    benjamini_hochberg,
    bootstrap_ci,
    bootstrap_delta_ci,
    cliffs_delta,
    cohens_d,
    effect_size,
    holm,
    mde,
    percentile,
    permutation_test,
    sample_size,
    wilson_ci,
)


# ---------------------------------------------------------------------------
# percentile
# ---------------------------------------------------------------------------

def test_percentile_linear_interpolation():
    # n=4: p25 -> 25%*(4-1)=0.75 => 1 + 0.75*(2-1) = 1.75
    assert percentile([1, 2, 3, 4], 25) == pytest.approx(1.75)
    # p95 -> 95%*3 = 2.85 => 3 + 0.85*(4-3) = 3.85
    assert percentile([1, 2, 3, 4], 95) == pytest.approx(3.85)
    # p50 (median) of even n is the mean of the two middle values
    assert percentile([1, 2, 3, 4], 50) == pytest.approx(2.5)


def test_percentile_matches_numpy_type7():
    rng = np.random.default_rng(0)
    x = rng.exponential(1.0, 100)
    for p in (10, 50, 90, 95, 99):
        assert percentile(x, p) == pytest.approx(
            float(np.percentile(x, p, method="linear")), rel=1e-12)


def test_percentile_single_value_and_bounds():
    assert percentile([7.0], 99) == pytest.approx(7.0)
    assert percentile([3, 9], 0) == pytest.approx(3)
    assert percentile([3, 9], 100) == pytest.approx(9)


def test_percentile_rejects_empty_and_bad_p():
    with pytest.raises(ValueError):
        percentile([], 50)
    with pytest.raises(ValueError):
        percentile([1, 2], 101)


# ---------------------------------------------------------------------------
# bootstrap_ci
# ---------------------------------------------------------------------------

def test_bootstrap_ci_contains_point_estimate():
    rng = np.random.default_rng(3)
    x = rng.uniform(0, 1, 30)
    for method in ("bca", "percentile"):
        lo, hi = bootstrap_ci(x, "mean", method=method, B=500, seed=7)
        assert lo <= np.mean(x) <= hi
        assert lo < hi


def test_bootstrap_ci_coverage_mean():
    # A 95% CI for the mean should contain the (unknown) population mean
    # for the large majority of independent draws.  Uniform(0,1) has mean
    # 0.5; use 200 draws of n=25 to estimate empirical coverage.
    rng = np.random.default_rng(11)
    hits = 0
    for i in range(200):
        x = rng.uniform(0, 1, 25)
        lo, hi = bootstrap_ci(x, "mean", method="percentile", B=100, seed=1000 + i)
        if lo <= 0.5 <= hi:
            hits += 1
    assert 0.85 <= hits / 200 <= 0.99  # looser than nominal 0.95; n=25 is small


def test_bootstrap_ci_p95_wider_than_p50():
    # For a right-skewed sample the p95 CI should be far wider than the
    # p50 CI (the tail is less stable).
    rng = np.random.default_rng(5)
    x = np.exp(rng.normal(0, 0.5, 60))
    lo50, hi50 = bootstrap_ci(x, "p50", "percentile", B=500, seed=2)
    lo95, hi95 = bootstrap_ci(x, "p95", "percentile", B=500, seed=2)
    assert (hi95 - lo95) > (hi50 - lo50)


def test_bootstrap_ci_rejects_small_n_and_small_B():
    with pytest.raises(ValueError):
        bootstrap_ci([1.0, 2.0], "mean", "bca", B=100, seed=0)  # n<3
    with pytest.raises(ValueError):
        bootstrap_ci(np.arange(10.0), "mean", "bca", B=5, seed=0)  # B<20


def test_bootstrap_ci_deterministic_given_seed():
    x = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
    a = bootstrap_ci(x, "median", "bca", B=200, seed=42)
    b = bootstrap_ci(x, "median", "bca", B=200, seed=42)
    assert a == b


# ---------------------------------------------------------------------------
# bootstrap_delta_ci
# ---------------------------------------------------------------------------

def test_bootstrap_delta_ci_point_is_mean_difference():
    a = [10, 20, 30, 40]
    b = [15, 25, 35, 45]
    r = bootstrap_delta_ci(a, b, stat="mean", method="bca", B=500, seed=1)
    # mean(a)=25, mean(b)=30 => delta = -5
    assert r["delta"] == pytest.approx(-5.0)
    assert r["ci"][0] < r["delta"] < r["ci"][1]
    # relative delta = delta / mean(b) = -5/30
    assert r["relative_delta_pct"] == pytest.approx(100 * -5.0 / 30.0)


def test_bootstrap_delta_ci_sign_and_separation():
    # a is clearly larger than b: delta > 0, CI must exclude 0.
    rng = np.random.default_rng(9)
    a = rng.normal(100, 5, 40)
    b = rng.normal(80, 5, 40)
    r = bootstrap_delta_ci(a, b, stat="mean", method="bca", B=500, seed=3)
    assert r["delta"] > 0
    assert r["ci"][0] > 0  # well-separated -> CI excludes 0


def test_bootstrap_delta_ci_paired_uses_difference_vector():
    # Paired delta must equal the single-sample CI of the difference.
    rng = np.random.default_rng(4)
    a = rng.normal(0, 1, 30)
    b = rng.normal(0.5, 1, 30)
    d = a - b
    r = bootstrap_delta_ci(a, b, stat="mean", method="percentile",
                           paired=True, B=500, seed=6)
    # delta = mean(a - b)
    assert r["delta"] == pytest.approx(float(np.mean(d)))
    ref_lo, ref_hi = bootstrap_ci(d, "mean", "percentile", B=500, seed=6)
    assert r["ci"] == pytest.approx([ref_lo, ref_hi], rel=1e-6)


def test_bootstrap_delta_ci_requires_equal_len_when_paired():
    with pytest.raises(ValueError):
        bootstrap_delta_ci([1, 2, 3], [1, 2], paired=True, B=100, seed=0)


# ---------------------------------------------------------------------------
# permutation_test
# ---------------------------------------------------------------------------

def test_permutation_identical_samples_p_near_one():
    # Under H0 (identical samples) every permutation gives obs=0, so
    # |t|>=|obs|=0 is always true -> p = (N+1)/(N+1) = 1.
    p = permutation_test([1, 2, 3, 4, 5], [1, 2, 3, 4, 5],
                         stat="mean", alternative="two-sided",
                         n_resamples=500, seed=1)["p"]
    assert p == pytest.approx(1.0)


def test_permutation_shifted_samples_p_small():
    a = [1, 2, 3, 4, 5]
    b = [6, 7, 8, 9, 10]  # b clearly larger
    p = permutation_test(a, b, stat="mean", alternative="two-sided",
                         n_resamples=5000, seed=1)["p"]
    assert p < 0.05


def test_permutation_directionality():
    a = [1, 2, 3, 4, 5]
    b = [6, 7, 8, 9, 10]
    less = permutation_test(a, b, stat="mean", alternative="less",
                            n_resamples=5000, seed=1)["p"]  # a<b -> small
    greater = permutation_test(a, b, stat="mean", alternative="greater",
                               n_resamples=5000, seed=1)["p"]  # a<b -> ~1
    assert less < 0.05
    assert greater > 0.95


def test_permutation_median_statistic():
    a = [1, 2, 3, 4, 5]
    b = [6, 7, 8, 9, 10]
    p = permutation_test(a, b, stat="median", alternative="two-sided",
                         n_resamples=5000, seed=2)["p"]
    assert p < 0.05


def test_permutation_p_bounded_and_deterministic():
    rng = np.random.default_rng(0)
    a = rng.normal(0, 1, 20)
    b = rng.normal(0, 1, 20)
    r1 = permutation_test(a, b, "mean", "two-sided", 500, seed=8)
    r2 = permutation_test(a, b, "mean", "two-sided", 500, seed=8)
    assert r1["p"] == r2["p"]
    assert 0.0 < r1["p"] <= 1.0


# ---------------------------------------------------------------------------
# wilson_ci
# ---------------------------------------------------------------------------

def test_wilson_ci_known_value():
    lo, hi = wilson_ci(10, 20)
    assert lo == pytest.approx(0.29929491, abs=1e-6)
    assert hi == pytest.approx(0.70070509, abs=1e-6)


def test_wilson_ci_edges():
    # zero successes -> lower bound 0
    lo, hi = wilson_ci(0, 10)
    assert lo == pytest.approx(0.0, abs=1e-9)
    assert 0 < hi < 0.5
    # all successes -> upper bound clamped to 1
    lo, hi = wilson_ci(5, 5)
    assert hi == pytest.approx(1.0)
    assert lo > 0.5


def test_wilson_ci_symmetric_about_proportion():
    # Wilson interval is centered near the raw proportion (slightly
    # shifted toward 0.5, but close).
    lo, hi = wilson_ci(80, 100)
    assert 0.7 < lo < 0.8 < hi < 0.9
    # the interval midpoint sits close to the raw proportion 0.8
    assert (lo + hi) / 2.0 == pytest.approx(0.8, abs=0.03)


def test_wilson_ci_rejects_bad_input():
    with pytest.raises(ValueError):
        wilson_ci(-1, 10)
    with pytest.raises(ValueError):
        wilson_ci(11, 10)
    with pytest.raises(ValueError):
        wilson_ci(0, 0)


# ---------------------------------------------------------------------------
# holm
# ---------------------------------------------------------------------------

def test_holm_hand_computed():
    # Already ascending, n=4.  Raw scaled: 4*.01,3*.02,2*.04,1*.08 then
    # cummax: [0.04, 0.06, 0.08, 0.08].
    adj = holm([0.01, 0.02, 0.04, 0.08])
    assert adj == pytest.approx([0.04, 0.06, 0.08, 0.08])


def test_holm_preserves_original_order():
    # Unsorted input: smallest p gets the smallest adjusted value, and the
    # adjusted values come back in the *original* (unsorted) order.
    raw = [0.08, 0.01, 0.04, 0.02]
    adj = holm(raw)
    # position 1 (raw 0.01, the min) must be the smallest adjusted value
    assert adj[1] == min(adj)
    # recompute expected: sort by value -> [0.01,0.02,0.04,0.08]
    # adj in sorted order = [0.04,0.06,0.08,0.08]; map back to original
    # order [0.08->0.08, 0.01->0.04, 0.04->0.08, 0.02->0.06]
    assert adj == pytest.approx([0.08, 0.04, 0.08, 0.06])


def test_holm_more_conservative_than_bh():
    raw = [0.001, 0.01, 0.03, 0.05, 0.4]
    h = holm(raw)
    b = benjamini_hochberg(raw)
    # Holm (FWER) adjusted p-values are always >= BH (FDR) ones, elementwise.
    for h_i, b_i in zip(h, b):
        assert h_i >= b_i - 1e-12


def test_holm_single_and_empty():
    assert holm([0.03]) == pytest.approx([0.03])
    assert holm([]) == []


# ---------------------------------------------------------------------------
# benjamini_hochberg
# ---------------------------------------------------------------------------

def test_benjamini_hochberg_hand_computed():
    raw = [0.01, 0.02, 0.04, 0.08]
    # scaled: 0.01*4/1, 0.02*4/2, 0.04*4/3, 0.08*4/4
    # = [0.04, 0.04, 0.05333, 0.08]; right-to-left cummin -> same (already
    # nondecreasing), so [0.04, 0.04, 0.05333, 0.08].
    adj = benjamini_hochberg(raw)
    assert adj == pytest.approx([0.04, 0.04, 0.0533333, 0.08], abs=1e-6)


def test_benjamini_hochberg_monotonicity():
    # BH's defining property: adjusted p-values must be nondecreasing in the
    # *ranked* order.  Use a family where naive scaling would violate this.
    raw = [0.001, 0.019, 0.021, 0.025, 0.026, 0.3]
    adj = benjamini_hochberg(raw)
    ranked = [adj[i] for i in np.argsort(raw)]
    assert all(ranked[i] <= ranked[i + 1] + 1e-12 for i in range(len(ranked) - 1))


def test_benjamini_hochberg_single_and_empty():
    assert benjamini_hochberg([0.07]) == pytest.approx([0.07])
    assert benjamini_hochberg([]) == []


# ---------------------------------------------------------------------------
# effect sizes
# ---------------------------------------------------------------------------

def test_cohens_d_independent_pooled_sd():
    a = [1, 2, 3, 4, 5]
    b = [2, 3, 4, 5, 6]
    # means 3 and 4, diff -1.  Both SDs (ddof=1) are sqrt(2.5).
    pooled = math.sqrt(((4 * 2.5) + (4 * 2.5)) / 8)  # = sqrt(2.5)
    d = cohens_d(a, b, paired=False)
    assert d == pytest.approx(-1.0 / math.sqrt(2.5))


def test_cohens_d_paired_mean_over_sd_of_diff():
    a = [10, 20, 30]
    b = [8, 21, 29]  # diffs [2,-1,1]
    diffs = np.array([2.0, -1.0, 1.0])
    d = cohens_d(a, b, paired=True)
    # d = mean(diff) / sd(diff) with ddof=1
    assert d == pytest.approx(diffs.mean() / diffs.std(ddof=1))


def test_cohens_d_equal_samples_zero():
    # identical samples -> delta 0 -> d 0
    x = [1, 2, 3, 4, 5]
    assert cohens_d(x, list(x), paired=False) == pytest.approx(0.0)


def test_cliffs_delta_hand_computed():
    a = [1, 2, 3, 4, 5]
    b = [2, 3, 4, 5, 6]
    # count strict a>b and a<b over all 25 cross-pairs
    gt = sum(1 for x in a for y in b if x > y)
    lt = sum(1 for x in a for y in b if x < y)
    assert cliffs_delta(a, b) == pytest.approx((gt - lt) / 25.0)
    # a is shifted down by 1 relative to b, so delta must be negative
    assert cliffs_delta(a, b) < 0
    # and symmetric sign flip
    assert cliffs_delta(b, a) == pytest.approx(-cliffs_delta(a, b))


def test_effect_size_returns_both():
    a = [1, 2, 3, 4, 5]
    b = [2, 3, 4, 5, 6]
    es = effect_size(a, b)
    assert "cohens_d" in es and "cliffs_delta" in es
    assert es["cliffs_delta"] is not None
    # paired -> cliffs_delta is None (not defined for a difference vector)
    es_p = effect_size(a, b, paired=True)
    assert es_p["cliffs_delta"] is None


# ---------------------------------------------------------------------------
# power / MDE / N round-trips
# ---------------------------------------------------------------------------

def test_sample_size_matches_doc_table():
    # docs/campaign_methodology.md RQ-a2/a4: n = 15.68·CV²/rel_d² (two-sample)
    # and n = 3.136·CV²/rel_d² (paired, ρ=0.8).
    assert sample_size(0.10, 0.15, "two-sample") == pytest.approx(15.68 * 0.15 ** 2 / 0.10 ** 2)
    assert sample_size(0.10, 0.15, "paired") == pytest.approx(3.136 * 0.15 ** 2 / 0.10 ** 2)
    # the doc's own numbers: 10% @ CV0.15 -> 35; 5% @ CV0.15 -> 141
    assert sample_size(0.10, 0.15, "two-sample") == pytest.approx(35.28, abs=0.1)
    assert sample_size(0.05, 0.15, "two-sample") == pytest.approx(141.12, abs=0.1)
    # paired 5% @ CV0.20 -> 50
    assert sample_size(0.05, 0.20, "paired") == pytest.approx(50.18, abs=0.1)


def test_power_round_trip_is_0_80():
    # achieved_power at the N given by sample_size must return the doc's
    # 0.80 power (Φ(0.84) = 0.7995).
    for design in ("two-sample", "paired"):
        for cv in (0.10, 0.15, 0.20):
            for rd in (0.05, 0.10):
                n = sample_size(rd, cv, design)
                p = achieved_power(n, rd, cv, design)
                assert p == pytest.approx(0.7995, abs=0.001), (design, cv, rd)


def test_mde_inverse_of_sample_size():
    # mde(n, cv) should return the rel_d that sample_size would invert.
    for design in ("two-sample", "paired"):
        for cv in (0.10, 0.20):
            for rd in (0.05, 0.10):
                n = sample_size(rd, cv, design)
                assert mde(n, cv, design) == pytest.approx(rd, rel=1e-6)


def test_power_monotonic_in_n_and_effect():
    # more N -> more power; bigger effect -> more power
    cv = 0.15
    assert achieved_power(30, 0.10, cv, "two-sample") < \
        achieved_power(60, 0.10, cv, "two-sample")
    assert achieved_power(50, 0.05, cv, "two-sample") < \
        achieved_power(50, 0.10, cv, "two-sample")
    # power in [0,1]
    assert 0.0 <= achieved_power(5, 0.10, cv, "two-sample") <= 1.0


def test_power_paired_beats_two_sample_at_same_n():
    # The whole point of the paired design: at equal N it has more power.
    cv = 0.15
    n = 50
    p_two = achieved_power(n, 0.10, cv, "two-sample")
    p_paired = achieved_power(n, 0.10, cv, "paired")
    assert p_paired > p_two
