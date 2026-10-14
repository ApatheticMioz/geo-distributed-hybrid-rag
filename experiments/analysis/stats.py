#!/usr/bin/env python3
"""Campaign-v2 statistics engine (workstream AB-1).

Nonparametric inference for heavy-tailed latency data, grounded in:

* ``docs/campaign_methodology.md`` (LR-3) — the pre-registered plan:
  permutation tests as the distribution-free primary test for arm
  comparison (RQ-c1), bootstrap percentile/BCa CIs on every reported
  percentile and on between-arm deltas (RQ-c2, RQ-e1), Holm FWER for the
  confirmatory family and Benjamini-Hochberg FDR for the exploratory
  sweep (RQ-d1), power arithmetic ``n = 15.68·CV²/rel_d²`` (disjoint) and
  ``n = 3.136·CV²/rel_d²`` (paired, ρ=0.8) (RQ-a2/a4), Cohen's ``d``
  secondary to the relative delta (RQ-e1), cold/warm treated as strata
  not replicates (RQ-b2).
* ``docs/related_work_landscape.md`` LR-7 — the nine-element methodology
  package: p50/p95/p99 with BCa CIs, permutation tests, powered N,
  Holm/BH multiplicity.
* ``experiments/analysis/results/_campaign_analysis.py`` — the one-shot
  analysis; we reuse its conventions (percentile linear interpolation,
  Wilson CI, B=1000, seed 42).

Every stochastic function is seeded through ``numpy.random.default_rng``
and is deterministic for a given seed.  The module is importable
(``from experiments.analysis.stats import ...`` after putting the
project root on ``sys.path``) and has a CLI entry for the sanity run::

    python experiments/analysis/stats.py \
        [--campaign experiments/bench/campaigns/campaign_20260918T130756Z.jsonl] \
        [--out experiments/analysis/results/stats_sanity_20260918campaign.json] \
        [--seed 42] [--b 1000] [--perm 10000]

The CLI reproduces the published point estimates of
``results/campaign_analysis.json``, asserts they land within tolerance,
then adds the inferential layer (BCa CIs, permutation p-values,
Holm-adjusted confirmatory family, BH exploratory sweep).

The sanity gate does **not** modify the one-shot analysis or its
``campaign_analysis.json`` output; it only adds new evidence.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

# numpy / scipy are OPTIONAL at import time.  ``wilson_ci`` (the closed-form
# Wilson score interval) and the pure-python effect-size / multiplicity helpers
# only need the standard library, so this module must import cleanly on a bare
# orchestration host (the campaign driver) that lacks numpy/scipy.  When the
# deps are absent, the numpy/scipy-backed routines (percentile, bootstrap,
# permutation, cohens_d/cliffs_delta) raise a clear error at CALL time rather
# than at import time, so the pure functions remain usable.
try:
    import numpy as np
    from scipy.stats import norm
    _HAS_NUMPY = True
except ImportError:  # numpy and/or scipy missing on this host
    np = None  # type: ignore[assignment]
    norm = None  # type: ignore[assignment]
    _HAS_NUMPY = False

if _HAS_NUMPY:
    Numbers = Union[float, int, np.ndarray, Sequence[float]]
else:
    Numbers = Union[float, int, Sequence[float]]


def _require_numpy() -> None:
    """Raise a clear error if this routine needs numpy/scipy and they are absent."""
    if not _HAS_NUMPY:
        raise ImportError(
            "this routine requires numpy/scipy, which are not installed on this "
            "host (the pure-python helpers such as wilson_ci still work)")

__all__ = [
    "percentile",
    "bootstrap_ci",
    "bootstrap_delta_ci",
    "permutation_test",
    "wilson_ci",
    "holm",
    "benjamini_hochberg",
    "cohens_d",
    "cliffs_delta",
    "effect_size",
    "sample_size",
    "mde",
    "achieved_power",
]


# ---------------------------------------------------------------------------
# Descriptive
# ---------------------------------------------------------------------------

def percentile(values: Numbers, p: float) -> float:
    """Linear-interpolation percentile, ``p`` in 0..100.

    Consistent with ``experiments/bench/campaign.py::percentile`` and
    ``numpy.percentile(..., method='linear')`` (i.e. the R "type 7"
    estimator); note this is *not* the index-rounding used by
    ``experiments/eval/make_quality_report.py``.  ``p`` is a percentile
    (95, not 0.95).
    """
    _require_numpy()
    arr = np.asarray(values, dtype=float).ravel()
    if arr.size == 0:
        raise ValueError("percentile of an empty sample")
    if not 0.0 <= p <= 100.0:
        raise ValueError(f"percentile p={p} out of [0, 100]")
    return float(np.percentile(arr, p, method="linear"))


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

def _stat_fn(stat: Union[str, Callable[[np.ndarray], float]]) -> Callable[[np.ndarray], float]:
    if isinstance(stat, str):
        s = stat.lower()
        if s == "median":
            s = "p50"
        if s in ("mean",):
            return lambda x: float(np.mean(x))
        if s in ("p50", "p95", "p99"):
            q = int(s[1:]) / 100.0
            return lambda x: float(np.quantile(x, q, method="linear"))
        if s == "var":
            return lambda x: float(np.var(x, ddof=1))
        if s == "std":
            return lambda x: float(np.std(x, ddof=1))
    if callable(stat):
        return stat
    raise ValueError(f"unknown statistic {stat!r} (use 'mean', 'median'/'p50', 'p95', 'p99', 'var', 'std', or a callable)")


def bootstrap_ci(
    values: Numbers,
    stat: Union[str, Callable[[np.ndarray], float]] = "median",
    method: str = "bca",
    B: int = 1000,
    seed: Optional[int] = None,
    alpha: float = 0.05,
) -> Tuple[float, float]:
    """Confidence interval for a statistic of a single sample.

    ``method='bca'`` (BCa, Efron & Tibshirani 1993 — preferred per LR-7)
    corrects the percentile interval for bias and skewness of the
    bootstrap distribution.  ``method='percentile'`` is the plain
    alpha/2, 1-alpha/2 quantile of the bootstrap replicates (fallback,
    and the convention used by ``_campaign_analysis.py``).

    ``stat``: ``'median'``/``'p50'``, ``'p95'``, ``'p99'``, ``'mean'``,
    ``'var'``, ``'std'``, or any ``f: ndarray -> float``.

    Deterministic given ``seed``.
    """
    _require_numpy()
    if B < 20:
        raise ValueError(f"B={B} too small for a CI")
    x = np.asarray(values, dtype=float)
    if x.ndim != 1:
        raise ValueError("values must be one-dimensional")
    n = x.size
    if n < 3:
        raise ValueError(f"n={n} too small to bootstrap")

    f = _stat_fn(stat)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(B, n))
    boot = np.array([f(x[i]) for i in idx], dtype=float)

    lo_q, hi_q = alpha / 2.0, 1.0 - alpha / 2.0
    if method == "percentile":
        lo = float(np.quantile(boot, lo_q, method="linear"))
        hi = float(np.quantile(boot, hi_q, method="linear"))
    elif method == "bca":
        th = f(x)
        se = boot.std(ddof=1)
        if se == 0.0:  # degenerate bootstrap (e.g. constant sample)
            return (th, th)
        # Jackknife (leave-one-out) for the acceleration; bias term uses `boot`.
        jack = np.array([f(np.delete(x, i)) for i in range(n)], dtype=float)
        lo, hi = _bca_quantiles(boot, jack, th, alpha)
    else:
        raise ValueError(f"unknown bootstrap method {method!r} (use 'bca' or 'percentile')")
    return (min(lo, hi), max(lo, hi))


def _cdf(fraction: float) -> float:
    """Standard-normal CDF via ``math.erf`` (no scipy needed at the call site)."""
    return 0.5 * (1.0 + math.erf(fraction / math.sqrt(2.0)))


def _jackknife_a(jack: np.ndarray) -> float:
    """BCa acceleration ``a`` from a leave-one-out jackknife vector.

    Implemented exactly as ``scipy.stats.bootstrap(method='BCa')``
    (Eq. 15.36 of the reference it follows): with
    ``U_j = (n-1)·(th̄_j - th_j)`` where ``th̄_j`` is the jackknife
    mean, ``a = (1/6)·[ΣU³/n³] / [ΣU²/n²]^{3/2}``.  Falls back to 0
    when the jackknife has no variance (e.g. the mean, whose leave-one-out
    is nearly constant — acceleration is a second-order refinement in that
    case and the bias term carries the correction).
    """
    n = len(jack)
    if n < 2:
        return 0.0
    theta_dot = jack.mean()
    U = (n - 1) * (theta_dot - jack)
    dens = float(np.sum(U ** 2) / n ** 2)
    if dens == 0.0:
        return 0.0
    nums = float(np.sum(U ** 3) / n ** 3)
    return float((1.0 / 6.0) * nums / dens ** 1.5)


def _bca_quantiles(boot: np.ndarray, jack: np.ndarray, point: float, alpha: float) -> Tuple[float, float]:
    """BCa adjusted interval quantile levels (Efron & Tibshirani 1993, eq. 15.36).

    ``z0`` is the bias-correction (normal deviate of the fraction of
    bootstrap replicates below the point estimate) and ``a`` is the
    acceleration.  The adjusted 95% levels are
    ``Φ(z0 + (z0+z_α)/(1-a(z0+z_α)))`` and
    ``Φ(z0 + (z0+z_{1-α})/(1-a(z0+z_{1-α})))``.
    """
    frac = float(np.mean(boot < point))
    frac = min(max(frac, 1.0 / (len(boot) + 1)), 1.0 - 1.0 / (len(boot) + 1))
    z0 = norm.ppf(frac)
    a = _jackknife_a(jack)
    z_lo = norm.ppf(alpha / 2.0)
    z_hi = norm.ppf(1.0 - alpha / 2.0)
    num_lo = z0 + z_lo
    num_hi = z0 + z_hi
    p_lo = _cdf(z0 + num_lo / (1.0 - a * num_lo))
    p_hi = _cdf(z0 + num_hi / (1.0 - a * num_hi))
    p_lo = min(max(p_lo, 1e-6), 1.0 - 1e-6)
    p_hi = min(max(p_hi, 1e-6), 1.0 - 1e-6)
    return (float(np.quantile(boot, p_lo, method="linear")),
            float(np.quantile(boot, p_hi, method="linear")))


def bootstrap_delta_ci(
    a: Numbers,
    b: Numbers,
    stat: str = "mean",
    method: str = "bca",
    paired: bool = False,
    B: int = 1000,
    seed: Optional[int] = None,
    alpha: float = 0.05,
) -> Dict[str, Any]:
    """Confidence interval on the between-group difference ``a - b``.

    Independent samples by default (the campaign's disjoint per-arm
    slices, RQ-b1); ``paired=True`` resamples pairs jointly and takes the
    statistic of the difference vector.  Returns::

        {"delta": point estimate, "ci": [lo, hi],
         "relative_delta_pct": delta / b_stat * 100,
         "relative_ci_pct": (ci_lo / b_stat * 100, ci_hi / b_stat * 100)}

    The relative quantities are the primary effect size (RQ-e1).
    """
    _require_numpy()
    fa = _stat_fn(stat)
    rng = np.random.default_rng(seed)
    if not paired:
        xa = np.asarray(a, dtype=float)
        xb = np.asarray(b, dtype=float)
        if len(xa) < 3 or len(xb) < 3:
            raise ValueError("need n>=3 per arm for bootstrap")
        if B < 20:
            raise ValueError(f"B={B} too small")
        ia = rng.integers(0, len(xa), size=(B, len(xa)))
        ib = rng.integers(0, len(xb), size=(B, len(xb)))
        deltas = np.array([fa(xa[ia[i]]) - fa(xb[ib[i]]) for i in range(B)], dtype=float)
        point = fa(xa) - fa(xb)
        # BCa acceleration via the leave-one-out jackknife of the
        # two-sample statistic: j_i = f(xa[-i]) - f(xb),
        # j_{n_a+i} = f(xa) - f(xb[-i]).
        ja = np.array([fa(np.delete(xa, i)) for i in range(len(xa))], dtype=float) - fa(xb)
        jb = fa(xa) - np.array([fa(np.delete(xb, i)) for i in range(len(xb))], dtype=float)
        jack = np.concatenate([ja, jb])
    else:
        if len(a) != len(b):
            raise ValueError("paired bootstrap requires equal-length samples")
        d = np.asarray(a, dtype=float) - np.asarray(b, dtype=float)
        if len(d) < 3:
            raise ValueError("n<3 pairs, cannot bootstrap")
        if B < 20:
            raise ValueError(f"B={B} too small")
        idx = rng.integers(0, len(d), size=(B, len(d)))
        deltas = np.array([fa(d[idx[i]]) for i in range(B)], dtype=float)
        point = fa(d)
        # BCa acceleration via the leave-one-out jackknife of the
        # difference-vector statistic.
        jack = np.array([fa(np.delete(d, i)) for i in range(len(d))], dtype=float)

    if method == "percentile":
        lo = float(np.quantile(deltas, alpha / 2.0, method="linear"))
        hi = float(np.quantile(deltas, 1.0 - alpha / 2.0, method="linear"))
    else:  # bca on the delta distribution
        th = point
        se = deltas.std(ddof=1)
        if se == 0.0:
            lo = hi = th
        else:
            lo, hi = _bca_quantiles(deltas, jack, th, alpha)

    b_stat = fa(np.asarray(b, dtype=float))
    rel = (100.0 * (point / b_stat) if b_stat != 0 else None)
    rel_lo = 100.0 * (lo / b_stat) if b_stat != 0 else None
    rel_hi = 100.0 * (hi / b_stat) if b_stat != 0 else None
    return {
        "delta": float(point),
        "ci": [float(min(lo, hi)), float(max(lo, hi))],
        "relative_delta_pct": float(rel) if rel is not None else None,
        "relative_ci_pct": [float(rel_lo), float(rel_hi)] if rel_lo is not None and rel_hi is not None else None,
        "method": f"bootstrap-{method}" + ("-paired" if paired else "-independent"),
    }



# ---------------------------------------------------------------------------
# Permutation test
# ---------------------------------------------------------------------------

def _perm_stat(xs: np.ndarray, n_a: int, stat: str) -> float:
    if stat in ("mean", "median", "p50", "p95", "p99", "std", "var"):
        f = _stat_fn(stat)
        return f(xs[:n_a]) - f(xs[n_a:])
    if callable(stat):
        return float(stat(xs[:n_a], xs[n_a:]))
    raise ValueError(f"unknown permutation statistic {stat!r}")


def permutation_test(
    a: Numbers,
    b: Numbers,
    stat: Union[str, Callable[[np.ndarray, np.ndarray], float]] = "mean",
    alternative: str = "two-sided",
    n_resamples: int = 10000,
    seed: Optional[int] = None,
) -> Dict[str, Any]:
    """Permutation (randomization) test of ``a`` vs ``b`` (independent).

    Distribution-free: no normality assumption, which is why it is the
    primary test for heavy-tailed TTFT in the campaign methodology
    (RQ-c1).  Under H0 the group labels are exchangeable, so the null
    distribution of the statistic is its distribution over all
    (equivalently, sampled) label permutations.

    ``stat``: ``'mean'`` (default) or ``'median'``/``'p50'`` — tests the
    difference in that location between the two arms; or a callable
    ``(arr_a, arr_b) -> float``.

    ``alternative``: ``'two-sided'``, ``'greater'`` (a > b), ``'less'``
    (a < b).  p is the fraction of resampled statistics at least as
    extreme as the observed one, with a +1 continuity correction:
    ``(count + 1) / (n_resamples + 1)`` — the standard correction that
    guarantees ``p > 0`` even when the observed statistic is the
    extreme one, and makes p close to uniform under H0.

    ``seed`` makes the resample sequence (and hence p) deterministic.
    """
    _require_numpy()
    if alternative not in ("two-sided", "greater", "less"):
        raise ValueError(f"alternative must be 'two-sided', 'greater', 'less' (got {alternative!r})")
    rng = np.random.default_rng(seed)
    xa = np.asarray(a, dtype=float)
    xb = np.asarray(b, dtype=float)
    if len(xa) == 0 or len(xb) == 0:
        raise ValueError("both samples must be non-empty")
    if callable(stat):
        f: Union[str, Callable] = stat
    else:
        f = stat
    obs = _perm_stat(np.concatenate([xa, xb]), len(xa), f) if not callable(f) else float(f(xa, xb))

    pooled = np.concatenate([xa, xb])
    n = len(pooled)
    n_a = len(xa)
    count = 0
    def _t(perm: np.ndarray) -> float:
        if callable(f):
            return float(f(pooled[perm[:n_a]], pooled[perm[n_a:]]))
        return _perm_stat(pooled[perm], n_a, f)
    if alternative == "two-sided":
        # fraction of permutations at least as far from the null (0) as the
        # observed statistic: |t| >= |obs|
        for i in range(n_resamples):
            if abs(_t(rng.permutation(n))) >= abs(obs):
                count += 1
    elif alternative == "greater":
        for i in range(n_resamples):
            if _t(rng.permutation(n)) >= obs:
                count += 1
    else:  # less
        for i in range(n_resamples):
            if _t(rng.permutation(n)) <= obs:
                count += 1

    p = (count + 1) / (n_resamples + 1)
    return {
        "p": float(p),
        "statistic": float(obs),
        "alternative": alternative,
        "n_resamples": int(n_resamples),
        "n_a": int(n_a),
        "n_b": int(len(xb)),
    }


# ---------------------------------------------------------------------------
# Wilson CI
# ---------------------------------------------------------------------------

def wilson_ci(k: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    """Wilson (score) interval for a binomial proportion, 95% by default.

    Reuses/extends the implementation in ``_campaign_analysis.py``
    (identical formula, same defaults, clamped to [0,1]).
    """
    if n <= 0:
        raise ValueError(f"n must be positive (got {n})")
    if k < 0 or k > n:
        raise ValueError(f"k={k} out of [0, n={n}]")
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


# ---------------------------------------------------------------------------
# Multiplicity
# ---------------------------------------------------------------------------

def holm(pvals: Sequence[float]) -> List[float]:
    """Holm step-down FWER adjustment (Holm, 1979; RQ-d1 confirmatory).

    Returns adjusted p-values in the *original* order.  Valid under any
    dependence; strictly more powerful than Bonferroni.
    """
    n = len(pvals)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: pvals[i])
    adj = [0.0] * n
    running_max = 0.0
    for rank, i in enumerate(order):
        m = n - rank
        adj[i] = max(running_max, m * pvals[i])
        running_max = adj[i]
    return [min(1.0, x) for x in adj]


def benjamini_hochberg(pvals: Sequence[float]) -> List[float]:
    """Benjamini-Hochberg FDR adjustment (1995; RQ-d1 exploratory).

    Returns adjusted p-values in the *original* order, with the standard
    monotonicity correction (right-to-left cummax after the rank-based
    scaling).
    """
    n = len(pvals)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: pvals[i])
    adj = [0.0] * n
    for rank, i in enumerate(order):
        adj[i] = pvals[i] * n / (rank + 1)
    # Monotonicity: walk from the largest rank back down, keeping the
    # running minimum of the scaled values (BH's standard fix).
    running_min = 1.0
    for rank in range(n - 1, -1, -1):
        i = order[rank]
        running_min = min(running_min, adj[i])
        adj[i] = running_min
    return [min(1.0, x) for x in adj]


# ---------------------------------------------------------------------------
# Effect size
# ---------------------------------------------------------------------------

def cohens_d(a: Numbers, b: Numbers, paired: bool = False) -> float:
    """Cohen's d (RQ-e1 secondary effect size).

    Independent: pooled-SD d = (mean_a - mean_b) / SD_pooled.
    Paired: d = mean(difference) / SD(difference).
    """
    _require_numpy()
    if paired:
        if len(a) != len(b):
            raise ValueError("paired Cohen's d requires equal-length samples")
        d = np.asarray(a, dtype=float) - np.asarray(b, dtype=float)
        sd = d.std(ddof=1)
        return float(d.mean() / sd) if sd > 0 else 0.0
    xa = np.asarray(a, dtype=float)
    xb = np.asarray(b, dtype=float)
    na, nb = len(xa), len(xb)
    if na < 2 or nb < 2:
        raise ValueError("need n>=2 per arm for pooled-SD Cohen's d")
    va, vb = xa.var(ddof=1), xb.var(ddof=1)
    sp2 = ((na - 1) * va + (nb - 1) * vb) / (na + nb - 2)
    sp = math.sqrt(sp2)
    return float((xa.mean() - xb.mean()) / sp) if sp > 0 else 0.0


def cliffs_delta(a: Numbers, b: Numbers) -> float:
    """Cliff's delta (rank-biserial effect size for two independent samples).

    ``δ = (P(a > b) - P(b > a))`` over all cross pairs.  Design-independent
    magnitude comparable to ``d``; robust to heavy tails, which is why we
    report it alongside ``d`` for latency (RQ-e1 secondary).
    """
    _require_numpy()
    xa = np.asarray(a, dtype=float)
    xb = np.asarray(b, dtype=float)
    n = len(xa) * len(xb)
    if n == 0:
        return 0.0
    gt = lt = 0
    for x in xa:
        gt += int(np.sum(xb < x))
        lt += int(np.sum(xb > x))
    return float((gt - lt) / n)


def effect_size(a: Numbers, b: Numbers, paired: bool = False) -> Dict[str, float]:
    """Both effect sizes at once: ``{'cohens_d': d, 'cliffs_delta': δ}``.

    ``cliffs_delta`` is only meaningful for the independent case; for a
    paired design we return the paired ``d`` and ``None`` for δ.
    """
    d = cohens_d(a, b, paired=paired)
    cd = None if paired else cliffs_delta(a, b)
    return {"cohens_d": d, "cliffs_delta": cd}


# ---------------------------------------------------------------------------
# Power / MDE / N
# ---------------------------------------------------------------------------

# Coefficients from docs/campaign_methodology.md RQ-a2/RQ-a4, derived
# from alpha=0.05 (z=1.96) and power=0.80 (z=0.84):
#   two-sample (disjoint):  n = 2(z_{α/2}+z_β)² · CV²/rel_d²  = 15.68·CV²/rel_d²
#   paired (ρ=0.8):         n = (z_{α/2}+z_β)²·2(1−ρ)·CV²/rel_d² = 3.136·CV²/rel_d²
# (2.0·(1.96+0.84)² = 15.698; (1.96+0.84)²·2·0.2 = 3.140 — the doc's
# 15.68/3.136 use z rounded to 1.96/0.84 and are what we use here,
# matching the doc's table to <0.1%.)
_COEF_TWO_SAMPLE = 15.68
_COEF_PAIRING_RHO08 = 3.136

# Base factors (before the (z_{α/2}+z_β)² term): 2 for two independent
# arms, 2(1−ρ) for a paired design.  The doc's coefficients are these
# base factors times (1.96+0.84)² = 7.84:  2·7.84 = 15.68,  0.4·7.84 = 3.136.
_BASE_TWO_SAMPLE = 2.0
def _base_factor(design: str, rho: float) -> float:
    if design == "two-sample":
        return _BASE_TWO_SAMPLE
    if design == "paired":
        return 2.0 * (1.0 - rho)
    raise ValueError(f"design must be 'two-sample' or 'paired' (got {design!r})")


def sample_size(
    rel_d: float,
    cv: float,
    design: str = "two-sample",
    alpha: float = 0.05,
    power: float = 0.80,
    rho: float = 0.8,
) -> float:
    """Per-arm sample size N from the doc's power arithmetic.

    ``design='two-sample'`` → ``n = 15.68·CV²/rel_d²`` (RQ-a2/a3);
    ``design='paired'`` → ``n = 3.136·CV²/rel_d²`` at ρ=0.8 (RQ-a4).
    The α/power knobs are accepted for API symmetry but the
    coefficients are fixed to the doc's values (15.68 / 3.136 at
    α=0.05, power=0.80) so the result matches the methodology table
    exactly; passing other α/power is a no-op (documented, not a bug
    — the doc's formulas are the spec of record).
    """
    if rel_d <= 0:
        raise ValueError(f"rel_d must be positive (got {rel_d})")
    if cv < 0:
        raise ValueError(f"cv must be non-negative (got {cv})")
    coef = _COEF_TWO_SAMPLE if design == "two-sample" else _COEF_PAIRING_RHO08
    if design not in ("two-sample", "paired"):
        raise ValueError(f"design must be 'two-sample' or 'paired' (got {design!r})")
    return float(coef * cv * cv / (rel_d * rel_d))


def mde(
    n: float,
    cv: float,
    design: str = "two-sample",
    alpha: float = 0.05,
    power: float = 0.80,
    rho: float = 0.8,
) -> float:
    """Inverse of :func:`sample_size`: the minimum detectable relative delta.

    ``mde = sqrt(coef · CV² / n)``.
    """
    if n <= 0:
        raise ValueError(f"n must be positive (got {n})")
    if cv < 0:
        raise ValueError(f"cv must be non-negative (got {cv})")
    coef = _COEF_TWO_SAMPLE if design == "two-sample" else _COEF_PAIRING_RHO08
    if design not in ("two-sample", "paired"):
        raise ValueError(f"design must be 'two-sample' or 'paired' (got {design!r})")
    return float(math.sqrt(coef * cv * cv / n))


def achieved_power(
    n: float,
    rel_d: float,
    cv: float,
    design: str = "two-sample",
    alpha: float = 0.05,
    rho: float = 0.8,
) -> float:
    """Achieved power at a given N — the exact inverse of :func:`sample_size`.

    Starting from the doc's power arithmetic,
    ``n = c · (z_{α/2}+z_β)² · CV²/rel_d²`` where ``c = 2`` (two-sample)
    or ``c = 2(1−ρ)`` (paired), solve for ``z_β``::

        (z_{α/2} + z_β)² = n · rel_d² / (c · CV²)
        ⇒  z_β = sqrt(n · rel_d² / (c · CV²)) − z_{α/2}
        ⇒  power = Φ(z_β)

    with ``z_{α/2} = 1.96`` (the doc's value).  Because the sample-size
    coefficient is ``c · (1.96 + 0.84)²``, ``achieved_power`` returns
    exactly 0.80 (Φ(0.84)) when ``n = sample_size(rel_d, cv)`` and
    ``rel_d`` is the true effect.
    """
    if n <= 0:
        raise ValueError(f"n must be positive (got {n})")
    if rel_d <= 0:
        raise ValueError(f"rel_d must be positive (got {rel_d})")
    if cv < 0:
        raise ValueError(f"cv must be non-negative (got {cv})")
    if cv == 0:
        return 1.0
    c = _base_factor(design, rho)
    z_alpha = 1.96  # doc's z_{α/2}
    inner = n * rel_d * rel_d / (c * cv * cv)
    if inner <= 0:
        return 0.0
    z_beta = math.sqrt(inner) - z_alpha
    return float(_cdf(z_beta))


# ---------------------------------------------------------------------------
# CLI / sanity gate
# ---------------------------------------------------------------------------

def _load_campaign(path: str) -> List[Dict[str, Any]]:
    recs = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                recs.append(json.loads(line))
    return recs


def _ttft_by(rtt: int, variant: str, recs: List[Dict[str, Any]], cold: bool) -> List[float]:
    out = []
    for r in recs:
        if r["rtt_ms"] != rtt or r["variant"] != variant:
            continue
        if cold and r["repeat_index"] != 0:
            continue
        if not cold and r["repeat_index"] < 1:
            continue
        out.append(r["ttft_ms"])
    return out


def _stat_block_bca(vals: List[float], seed: int, B: int) -> Dict[str, Any]:
    x = np.asarray(vals, dtype=float)
    return {
        "n": int(len(x)),
        "mean": float(round(float(np.mean(x)), 2)),
        "median": float(round(float(np.median(x)), 2)),
        "p50": float(round(float(np.median(x)), 2)),
        "p95": float(round(percentile(x, 95), 2)),
        "p99": float(round(percentile(x, 99), 2)),
        "ci95_mean_bca": [round(v, 2) for v in bootstrap_ci(x, stat="mean", method="bca", B=B, seed=seed)],
        "ci95_p50_bca": [round(v, 2) for v in bootstrap_ci(x, stat="p50", method="bca", B=B, seed=seed)],
        "ci95_p95_bca": [round(v, 2) for v in bootstrap_ci(x, stat="p95", method="bca", B=B, seed=seed)],
        "ci95_p99_bca": [round(v, 2) for v in bootstrap_ci(x, stat="p99", method="bca", B=B, seed=seed)],
    }


def run_sanity(
    campaign_path: str,
    out_path: str,
    seed: int = 42,
    B: int = 1000,
    n_perm: int = 10000,
) -> Dict[str, Any]:
    """Sanity gate: reproduce published point estimates, then add the
    new inferential layer.  Returns the full result dict (also written
    to ``out_path``).

    Sanity assertions (raise on failure):
      * The rtt=0 point estimates (cold/warm, baseline/SPHP) match the
        published values in ``campaign_analysis.json`` to 2 decimal
        places (tolerance 0.01 ms).
      * The published delta percentages are reproduced.
    """
    recs = _load_campaign(campaign_path)
    if len(recs) != 1200:
        raise ValueError(f"expected 1200 campaign records, got {len(recs)}")

    # ---- Load the published point estimates for comparison ----
    pub_path = out_path.rsplit("/", 1)[0] + "/campaign_analysis.json"
    try:
        pub = json.load(open(pub_path, encoding="utf-8"))["deltas"]
    except (OSError, json.JSONDecodeError):
        pub = None

    # ---- rtt=0 headline cell (the dispatch's numbers) ----
    b_cold = _ttft_by(0, "baseline", recs, cold=True)
    s_cold = _ttft_by(0, "sphp", recs, cold=True)
    b_warm = _ttft_by(0, "baseline", recs, cold=False)
    s_warm = _ttft_by(0, "sphp", recs, cold=False)

    b_cold_mean = float(np.mean(b_cold))
    s_cold_mean = float(np.mean(s_cold))
    b_warm_mean = float(np.mean(b_warm))
    s_warm_mean = float(np.mean(s_warm))

    cold_delta_pct = 100.0 * (s_cold_mean - b_cold_mean) / b_cold_mean
    warm_delta_pct = 100.0 * (s_warm_mean - b_warm_mean) / b_warm_mean

    # Pooled (all RTT) cold/warm deltas — the source of the ≈17.4% headline
    all_b_cold = [r["ttft_ms"] for r in recs if r["variant"] == "baseline" and r["repeat_index"] == 0]
    all_s_cold = [r["ttft_ms"] for r in recs if r["variant"] == "sphp" and r["repeat_index"] == 0]
    all_b_warm = [r["ttft_ms"] for r in recs if r["variant"] == "baseline" and r["repeat_index"] >= 1]
    all_s_warm = [r["ttft_ms"] for r in recs if r["variant"] == "sphp" and r["repeat_index"] >= 1]
    pooled_cold_pct = 100.0 * (float(np.mean(all_s_cold)) - float(np.mean(all_b_cold))) / float(np.mean(all_b_cold))
    pooled_warm_pct = 100.0 * (float(np.mean(all_s_warm)) - float(np.mean(all_b_warm))) / float(np.mean(all_b_warm))

    # ---- Sanity assertions ----
    tol = 0.01
    p0c = pub["rtt0"]["ttft_cold"] if pub else None
    p0w = pub["rtt0"]["ttft_warm"] if pub else None
    if pub is not None:
        assert abs(b_cold_mean - p0c["baseline_mean"]) < tol, \
            f"cold baseline mean {b_cold_mean:.2f} vs published {p0c['baseline_mean']}"
        assert abs(s_cold_mean - p0c["sphp_mean"]) < tol, \
            f"cold sphp mean {s_cold_mean:.2f} vs published {p0c['sphp_mean']}"
        assert abs(b_warm_mean - p0w["baseline_mean"]) < tol, \
            f"warm baseline mean {b_warm_mean:.2f} vs published {p0w['baseline_mean']}"
        assert abs(s_warm_mean - p0w["sphp_mean"]) < tol, \
            f"warm sphp mean {s_warm_mean:.2f} vs published {p0w['sphp_mean']}"
        assert abs(100.0 * (p0c["sphp_mean"] - p0c["baseline_mean"]) / p0c["baseline_mean"] - cold_delta_pct) < 0.01, \
            f"cold delta pct mismatch"
        assert abs(100.0 * (p0w["sphp_mean"] - p0w["baseline_mean"]) / p0w["baseline_mean"] - warm_delta_pct) < 0.01, \
            f"warm delta pct mismatch"
    else:
        # No published file available — fall back to the dispatch's numbers
        assert abs(b_cold_mean - 3634) < 2.0, f"cold baseline {b_cold_mean:.2f} vs ~3634"
        assert abs(s_cold_mean - 2948) < 2.0, f"cold sphp {s_cold_mean:.2f} vs ~2948"
        assert abs(b_warm_mean - 818) < 2.0, f"warm baseline {b_warm_mean:.2f} vs ~818"
        assert abs(s_warm_mean - 739) < 2.0, f"warm sphp {s_warm_mean:.2f} vs ~739"

    # ---- Inferential layer: BCa CIs on the rtt=0 headline numbers ----
    ci_blocks = {
        "baseline_cold_ttft": _stat_block_bca(b_cold, seed, B),
        "sphp_cold_ttft": _stat_block_bca(s_cold, seed, B),
        "baseline_warm_ttft": _stat_block_bca(b_warm, seed, B),
        "sphp_warm_ttft": _stat_block_bca(s_warm, seed, B),
    }

    # ---- Permutation tests: baseline vs sphp per cache state, rtt=0 ----
    perm_cold_mean = permutation_test(s_cold, b_cold, stat="mean", alternative="less",
                                      n_resamples=n_perm, seed=seed)
    perm_cold_med = permutation_test(s_cold, b_cold, stat="median", alternative="less",
                                     n_resamples=n_perm, seed=seed)
    perm_warm_mean = permutation_test(s_warm, b_warm, stat="mean", alternative="less",
                                      n_resamples=n_perm, seed=seed)
    perm_warm_med = permutation_test(s_warm, b_warm, stat="median", alternative="less",
                                     n_resamples=n_perm, seed=seed)

    # Two-sided versions (for the multiplicity family)
    perm_cold_mean_2s = permutation_test(s_cold, b_cold, stat="mean", alternative="two-sided",
                                         n_resamples=n_perm, seed=seed)
    perm_warm_mean_2s = permutation_test(s_warm, b_warm, stat="mean", alternative="two-sided",
                                         n_resamples=n_perm, seed=seed)

    # Delta CIs
    delta_cold = bootstrap_delta_ci(s_cold, b_cold, stat="mean", method="bca",
                                   paired=False, B=B, seed=seed)
    delta_warm = bootstrap_delta_ci(s_warm, b_warm, stat="mean", method="bca",
                                    paired=False, B=B, seed=seed)

    # Effect sizes
    eff_cold = effect_size(s_cold, b_cold, paired=False)
    eff_warm = effect_size(s_warm, b_warm, paired=False)

    # ---- Multiplicity: confirmatory family (Holm) ----
    # Confirmatory family: the 4 rtt tiers × 2 strata on ttft mean,
    # two-sided permutation p-values (RQ-d1: Holm/FWER for the small
    # pre-specified confirmatory family).
    confirm_pvals = []
    confirm_labels = []
    for rtt in (0, 15, 40, 80):
        for cold, strat in ((True, "cold"), (False, "warm")):
            bc = _ttft_by(rtt, "baseline", recs, cold=cold)
            sc = _ttft_by(rtt, "sphp", recs, cold=cold)
            pt = permutation_test(sc, bc, stat="mean", alternative="two-sided",
                                  n_resamples=n_perm, seed=seed)
            confirm_pvals.append(pt["p"])
            confirm_labels.append(f"rtt{rtt}_{strat}_ttft_mean")
    holm_adj = holm(confirm_pvals)

    # Exploratory sweep (BH): same family but BH-adjusted (RQ-d1:
    # BH/FDR for the exploratory regime-by-regime sweep).
    bh_adj = benjamini_hochberg(confirm_pvals)

    # ---- Power / MDE for the rtt=0 cold cell ----
    # CV of the cold TTFT for baseline (the control arm's CV is the
    # relevant one for the MDE of a treatment effect against it).
    b_cold_cv = float(np.std(b_cold, ddof=1) / np.mean(b_cold))
    n_cold = len(b_cold)
    # The rtt=0 cold cell: how much power does the existing N=50/arm give
    # at the 10% confirmatory MDE, and what MDE is it good for?
    # Two-sample (the actual design) and paired (RQ-a4: what N=50 would
    # give if the same queries were run in both arms at the same cache
    # state — the contested trade-off from RQ-b1).
    mde_cold = {
        "n_per_arm": n_cold,
        "cv_baseline_cold": round(b_cold_cv, 4),
        "two_sample": {
            "mde": round(mde(n_cold, b_cold_cv, "two-sample"), 4),
            "achieved_power_at_10pct": round(achieved_power(n_cold, 0.10, b_cold_cv, "two-sample"), 4),
            "achieved_power_at_5pct": round(achieved_power(n_cold, 0.05, b_cold_cv, "two-sample"), 4),
            "required_n_at_10pct": round(sample_size(0.10, b_cold_cv, "two-sample"), 1),
            "required_n_at_5pct": round(sample_size(0.05, b_cold_cv, "two-sample"), 1),
        },
        "paired_rho_0p8": {
            "mde": round(mde(n_cold, b_cold_cv, "paired"), 4),
            "achieved_power_at_10pct": round(achieved_power(n_cold, 0.10, b_cold_cv, "paired"), 4),
            "achieved_power_at_5pct": round(achieved_power(n_cold, 0.05, b_cold_cv, "paired"), 4),
            "required_n_at_10pct": round(sample_size(0.10, b_cold_cv, "paired"), 1),
            "required_n_at_5pct": round(sample_size(0.05, b_cold_cv, "paired"), 1),
        },
    }

    result: Dict[str, Any] = {
        "source": campaign_path,
        "n_records": len(recs),
        "seed": seed,
        "B_bootstrap": B,
        "n_permutation": n_perm,
        "method": {
            "percentile": "linear interpolation (numpy method='linear', type-7)",
            "ci": "BCa bootstrap (B=1000, seed=42) per LR-7; percentile fallback available",
            "delta_ci": "independent-sample BCa bootstrap of (sphp_mean − baseline_mean)",
            "test": "permutation test (n=10000 resamples, +1 continuity correction), stat=mean and stat=median",
            "multiplicity_confirmatory": "Holm (FWER) on the 8-cell rtt×stratum family",
            "multiplicity_exploratory": "Benjamini-Hochberg (FDR) on the same family",
            "power": "n = 15.68·CV²/rel_d² (two-sample) / 3.136·CV²/rel_d² (paired ρ=0.8); "
                     "achieved_power inverts the formula; MDE = sqrt(coef·CV²/n)",
            "strata": "cold = repeat_index==0, warm = repeat_index>=1 (RQ-b2: strata not replicates)",
        },
        "point_estimates_rtt0": {
            "baseline_cold_ttft_mean_ms": round(b_cold_mean, 2),
            "sphp_cold_ttft_mean_ms": round(s_cold_mean, 2),
            "baseline_warm_ttft_mean_ms": round(b_warm_mean, 2),
            "sphp_warm_ttft_mean_ms": round(s_warm_mean, 2),
            "cold_delta_pct": round(cold_delta_pct, 2),
            "warm_delta_pct": round(warm_delta_pct, 2),
            "pooled_cold_delta_pct": round(pooled_cold_pct, 2),
            "pooled_warm_delta_pct": round(pooled_warm_pct, 2),
            "published_comparison": {
                "match": pub is not None,
                "tolerance_ms": tol,
                "published_rtt0": {
                    "ttft_cold": {
                        "baseline_mean": p0c["baseline_mean"] if pub else None,
                        "sphp_mean": p0c["sphp_mean"] if pub else None,
                        "delta_pct": round(100.0 * (p0c["sphp_mean"] - p0c["baseline_mean"]) / p0c["baseline_mean"], 2) if pub else None,
                    },
                    "ttft_warm": {
                        "baseline_mean": p0w["baseline_mean"] if pub else None,
                        "sphp_mean": p0w["sphp_mean"] if pub else None,
                        "delta_pct": round(100.0 * (p0w["sphp_mean"] - p0w["baseline_mean"]) / p0w["baseline_mean"], 2) if pub else None,
                    },
                },
            },
        },
        "bca_cis_rtt0": ci_blocks,
        "permutation_tests_rtt0": {
            "cold_mean_less": perm_cold_mean,
            "cold_median_less": perm_cold_med,
            "warm_mean_less": perm_warm_mean,
            "warm_median_less": perm_warm_med,
            "cold_mean_two_sided": perm_cold_mean_2s,
            "warm_mean_two_sided": perm_warm_mean_2s,
        },
        "delta_cis_rtt0": {
            "cold": delta_cold,
            "warm": delta_warm,
        },
        "effect_sizes_rtt0": {
            "cold": eff_cold,
            "warm": eff_warm,
        },
        "multiplicity": {
            "labels": confirm_labels,
            "raw_pvals": [round(p, 6) for p in confirm_pvals],
            "holm_adjusted": [round(p, 6) for p in holm_adj],
            "bh_adjusted": [round(p, 6) for p in bh_adj],
            "alpha_confirmatory": 0.05,
            "n_significant_holm": sum(1 for p in holm_adj if p < 0.05),
            "n_significant_bh": sum(1 for p in bh_adj if p < 0.05),
        },
        "power_rtt0_cold": mde_cold,
    }

    out_dir = out_path.rsplit("/", 1)[0] if "/" in out_path else "."
    if out_dir:
        import os
        os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(f"wrote {out_path}")
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="Campaign statistics engine — sanity gate")
    ap.add_argument("--campaign", default="experiments/bench/campaigns/campaign_20260918T130756Z.jsonl")
    ap.add_argument("--out", default="experiments/analysis/results/stats_sanity_20260918campaign.json")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--b", type=int, default=1000)
    ap.add_argument("--perm", type=int, default=10000)
    args = ap.parse_args()
    run_sanity(args.campaign, args.out, seed=args.seed, B=args.b, n_perm=args.perm)
    return 0


if __name__ == "__main__":
    sys.exit(main())
