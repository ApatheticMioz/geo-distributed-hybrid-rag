#!/usr/bin/env python3
"""Offline quality-metrics CLI for the Node B hybrid retrieval gateway.

Reads a JSONL run file produced by ``benchmarks/run_eval.py`` (one record per
query, each carrying ``qid`` and one or more ``<mode>_doc_ids`` arrays),
scores every ``*_doc_ids`` array present against a TREC qrels file, and writes
a JSON report.

Report shape::

    {
      "<mode>": {
        "n_queries": <int>,
        "mrr": <float>,          # full-pool MRR (official MS MARCO = top-1000)
        "mrr@10": <float>,
        "ndcg@10": <float>,
        "recall@100": <float>,
        "recall@1000": <float>
      },
      ...
    }

The ``<mode>`` key is the ranking key with the trailing ``"_doc_ids"``
stripped (e.g. ``"sparse_doc_ids"`` -> ``"sparse"``). A mode is auto-detected
from whichever ``*_doc_ids`` arrays appear in the run file, so the same CLI
works for hybrid / sparse / dense runs without extra flags.

Usage::

    python eval/compute_metrics.py \
        --run /tmp/eval_sparse25.jsonl \
        --qrels /home/apath/Work/PDC/data/msmarco/qrels.dev.small.tsv \
        --out /tmp/metrics_sparse25.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make the package importable both as ``python eval/compute_metrics.py``
# (script mode, where the package dir is on sys.path) and as
# ``python -m eval.compute_metrics`` (package mode).
try:
    from .metrics import load_qrels, mrr, mrr_at_10, ndcg_at_10, recall_at_k
except ImportError:  # pragma: no cover - script-mode fallback
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from metrics import (  # type: ignore
        load_qrels, mrr, mrr_at_10, ndcg_at_10, recall_at_k,
    )

# Ranking keys that are NOT retrieval rankings (defensive: skip if present).
_NON_RANKING_KEYS = {"qid", "query", "mode", "top_k", "rrf_k", "timings"}


def _load_runs(path: str) -> list[dict]:
    """Load a JSONL run file into a list of record dicts."""
    runs: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            runs.append(json.loads(line))
    return runs


def _detect_ranking_keys(runs: list[dict]) -> list[str]:
    """Return the sorted set of ``*_doc_ids`` keys present across all records.

    Only keys ending in ``"_doc_ids"`` are treated as rankings; everything
    else (qid, query, mode, top_k, rrf_k, timings, ...) is ignored.
    """
    keys: set[str] = set()
    for rec in runs:
        for k in rec.keys():
            if k.endswith("_doc_ids") and k not in _NON_RANKING_KEYS:
                keys.add(k)
    return sorted(keys)


def _mode_name(key: str) -> str:
    """``"sparse_doc_ids"`` -> ``"sparse"`` (strip the ``_doc_ids`` suffix)."""
    return key[: -len("_doc_ids")] if key.endswith("_doc_ids") else key


def compute_report(runs: list[dict], qrels: dict[str, set[str]]) -> dict:
    """Score every detected ``*_doc_ids`` ranking and build the report dict."""
    report: dict = {}
    for key in _detect_ranking_keys(runs):
        report[_mode_name(key)] = {
            "n_queries": len(_evaluable(runs, qrels)),
            "mrr": round(mrr(runs, qrels, key=key), 6),
            "mrr@10": round(mrr_at_10(runs, qrels, key=key), 6),
            "ndcg@10": round(ndcg_at_10(runs, qrels, key=key), 6),
            "recall@100": round(recall_at_k(runs, qrels, key=key, k=100), 6),
            "recall@1000": round(recall_at_k(runs, qrels, key=key, k=1000), 6),
        }
    return report


def _evaluable(runs: list[dict], qrels: dict[str, set[str]]) -> list[str]:
    """Qids present in both the runs and the qrels (deduped, run order)."""
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Offline quality metrics (MRR / nDCG / Recall) for Node B "
                    "retrieval runs.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--run", required=True,
                    help="JSONL run file from benchmarks/run_eval.py")
    ap.add_argument("--qrels", required=True,
                    help="TREC qrels file (col0=qid, col2=pid)")
    ap.add_argument("--out", required=True, help="Output JSON report path")
    args = ap.parse_args(argv)

    runs = _load_runs(args.run)
    if not runs:
        print(f"ERROR: no records found in {args.run}", file=sys.stderr)
        return 1

    qrels = load_qrels(args.qrels)
    report = compute_report(runs, qrels)

    if not report:
        print("WARNING: no *_doc_ids ranking arrays found in the run file; "
              "writing an empty report.", file=sys.stderr)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
        f.write("\n")

    # Human-readable summary to stdout.
    print(f"Scored {len(runs)} run records against {len(qrels)} qrel qids.")
    print(json.dumps(report, indent=2))
    print(f"\nWrote report to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
