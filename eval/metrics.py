"""Pure, dependency-free IR quality metrics for the Node B gateway.

These functions score per-mode ranked doc-id lists (as produced by
``benchmarks/run_eval.py``) against TREC-format qrels. They are pure: no I/O,
no globals, no side effects, so they are trivially unit-testable and safe to
call from any process.

Conventions
-----------
* ``qrels`` is ``dict[qid -> set[pid]]`` (relevance judgments; a pid in the
  set is relevant).
* ``runs`` is a list of records, each a ``dict`` carrying a ``"qid"`` and one
  or more ranked doc-id arrays under keys of the form ``"<mode>_doc_ids"``
  (e.g. ``"sparse_doc_ids"``, ``"dense_doc_ids"``, ``"fused_doc_ids"``).
* Every metric is averaged over the set of qids that are present in BOTH the
  runs and the qrels (the "evaluable" set). A qid present in the runs but
  absent from the qrels is skipped (no relevance judgments to score against);
  a qid in the qrels but absent from the runs is likewise skipped (no ranking
  to score). This keeps the denominator well-defined and independent of how
  many qrels a query has.
* A ranking that is empty, or that contains no relevant doc within the cutoff,
  contributes 0 to MRR / nDCG / Recall for that query.
"""

from __future__ import annotations

import math

__all__ = [
    "load_qrels",
    "mrr",
    "mrr_at_10",
    "ndcg_at_10",
    "recall_at_k",
]


# ---------------------------------------------------------------------------
# Qrels loading
# ---------------------------------------------------------------------------

def load_qrels(path: str) -> dict[str, set[str]]:
    """Load a TREC qrels file into ``{qid: set(pid)}``.

    TREC qrels layout (tab-separated):
        col0 = query-id
        col1 = run-tag / iteration
        col2 = passage-id
        col3 = relevance-grade   (optional)

    Only the qid (col0) and pid (col2) are needed. Lines with fewer than
    three columns, or with a blank qid/pid, are ignored. A pid is considered
    relevant if it appears on any line for the qid (grades are not
    interpreted; presence == relevant).
    """
    qrels: dict[str, set[str]] = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            qid = parts[0].strip()
            pid = parts[2].strip()
            if not qid or not pid:
                continue
            qrels.setdefault(qid, set()).add(pid)
    return qrels


# ---------------------------------------------------------------------------
# Ranking access
# ---------------------------------------------------------------------------

def _ranking_for(runs: list[dict], qid: str, key: str) -> list[str]:
    """Return the ranked doc-id list for ``qid`` under ``key`` (or ``[]``).

    The first record in ``runs`` whose ``"qid"`` matches is used. If the
    record lacks ``key`` (or it is not a list) an empty list is returned,
    which the metrics treat as "no relevant doc retrieved".
    """
    for rec in runs:
        if rec.get("qid") == qid:
            val = rec.get(key)
            if isinstance(val, list):
                return [str(x) for x in val]
            return []
    return []


def _evaluable_qids(runs: list[dict], qrels: dict[str, set[str]]) -> list[str]:
    """Qids present in both the runs and the qrels, in first-seen run order."""
    seen: list[str] = []
    seen_set: set[str] = set()
    for rec in runs:
        qid = rec.get("qid")
        if qid is None:
            continue
        qid = str(qid)
        if qid in qrels and qid not in seen_set:
            seen_set.add(qid)
            seen.append(qid)
    return seen


# ---------------------------------------------------------------------------
# MRR
# ---------------------------------------------------------------------------

def mrr(runs: list[dict], qrels: dict[str, set[str]],
        cutoff: int | None = None, key: str = "fused_doc_ids") -> float:
    """Mean Reciprocal Rank over the evaluable qids.

    For each qid, the reciprocal rank is ``1 / rank`` where ``rank`` is the
    1-based position of the FIRST relevant doc in the ``key`` ranking; it is
    ``0`` if no relevant doc appears (or the ranking is empty). When
    ``cutoff`` is given, only the first ``cutoff`` positions are considered
    (a relevant doc ranked beyond the cutoff counts as a miss). ``cutoff=None``
    means the full pool (the official MS MARCO setting uses a top-1000 pool).

    Returns the mean over the evaluable qids, or ``0.0`` if there are none.
    """
    qids = _evaluable_qids(runs, qrels)
    if not qids:
        return 0.0
    total = 0.0
    for qid in qids:
        relevant = qrels[qid]
        ranking = _ranking_for(runs, qid, key)
        if cutoff is not None:
            ranking = ranking[:cutoff]
        rr = 0.0
        for rank, doc in enumerate(ranking, start=1):
            if doc in relevant:
                rr = 1.0 / rank
                break
        total += rr
    return total / len(qids)


def mrr_at_10(runs: list[dict], qrels: dict[str, set[str]],
              key: str = "fused_doc_ids") -> float:
    """MRR restricted to the top-10 positions."""
    return mrr(runs, qrels, key=key, cutoff=10)


# ---------------------------------------------------------------------------
# nDCG
# ---------------------------------------------------------------------------

def ndcg_at_10(runs: list[dict], qrels: dict[str, set[str]],
               key: str = "fused_doc_ids") -> float:
    """Normalized Discounted Cumulative Gain at rank 10.

    Binary relevance (gain 1 for a relevant doc, 0 otherwise) with the
    standard logarithmic discount ``DCG = sum_{i} gain_i / log2(i + 1)``.
    The ideal DCG places all relevant docs at the top ranks. A query with no
    relevant docs (or an empty ranking) contributes 0. Returns the mean over
    the evaluable qids, or ``0.0`` if there are none.
    """
    qids = _evaluable_qids(runs, qrels)
    if not qids:
        return 0.0
    total = 0.0
    for qid in qids:
        relevant = qrels[qid]
        n_rel = len(relevant)
        if n_rel == 0:
            continue
        ranking = _ranking_for(runs, qid, key)[:10]
        dcg = 0.0
        for i, doc in enumerate(ranking, start=1):
            if doc in relevant:
                dcg += 1.0 / math.log2(i + 1)
        # Ideal: all n_rel relevant docs occupy ranks 1..n_rel.
        idcg = 0.0
        for i in range(1, n_rel + 1):
            idcg += 1.0 / math.log2(i + 1)
        total += (dcg / idcg) if idcg > 0 else 0.0
    return total / len(qids)


# ---------------------------------------------------------------------------
# Recall
# ---------------------------------------------------------------------------

def recall_at_k(runs: list[dict], qrels: dict[str, set[str]],
                k: int = 100, key: str = "fused_doc_ids") -> float:
    """Recall at rank ``k``: fraction of relevant docs retrieved in the top-k.

    For each qid, ``recall = |relevant ∩ top_k| / |relevant|`` (0 if the query
    has no relevant docs). Returns the mean over the evaluable qids, or
    ``0.0`` if there are none.
    """
    qids = _evaluable_qids(runs, qrels)
    if not qids:
        return 0.0
    total = 0.0
    for qid in qids:
        relevant = qrels[qid]
        if not relevant:
            continue
        topk = set(_ranking_for(runs, qid, key)[:k])
        total += len(relevant & topk) / len(relevant)
    return total / len(qids)
