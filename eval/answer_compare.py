#!/usr/bin/env python3
"""Measure the answer-level cost of SPHP's speculative hit path (paper F-25).

For each sampled MS MARCO dev query the driver runs the full pipeline TWICE
against the Node B gateway: once in baseline mode (sphp=false, answer
generated from the fused context) and once with SPHP enabled (sphp=true; on
a speculative hit the answer is generated from the sparse-only hint context).
It then compares the two answers per query:

  * EM       — normalized exact-match agreement between the two answers
  * token-F1 — bag-of-words F1 between the two answers
  * len ratio — sphp answer length / baseline answer length

reported separately over hit and miss subsets, so the paper's "no quality
cost" claim rests on measured agreement instead of assumption.

Requires the gateway to expose ``answer_full`` on /query/benchmark
(node_B/src/server.py); the script fails loudly on truncated previews.

Usage::

    python3 eval/answer_compare.py \
        --gateway http://10.8.0.2:8000 \
        --queries /home/apath/Work/PDC/data/msmarco/queries.dev.tsv \
        --qrels   /home/apath/Work/PDC/data/msmarco/qrels.dev.small.tsv \
        --limit 200 --seed 42 --top-k 10 \
        --out /tmp/answers_sphp.jsonl \
        --report analysis/results/answer_comparison.json
"""
from __future__ import annotations

import argparse
import json
import random
import re
import string
import sys
import time
import urllib.request
from pathlib import Path

VALID_MODES = ("hybrid", "sparse", "dense")


def load_queries(path: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2 and parts[0].strip() and parts[1].strip():
                out.append((parts[0].strip(), parts[1].strip()))
    return out


def load_qrel_qids(path: str) -> set[str]:
    qids: set[str] = set()
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 3 and parts[0].strip():
                qids.add(parts[0].strip())
    return qids


def post_benchmark(gateway: str, payload: dict, timeout: int = 180) -> dict:
    req = urllib.request.Request(
        gateway.rstrip("/") + "/query/benchmark",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _normalize(text: str) -> str:
    text = text.lower()
    text = text.translate(str.maketrans("", "", string.punctuation))
    return " ".join(text.split())


def em(a: str, b: str) -> float:
    return float(_normalize(a) == _normalize(b) and bool(_normalize(a)))


def token_f1(a: str, b: str) -> float:
    ta, tb = _normalize(a).split(), _normalize(b).split()
    if not ta or not tb:
        return 0.0
    common = set(ta) & set(tb)
    if not common:
        return 0.0
    prec = len(common) / len(tb)
    rec = len(common) / len(ta)
    return 2 * prec * rec / (prec + rec)


def summarize(pairs: list[dict]) -> dict:
    if not pairs:
        return {"n": 0}
    ems = [p["em"] for p in pairs]
    f1s = [p["token_f1"] for p in pairs]
    lens = [p["len_ratio"] for p in pairs if p["len_ratio"] is not None]
    return {
        "n": len(pairs),
        "em_mean": round(sum(ems) / len(ems), 4),
        "token_f1_mean": round(sum(f1s) / len(f1s), 4),
        "len_ratio_mean": round(sum(lens) / len(lens), 4) if lens else None,
        "em_exact_matches": sum(1 for e in ems if e == 1.0),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gateway", default="http://10.8.0.2:8000")
    ap.add_argument("--queries", required=True)
    ap.add_argument("--qrels", required=True)
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--rrf-k", type=int, default=60)
    ap.add_argument("--mode", default="hybrid", choices=VALID_MODES)
    ap.add_argument("--out", required=True, help="per-query JSONL with both answers")
    ap.add_argument("--report", default="analysis/results/answer_comparison.json")
    args = ap.parse_args()

    queries = load_queries(args.queries)
    qrel_qids = load_qrel_qids(args.qrels)
    evaluable = [(qid, text) for qid, text in queries if qid in qrel_qids]
    random.Random(args.seed).shuffle(evaluable)
    sample = evaluable[: args.limit]
    if len(sample) < args.limit:
        print(f"WARNING: only {len(sample)} evaluable queries available "
              f"(requested {args.limit})", file=sys.stderr)
    if not sample:
        print("ERROR: no evaluable queries", file=sys.stderr)
        return 1

    print(f"Answer comparison: {len(sample)} queries, seed={args.seed}, "
          f"top_k={args.top_k}, mode={args.mode}")

    n_ok = 0
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as out:
        for i, (qid, text) in enumerate(sample, 1):
            base = post_benchmark(args.gateway, {
                "query": text, "top_k": args.top_k, "mode": args.mode,
                "rrf_k": args.rrf_k, "sphp": False,
            })
            sphp = post_benchmark(args.gateway, {
                "query": text, "top_k": args.top_k, "mode": args.mode,
                "rrf_k": args.rrf_k, "sphp": True,
            })
            def full_or_fail(res: dict) -> str:
                full = res.get("answer_full") or ""
                if full:
                    return full
                prev = res.get("answer_preview") or ""
                if prev and not prev.endswith("..."):
                    return prev  # short answer, preview was not truncated
                print(f"ERROR: no untruncated answer for qid={qid} — deploy "
                      f"node_B server with the answer_full field", file=sys.stderr)
                raise SystemExit(2)

            a_base = full_or_fail(base)
            a_sphp = full_or_fail(sphp)

            lr = (len(a_sphp) / len(a_base)) if a_base else None
            rec = {
                "qid": qid,
                "query": text,
                "answer_baseline": a_base,
                "answer_sphp": a_sphp,
                "sphp_hit": sphp.get("sphp_hit"),
                "sphp_overlap": sphp.get("sphp_overlap"),
                "wasted_prefill_ms": sphp.get("wasted_prefill_ms"),
                "ttft_baseline_ms": base.get("timings", {}).get("ttft_ms"),
                "ttft_sphp_ms": sphp.get("timings", {}).get("ttft_ms"),
                "em": em(a_base, a_sphp),
                "token_f1": round(token_f1(a_base, a_sphp), 4),
                "len_ratio": round(lr, 4) if lr is not None else None,
            }
            out.write(json.dumps(rec) + "\n")
            out.flush()
            n_ok += 1
            hit = rec["sphp_hit"]
            print(f"  [{i:3d}/{len(sample)}] qid={qid} hit={hit} "
                  f"ovl={rec['sphp_overlap']} em={rec['em']:.0f} "
                  f"f1={rec['token_f1']:.2f} "
                  f"ttft {rec['ttft_baseline_ms']:.0f}->{rec['ttft_sphp_ms']:.0f}ms")

    # Aggregate report over the recorded pairs.
    pairs = [json.loads(l) for l in open(out_path, encoding="utf-8")]
    hits = [p for p in pairs if p["sphp_hit"]]
    misses = [p for p in pairs if p["sphp_hit"] is False]
    unknown = [p for p in pairs if p["sphp_hit"] is None]
    hit_rate = len(hits) / max(1, len(hits) + len(misses))
    ttft_b = [p["ttft_baseline_ms"] for p in pairs if p["ttft_baseline_ms"]]
    ttft_s = [p["ttft_sphp_ms"] for p in pairs if p["ttft_sphp_ms"]]
    report = {
        "title": "SPHP answer-level cost — baseline vs speculative hit path",
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "generator": "eval/answer_compare.py",
        "provenance": {
            "gateway": args.gateway,
            "queries_file": args.queries,
            "qrels_file": args.qrels,
            "sampling": f"seeded random sample (seed={args.seed}), limit={args.limit}",
            "top_k": args.top_k,
            "rrf_k": args.rrf_k,
            "run_file": str(out_path.resolve()),
        },
        "n_queries": len(pairs),
        "sphp_hit_rate": round(hit_rate, 4),
        "measured_hit_rate_note": (
            "Measured hit rate replaces the previously assumed h=0.60."),
        "agreement": {
            "hits": summarize(hits),
            "misses": summarize(misses),
            "unknown_hit": len(unknown),
            "all": summarize(pairs),
        },
        "ttft_ms": {
            "baseline_mean": round(sum(ttft_b) / len(ttft_b), 1) if ttft_b else None,
            "sphp_mean": round(sum(ttft_s) / len(ttft_s), 1) if ttft_s else None,
        },
        "findings": [
            f"Measured SPHP hit rate: {hit_rate:.2%} over {len(hits) + len(misses)} queries.",
            f"Hit-path answer agreement with baseline: EM="
            f"{summarize(hits).get('em_mean')}, token-F1="
            f"{summarize(hits).get('token_f1_mean')} (n={len(hits)} hits).",
            "Miss-path answers use the reconciled fused context and are expected "
            "to agree with baseline; deviations there indicate a SPHP bug.",
        ],
    }
    rep_path = Path(args.report)
    rep_path.parent.mkdir(parents=True, exist_ok=True)
    with open(rep_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"\nDone. {n_ok} pairs -> {out_path}")
    print(json.dumps(report["agreement"], indent=2))
    print(f"Report -> {rep_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
