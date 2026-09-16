"""Pytest suite for the offline quality-metrics module.

Uses synthetic fixtures with hand-computed expected values, covering:
  * MRR (basic, cutoff, missing qid, empty ranking, no-relevant, multi-relevant)
  * nDCG@10 (perfect, single-relevant at various ranks, interleaved, empty)
  * Recall@k (full, partial, k-limiting, empty, duplicate docs, missing qid)
  * load_qrels (TREC col0=qid / col2=pid, ignores run-tag & grade, skips short lines)

Expected values are derived by hand from the metric definitions; float
comparisons use ``pytest.approx``.
"""

import math
import os
import tempfile

import pytest

from eval.metrics import (
    load_qrels,
    mrr,
    mrr_at_10,
    ndcg_at_10,
    recall_at_k,
)


# ---------------------------------------------------------------------------
# load_qrels
# ---------------------------------------------------------------------------

def test_load_qrels_basic(tmp_path):
    p = tmp_path / "qrels.tsv"
    p.write_text(
        "q1\tA\td1\t1\n"
        "q1\tA\td2\t1\n"
        "q2\tA\td3\t1\n",
        encoding="utf-8",
    )
    q = load_qrels(str(p))
    assert q == {"q1": {"d1", "d2"}, "q2": {"d3"}}


def test_load_qrels_ignores_run_tag_and_grade(tmp_path):
    # col1 (run tag) and col3 (grade) must be ignored; only col0 & col2 matter.
    p = tmp_path / "qrels.tsv"
    p.write_text(
        "q1\tRUN1\td1\t2\n"
        "q1\tRUN2\td1\t0\n"   # same pid, different tag/grade -> still one pid
        "q1\tRUN1\td2\t1\n",
        encoding="utf-8",
    )
    q = load_qrels(str(p))
    assert q == {"q1": {"d1", "d2"}}


def test_load_qrels_skips_short_and_blank_lines(tmp_path):
    p = tmp_path / "qrels.tsv"
    p.write_text(
        "q1\tonly_one_col\n"      # <3 cols -> skipped
        "\n"                       # blank -> skipped
        "q2\tA\td9\t1\n",         # valid
        encoding="utf-8",
    )
    q = load_qrels(str(p))
    assert q == {"q2": {"d9"}}


# ---------------------------------------------------------------------------
# MRR
# ---------------------------------------------------------------------------

def test_mrr_basic():
    qrels = {"q1": {"d1", "d2"}, "q2": {"d3"}}
    runs = [
        {"qid": "q1", "fused_doc_ids": ["d5", "d1", "d2"]},  # first rel d1 @2 -> 1/2
        {"qid": "q2", "fused_doc_ids": ["d3"]},              # d3 @1 -> 1
    ]
    assert mrr(runs, qrels) == pytest.approx(0.75)


def test_mrr_full_pool_vs_cutoff():
    # d1 is the only relevant doc, ranked 11th.
    qrels = {"q1": {"d1"}}
    runs = [{"qid": "q1", "fused_doc_ids":
             ["d2", "d3", "d4", "d5", "d6", "d7", "d8", "d9", "d10", "d11", "d1"]}]
    # Full pool (cutoff=None): 1/11.
    assert mrr(runs, qrels, cutoff=None) == pytest.approx(1.0 / 11)
    # Top-10: d1 is at rank 11 -> out of pool -> 0.
    assert mrr_at_10(runs, qrels) == pytest.approx(0.0)


def test_mrr_missing_qid_in_qrels_is_skipped():
    # q3 is in the runs but has no qrels -> skipped; only q1 is evaluable.
    qrels = {"q1": {"d1"}}
    runs = [
        {"qid": "q1", "fused_doc_ids": ["d1"]},   # 1
        {"qid": "q3", "fused_doc_ids": ["d1"]},   # skipped (no qrels)
    ]
    assert mrr(runs, qrels) == pytest.approx(1.0)


def test_mrr_empty_ranking_contributes_zero():
    qrels = {"q1": {"d1"}, "q2": {"d2"}}
    runs = [
        {"qid": "q1", "fused_doc_ids": []},        # 0
        {"qid": "q2", "fused_doc_ids": ["d2"]},    # 1
    ]
    assert mrr(runs, qrels) == pytest.approx(0.5)


def test_mrr_no_relevant_in_ranking():
    qrels = {"q1": {"d1"}}
    runs = [{"qid": "q1", "fused_doc_ids": ["d2", "d3"]}]
    assert mrr(runs, qrels) == pytest.approx(0.0)


def test_mrr_multi_relevant_uses_first():
    # Two relevant docs; MRR uses the FIRST one (d2 @ rank 2).
    qrels = {"q1": {"d1", "d2"}}
    runs = [{"qid": "q1", "fused_doc_ids": ["d3", "d2", "d1"]}]
    assert mrr(runs, qrels) == pytest.approx(0.5)


def test_mrr_no_evaluable_qids():
    # Runs and qrels share no qid -> 0.0 (no division by zero).
    qrels = {"q1": {"d1"}}
    runs = [{"qid": "q9", "fused_doc_ids": ["d1"]}]
    assert mrr(runs, qrels) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# nDCG@10
# ---------------------------------------------------------------------------

def test_ndcg_perfect_single():
    qrels = {"q1": {"d1"}}
    runs = [{"qid": "q1", "fused_doc_ids": ["d1"]}]
    assert ndcg_at_10(runs, qrels) == pytest.approx(1.0)


def test_ndcg_single_relevant_at_rank2():
    qrels = {"q1": {"d1"}}
    runs = [{"qid": "q1", "fused_doc_ids": ["d2", "d1"]}]
    # DCG = 1/log2(3); IDCG = 1/log2(2) = 1.
    assert ndcg_at_10(runs, qrels) == pytest.approx(1.0 / math.log2(3))


def test_ndcg_single_relevant_at_rank3():
    qrels = {"q1": {"d1"}}
    runs = [{"qid": "q1", "fused_doc_ids": ["d2", "d3", "d1"]}]
    # DCG = 1/log2(4) = 0.5; IDCG = 1.
    assert ndcg_at_10(runs, qrels) == pytest.approx(0.5)


def test_ndcg_perfect_multi_relevant():
    qrels = {"q1": {"d1", "d2"}}
    runs = [{"qid": "q1", "fused_doc_ids": ["d1", "d2", "d3"]}]
    assert ndcg_at_10(runs, qrels) == pytest.approx(1.0)


def test_ndcg_interleaved_relevant():
    # Relevant d1 @ rank1, d2 @ rank3 (d3 non-relevant @ rank2).
    qrels = {"q1": {"d1", "d2"}}
    runs = [{"qid": "q1", "fused_doc_ids": ["d1", "d3", "d2"]}]
    # DCG = 1/log2(2) + 1/log2(4); IDCG = 1/log2(2) + 1/log2(3).
    expected = (1.0 / math.log2(2) + 1.0 / math.log2(4)) / \
               (1.0 / math.log2(2) + 1.0 / math.log2(3))
    assert ndcg_at_10(runs, qrels) == pytest.approx(expected)


def test_ndcg_empty_ranking():
    qrels = {"q1": {"d1"}}
    runs = [{"qid": "q1", "fused_doc_ids": []}]
    assert ndcg_at_10(runs, qrels) == pytest.approx(0.0)


def test_ndcg_no_relevant_in_ranking():
    qrels = {"q1": {"d1"}}
    runs = [{"qid": "q1", "fused_doc_ids": ["d2", "d3"]}]
    assert ndcg_at_10(runs, qrels) == pytest.approx(0.0)


def test_ndcg_more_relevant_than_cutoff():
    # 11 relevant docs, all ranked in order; only top-10 count for DCG,
    # but IDCG uses all 11 -> nDCG < 1.
    qrels = {"q1": {f"d{i}" for i in range(1, 12)}}
    runs = [{"qid": "q1", "fused_doc_ids": [f"d{i}" for i in range(1, 12)]}]
    dcg = sum(1.0 / math.log2(i + 1) for i in range(1, 11))   # top 10
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, 12))  # all 11
    assert ndcg_at_10(runs, qrels) == pytest.approx(dcg / idcg)
    assert ndcg_at_10(runs, qrels) < 1.0


# ---------------------------------------------------------------------------
# Recall@k
# ---------------------------------------------------------------------------

def test_recall_full():
    qrels = {"q1": {"d1", "d2", "d3"}}
    runs = [{"qid": "q1", "fused_doc_ids": ["d1", "d2", "d3", "d4"]}]
    assert recall_at_k(runs, qrels, k=100) == pytest.approx(1.0)


def test_recall_partial():
    qrels = {"q1": {"d1", "d2", "d3", "d4"}}
    runs = [{"qid": "q1", "fused_doc_ids": ["d1", "d2", "d5", "d6"]}]
    # 2 of 4 relevant in top-100.
    assert recall_at_k(runs, qrels, k=100) == pytest.approx(0.5)


def test_recall_k_limits_window():
    qrels = {"q1": {"d1", "d2", "d3"}}
    runs = [{"qid": "q1", "fused_doc_ids": ["d1", "d2", "d3"]}]
    # k=2 -> only d1,d2 in window -> 2/3.
    assert recall_at_k(runs, qrels, k=2) == pytest.approx(2.0 / 3.0)


def test_recall_empty_ranking():
    qrels = {"q1": {"d1"}}
    runs = [{"qid": "q1", "fused_doc_ids": []}]
    assert recall_at_k(runs, qrels, k=100) == pytest.approx(0.0)


def test_recall_duplicate_docs_do_not_inflate():
    # d1 appears twice; set-based recall must not double count.
    qrels = {"q1": {"d1", "d2"}}
    runs = [{"qid": "q1", "fused_doc_ids": ["d1", "d1", "d2"]}]
    assert recall_at_k(runs, qrels, k=100) == pytest.approx(1.0)


def test_recall_missing_qid_in_runs_is_skipped():
    # q2 is in qrels but not in the runs -> skipped; only q1 evaluable.
    qrels = {"q1": {"d1"}, "q2": {"d2"}}
    runs = [{"qid": "q1", "fused_doc_ids": ["d1"]}]
    assert recall_at_k(runs, qrels, k=100) == pytest.approx(1.0)


def test_recall_no_evaluable_qids():
    qrels = {"q1": {"d1"}}
    runs = [{"qid": "q9", "fused_doc_ids": ["d1"]}]
    assert recall_at_k(runs, qrels, k=100) == pytest.approx(0.0)
