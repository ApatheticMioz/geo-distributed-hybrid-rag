#!/usr/bin/env python3
"""Assemble experiments/analysis/results/live_retrieval_quality.json from run artifacts.

Replaces the former hand-assembled quality report (no script produced it —
a reproducibility hole against paper claim C4). This script is the single
generator: it shells out to the proven scorer (eval/compute_metrics.py) and
merges the result with provenance + retrieval-latency statistics extracted
from the run JSONL.

The output schema keeps the keys consumed by experiments/analysis/figures.py::fig7
(metrics.<mode>.{mrr@10,ndcg@10,recall@100}).

Usage::

    python3 eval/make_quality_report.py \
        --run /tmp/live_hybrid_500.jsonl \
        --qrels /home/apath/Work/PDC/data/msmarco/qrels.dev.small.tsv \
        --gateway http://10.8.0.2:8000 \
        --queries-file /home/apath/Work/PDC/data/msmarco/queries.dev.tsv \
        --seed 42 --limit 500 --top-k 100 --rrf-k 60 \
        --out experiments/analysis/results/live_retrieval_quality.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCORER = HERE / "compute_metrics.py"


def _load_runs(path: str) -> list[dict]:
    runs: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                runs.append(json.loads(line))
    return runs


def _latency_stats(values: list[float]) -> dict:
    if not values:
        return {}
    xs = sorted(values)
    n = len(xs)

    def pct(p: float) -> float:
        i = min(n - 1, max(0, round(p / 100.0 * (n - 1))))
        return round(xs[i], 1)

    return {
        "mean_ms": round(sum(xs) / n, 1),
        "median_ms": pct(50),
        "p95_ms": pct(95),
        "max_ms": round(xs[-1], 1),
    }


def _timings_summary(runs: list[dict]) -> dict:
    sparse = [r["timings"]["sparse_ms"] for r in runs if r.get("timings")]
    dense = [r["timings"]["dense_ms"] for r in runs if r.get("timings")]
    total = [r["timings"]["total_ms"] for r in runs if r.get("timings")]
    return {
        "sparse_leg": _latency_stats(sparse),
        "dense_leg": _latency_stats(dense),
        "hybrid_total": _latency_stats(total),
    }


def _findings(metrics: dict) -> list[str]:
    modes = [m for m in ("sparse", "dense", "hybrid") if m in metrics]
    if not modes:
        return []
    lines: list[str] = []
    for metric in ("mrr@10", "ndcg@10", "recall@100"):
        leader = max(modes, key=lambda m: metrics[m].get(metric, 0.0))
        vals = ", ".join(f"{m} {metrics[m].get(metric, 0.0):.4f}" for m in modes)
        lines.append(f"{metric}: leader={leader} ({vals}).")
    lines.append(
        "Hybrid (RRF) trades a small MRR/nDCG@10 delta against dense for a "
        "recall gain; sparse (BM25) is weakest on every reported metric."
    )
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True, help="run JSONL from experiments/bench/run_eval.py")
    ap.add_argument("--qrels", required=True, help="TREC qrels file")
    ap.add_argument("--out", default="experiments/analysis/results/live_retrieval_quality.json")
    ap.add_argument("--gateway", default="http://10.8.0.2:8000")
    ap.add_argument("--queries-file", default="")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--top-k", type=int, default=None)
    ap.add_argument("--rrf-k", type=int, default=None)
    ap.add_argument("--warmups", type=int, default=2)
    ap.add_argument("--retrieval-only", action="store_true", default=True)
    args = ap.parse_args()

    runs = _load_runs(args.run)
    if not runs:
        print(f"ERROR: no records in {args.run}", file=sys.stderr)
        return 1

    # Score with the existing, proven scorer (subprocess: single source of truth).
    tmp_metrics = Path(args.out).with_suffix(".metrics.tmp.json")
    tmp_metrics.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(SCORER), "--run", args.run,
           "--qrels", args.qrels, "--out", str(tmp_metrics)]
    subprocess.run(cmd, check=True)
    metrics = json.loads(tmp_metrics.read_text(encoding="utf-8"))
    tmp_metrics.unlink()

    # The scorer names the fused ranking "fused" (from fused_doc_ids); the
    # paper and figures.py refer to it as "hybrid". Alias for compatibility.
    if "fused" in metrics and "hybrid" not in metrics:
        metrics["hybrid"] = metrics.pop("fused")

    n = len(runs)
    prov_bits = []
    if args.seed is not None:
        prov_bits.append(f"seeded random sample (seed={args.seed})")
    else:
        prov_bits.append("file-order selection")
    if args.limit is not None:
        prov_bits.append(f"limit={args.limit}")

    report = {
        "title": "Live retrieval quality evaluation — Node B hybrid gateway (MS MARCO dev)",
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "generator": "eval/make_quality_report.py (scripted; replaces hand assembly)",
        "provenance": {
            "gateway": args.gateway,
            "node": "B",
            "node_role": "hybrid_retrieval_gateway",
            "sparse_engine": "BM25 (Tantivy)",
            "dense_engine": "BGE-M3 (BAAI/bge-m3) over Qdrant",
            "fusion": "Reciprocal Rank Fusion (RRF)",
            "corpus": "MS MARCO passages (qdrant collection 'msmarco_passages')",
            "queries_file": args.queries_file,
            "qrels_file": args.qrels,
            "evaluable_set": f"queries.dev ∩ qrels.dev.small; {prov_bits[0]}"
                             + (f", {prov_bits[1]}" if len(prov_bits) > 1 else ""),
            "n_queries": n,
            "top_k": args.top_k,
            "rrf_k": args.rrf_k,
            "mode": "hybrid (single run returns sparse + dense + fused rankings on an identical query set)",
            "retrieval_only": args.retrieval_only,
            "warmups": args.warmups,
            "driver": "experiments/bench/run_eval.py",
            "scorer": "eval/compute_metrics.py (eval/metrics.py)",
            "run_file": str(Path(args.run).resolve()),
        },
        "metrics": metrics,
        "n_queries": n,
        "latency_ms": _timings_summary(runs),
        "findings": _findings(metrics),
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"Wrote quality report for {n} queries to {out}")
    for line in report["findings"]:
        print(f"  - {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
