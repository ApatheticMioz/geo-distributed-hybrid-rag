#!/usr/bin/env python3
"""Generate the seeded random latency query sample from MS MARCO dev queries.

Replaces the hand-picked queries50.txt (order/selection bias). Deterministic
given --seed: identical output on any machine, satisfying the reproducibility
contract (paper C4).

Usage:
    python3 scripts/make_latency_sample.py \
        --source /home/apath/Work/PDC/data/msmarco/queries.dev.tsv \
        --out benchmarks/queries/latency_dev60_seed42.txt \
        --n 60 --seed 42
"""
import argparse
import os
import random


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", required=True, help="MS MARCO queries .tsv (qid\\ttext)")
    ap.add_argument("--out", required=True, help="output path, one query text per line")
    ap.add_argument("--n", type=int, default=60, help="sample size (measured + warmups)")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    texts: list[str] = []
    with open(args.source, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2 and parts[1].strip():
                texts.append(parts[1].strip())

    seen: set[str] = set()
    uniq = [t for t in texts if not (t in seen or seen.add(t))]
    random.Random(args.seed).shuffle(uniq)
    sel = uniq[: args.n]

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(sel) + "\n")

    print(f"source={len(texts)} unique={len(uniq)} written={len(sel)} seed={args.seed}")


if __name__ == "__main__":
    main()
