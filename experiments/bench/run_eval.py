#!/usr/bin/env python3
"""
Per-mode retrieval evaluation driver for the Node B hybrid gateway.

Sends a fixed set of MS MARCO dev queries (intersected with qrels) to the
Node B ``/query/benchmark`` endpoint in a single retrieval mode, and writes
one JSONL record per query carrying the per-leg ranked doc-id arrays and
timings.

The driver is intentionally dependency-free (stdlib only) so it can run from
the orchestration host against the live gateway.

Usage:
    python benchmarks/run_eval.py \
        --gateway http://10.8.0.2:8000 \
        --queries /home/apath/Work/PDC/data/msmarco/queries.dev.tsv \
        --qrels   /home/apath/Work/PDC/data/msmarco/qrels.dev.small.tsv \
        --mode hybrid --limit 100 --top-k 100 --rrf-k 60 \
        --out benchmarks/eval_hybrid.jsonl

    Add --retrieval-only to skip Node A generation (quality runs): the
    gateway returns per-mode rankings + retrieval timings with zeroed
    generation metrics and no gRPC call.

Behavior:
    * Loads queries.tsv (col0=qid, col1=text) and keeps only qids that also
      appear in qrels.tsv (col0=qid, col2=pid) — the evaluable set.
    * Truncates the evaluable set to ``--limit`` queries (file order).
    * Sends 2 warmup requests first (not measured, not written) to prime the
      GPU / caches.
    * Iterates the evaluable set sequentially, fail-fast with no retries.
    * Writes one JSONL line per query:
        {qid, query, mode, top_k, rrf_k,
         sparse_doc_ids, dense_doc_ids, fused_doc_ids, timings}
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

VALID_MODES = ("hybrid", "sparse", "dense")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_queries(path: str) -> list[tuple[str, str]]:
    """Load queries.tsv (qid<TAB>text) -> list of (qid, text) in file order."""
    queries: list[tuple[str, str]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            qid, text = parts[0].strip(), parts[1]
            if qid and text:
                queries.append((qid, text))
    return queries


def load_qrel_qids(path: str) -> set[str]:
    """Load qrels.tsv (col0=qid, col2=pid) -> set of qids that have qrels."""
    qids: set[str] = set()
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) >= 3:
                qid = parts[0].strip()
                if qid:
                    qids.add(qid)
    return qids


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def http_post_json(url: str, payload: dict, timeout: int = 120) -> dict:
    """POST JSON to ``url`` and return the parsed JSON response.

    Raises on any HTTP / network error (caller decides to fail-fast).
    """
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check_health(gateway: str) -> dict:
    url = gateway.rstrip("/") + "/health"
    with urllib.request.urlopen(url, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Per-mode retrieval evaluation driver for the Node B gateway.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--gateway", default="http://10.8.0.2:8000",
                    help="Node B gateway base URL")
    ap.add_argument("--queries", required=True,
                    help="Path to queries.tsv (col0=qid, col1=text)")
    ap.add_argument("--qrels", required=True,
                    help="Path to qrels.tsv (col0=qid, col2=pid)")
    ap.add_argument("--mode", default="hybrid", choices=VALID_MODES,
                    help="Retrieval mode sent to the gateway")
    ap.add_argument("--limit", type=int, default=100,
                    help="Number of queries to evaluate (after warmups)")
    ap.add_argument("--seed", type=int, default=None,
                    help="When set, sample --limit queries uniformly at random "
                         "(seeded shuffle) from the evaluable set instead of "
                         "taking the first --limit in file order. Use a fixed "
                         "seed for reproducible, order-unbiased eval sets.")
    ap.add_argument("--top-k", type=int, default=100,
                    help="top_k sent to the gateway")
    ap.add_argument("--rrf-k", type=int, default=60,
                    help="RRF constant k sent to the gateway")
    ap.add_argument("--retrieval-only", action="store_true", default=False,
                    help="Send retrieval_only=true: gateway skips Node A "
                         "generation and returns rankings + retrieval timings "
                         "only (zeroed generation metrics)")
    ap.add_argument("--out", required=True, help="Output JSONL path")
    args = ap.parse_args()

    # 1. Load + intersect queries with qrels.
    queries = load_queries(args.queries)
    qrel_qids = load_qrel_qids(args.qrels)
    eval_queries = [(qid, text) for qid, text in queries if qid in qrel_qids]
    if not eval_queries:
        print("ERROR: no queries in common with qrels", file=sys.stderr)
        sys.exit(1)
    if args.seed is not None:
        import random
        random.Random(args.seed).shuffle(eval_queries)
    eval_queries = eval_queries[: args.limit]

    # 2. Health check (fail-fast if the gateway is down).
    try:
        health = check_health(args.gateway)
    except Exception as e:
        print(f"ERROR: gateway health check failed at {args.gateway}: {e}",
              file=sys.stderr)
        sys.exit(1)

    print(f"Gateway healthy: {health}")
    print(f"Mode={args.mode} top_k={args.top_k} rrf_k={args.rrf_k} | "
          f"{len(eval_queries)} queries "
          f"(of {len(queries)} total, {len(qrel_qids)} qrel qids)")

    endpoint = args.gateway.rstrip("/") + "/query/benchmark"

    # 3. Warmups (2, not measured, not written) — prime GPU / caches.
    for w in range(2):
        wq = f"warmup query {w}"
        t0 = time.perf_counter()
        try:
            res = http_post_json(endpoint, {
                "query": wq,
                "top_k": args.top_k,
                "mode": args.mode,
                "rrf_k": args.rrf_k,
                "retrieval_only": args.retrieval_only,
            })
        except Exception as e:
            print(f"ERROR: warmup [{w + 1}/2] failed: {e}", file=sys.stderr)
            sys.exit(1)
        t = res.get("timings", {})
        print(f"  Warmup [{w + 1}/2]: sparse={t.get('sparse_ms', 0):.1f}ms "
              f"dense={t.get('dense_ms', 0):.1f}ms "
              f"total={t.get('total_ms', 0):.1f}ms "
              f"(client {(time.perf_counter() - t0) * 1000:.0f}ms)")

    # 4. Main loop: sequential, fail-fast, no retries.
    n_ok = 0
    with open(args.out, "w", encoding="utf-8") as out:
        for i, (qid, text) in enumerate(eval_queries, 1):
            t0 = time.perf_counter()
            try:
                res = http_post_json(endpoint, {
                    "query": text,
                    "top_k": args.top_k,
                    "mode": args.mode,
                    "rrf_k": args.rrf_k,
                    "retrieval_only": args.retrieval_only,
                })
            except Exception as e:
                print(f"ERROR: query qid={qid} ('{text[:40]}') failed: {e}",
                      file=sys.stderr)
                sys.exit(1)  # fail-fast, no retries

            record = {
                "qid": qid,
                "query": text,
                "mode": args.mode,
                "top_k": args.top_k,
                "rrf_k": args.rrf_k,
                "sparse_doc_ids": res.get("sparse_doc_ids", []),
                "dense_doc_ids": res.get("dense_doc_ids", []),
                "fused_doc_ids": res.get("fused_doc_ids", []),
                "timings": res.get("timings", {}),
            }
            out.write(json.dumps(record) + "\n")
            out.flush()
            n_ok += 1

            t = record["timings"]
            client_ms = (time.perf_counter() - t0) * 1000.0
            print(f"  [{i:3d}/{len(eval_queries)}] qid={qid} "
                  f"sparse={t.get('sparse_ms', 0):6.1f}ms "
                  f"dense={t.get('dense_ms', 0):6.1f}ms "
                  f"fusion={t.get('fusion_ms', 0):5.1f}ms "
                  f"total={t.get('total_ms', 0):6.0f}ms "
                  f"client={client_ms:6.0f}ms | '{text[:30]}...'")

    # 5. Sanity check: warn if the gateway ignored the new params.
    first = json.loads(open(args.out, "r", encoding="utf-8").readline())
    if not (first.get("sparse_doc_ids") or first.get("dense_doc_ids")
            or first.get("fused_doc_ids")):
        print("WARNING: first record has no doc-id arrays — the gateway may "
              "not support mode/rrf_k (old server.py?).", file=sys.stderr)

    print(f"\nDone. {n_ok}/{len(eval_queries)} queries written to {args.out}")


if __name__ == "__main__":
    main()
