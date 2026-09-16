"""Offline quality-metrics package for the Node B hybrid retrieval gateway.

Provides pure, dependency-free IR metrics (MRR, nDCG, Recall) over the
per-mode ranked doc-id arrays produced by ``benchmarks/run_eval.py``,
scored against TREC-format qrels.

Public API:
    load_qrels(path) -> dict[str, set[str]]
    mrr(runs, qrels, cutoff=None) -> float
    mrr_at_10(runs, qrels) -> float
    ndcg_at_10(runs, qrels) -> float
    recall_at_k(runs, qrels, k) -> float
"""

from .metrics import (
    load_qrels,
    mrr,
    mrr_at_10,
    ndcg_at_10,
    recall_at_k,
)

__all__ = [
    "load_qrels",
    "mrr",
    "mrr_at_10",
    "ndcg_at_10",
    "recall_at_k",
]
