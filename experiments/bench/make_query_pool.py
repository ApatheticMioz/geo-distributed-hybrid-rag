#!/usr/bin/env python3
"""Seeded query-pool generator for Campaign v2 (and any future benchmark).

Produces a deterministic pool of ``n`` distinct queries (one query text per
line, matching the ``experiments/bench/queries/*.txt`` convention) from a TSV
source of ``qid<TAB>text`` lines, with:

  * determinism: same seed + byte-identical source file => byte-identical
    output (the source is pinned in the manifest by sha256);
  * optional stratification by number of qrel lines per query (from a
    ``qid<TAB>run<TAB>doc<TAB>rel`` qrels file) into bands
    ``0 / 1-4 / 5-9 / 10+`` with proportional, deterministic allocation;
  * disjointness: ``--exclude`` lists pre-existing pool files whose query
    texts must NOT appear in the new pool; the generator asserts the
    intersection of the drawn pool against each exclusion is empty and
    fails loudly (refuses to write) otherwise;
  * manifest: a JSON sidecar recording seed, source path + sha256, n,
    sampling method, exclusion list (+ verified per-file overlap counts),
    output sha256, UTC timestamp, and first/last query ids.

Usage (canonical Campaign v2 invocation):

    python3 experiments/bench/make_query_pool.py \
        --source /home/apath/Work/PDC/data/msmarco/queries.dev.tsv \
        --qrels  /home/apath/Work/PDC/data/msmarco/qrels.dev.small.tsv \
        --n 400 --seed 42 \
        --out experiments/bench/queries/campaign_v2_core400_seed42.txt \
        --exclude experiments/bench/queries/latency_dev480_seed42.txt \
        --exclude experiments/bench/queries/latency_dev60_seed42.txt \
        --manifest experiments/bench/queries/campaign_v2_core400_seed42.txt.manifest.json

Note on the RNG: sampling uses ``numpy.random.default_rng(seed)``, which is
deliberately a different stream from the ``random.Random(seed)`` shuffle used
by the legacy ``scripts/make_latency_sample.py`` -- a same-seed pool is
therefore never a re-cut of the legacy pools' line order, and disjointness
against them is additionally enforced explicitly via ``--exclude``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np


# ---------------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------------

def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_tsv_source(path: str) -> tuple[list[str], list[str]]:
    """Return (query_ids, query_texts) preserving file order, skipping
    malformed / blank lines. The two lists are 1:1 aligned by position."""
    qids: list[str] = []
    texts: list[str] = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 2 or not parts[0].strip() or not parts[1].strip():
                print(f"warn: {path}:{lineno}: skipped malformed line", file=sys.stderr)
                continue
            qids.append(parts[0].strip())
            texts.append(parts[1].strip())
    return qids, texts


def load_qrels_counts(path: str) -> dict[str, int]:
    """Map qid -> number of qrel lines (relevant-passage proxy). Queries
    absent from the file have count 0 by construction."""
    counts: dict[str, int] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            # TREC-style: qid run docid rel [score]
            if len(parts) < 4:
                continue
            qid = parts[0].strip()
            if qid:
                counts[qid] = counts.get(qid, 0) + 1
    return counts


def load_pool_texts(path: str) -> set[str]:
    """Query texts of an existing one-query-per-line pool file."""
    with open(path, encoding="utf-8") as f:
        return {line.rstrip("\n") for line in f if line.strip()}


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------

BAND_LABELS = ("0", "1-4", "5-9", "10+")


def band_of(k: int) -> str:
    if k <= 0:
        return BAND_LABELS[0]
    if k <= 4:
        return BAND_LABELS[1]
    if k <= 9:
        return BAND_LABELS[2]
    return BAND_LABELS[3]


def allocate_proportional(band_sizes: list[int], n: int) -> list[int]:
    """Deterministic proportional allocation of ``n`` across bands using
    largest-remainder (Hamilton) rounding, capped by band size; any
    shortfall (capping or rounding) is redistributed in band order."""
    total = sum(band_sizes)
    if total == 0:
        return [0] * len(band_sizes)
    raw = [n * s / total for s in band_sizes]
    alloc = [min(int(r), s) for r, s in zip(raw, band_sizes)]
    deficit = n - sum(alloc)
    if deficit > 0:
        order = sorted(
            range(len(band_sizes)), key=lambda i: (-(raw[i] - int(raw[i])), i)
        )
        for i in order:
            if deficit <= 0:
                break
            take = min(band_sizes[i] - alloc[i], deficit)
            alloc[i] += take
            deficit -= take
        if deficit > 0:
            for i in range(len(band_sizes)):
                if deficit <= 0:
                    break
                take = min(band_sizes[i] - alloc[i], deficit)
                alloc[i] += take
                deficit -= take
        if deficit > 0:
            raise ValueError(
                f"cannot allocate n={n} across bands sized {band_sizes}"
            )
    elif deficit < 0:
        for i in range(len(band_sizes)):
            if deficit >= 0:
                break
            take = min(-deficit, alloc[i])
            alloc[i] -= take
            deficit += take
    return alloc


def sample_uniform(
    eligible: list[int], n: int, rng: np.random.Generator
) -> list[int]:
    """n distinct source positions drawn without replacement; n distinct
    texts guaranteed when source texts are unique (checked by caller)."""
    if n > len(eligible):
        raise SystemExit(
            f"error: requested n={n} but only {len(eligible)} eligible distinct "
            f"queries remain after exclusions; reduce --n or relax --exclude"
        )
    pool = np.asarray(eligible, dtype=np.int64)
    return [int(i) for i in rng.choice(pool, size=n, replace=False)]


def sample_stratified(
    eligible: list[int],
    qids: list[str],
    qrel_counts: dict[str, int],
    n: int,
    seed: int,
) -> tuple[list[int], dict[str, int], dict[str, int]]:
    """Stratified draw: proportional allocation across qrel-count bands,
    each band sampled by its own child stream of SeedSequence(seed), so the
    overall pool is deterministic for a fixed seed."""
    band_idx: dict[str, list[int]] = {b: [] for b in BAND_LABELS}
    for i in eligible:
        band_idx[band_of(qrel_counts.get(qids[i], 0))].append(i)
    sizes = [len(band_idx[b]) for b in BAND_LABELS]
    requested = allocate_proportional(sizes, n)
    ss = np.random.SeedSequence(seed).spawn(len(BAND_LABELS))
    selected: list[int] = []
    actual: dict[str, int] = {}
    for j, b in enumerate(BAND_LABELS):
        want = min(requested[j], len(band_idx[b]))
        if requested[j] > len(band_idx[b]):
            print(
                f"warn: band '{b}' requested {requested[j]} but has "
                f"{len(band_idx[b])}; taking all of it",
                file=sys.stderr,
            )
        if want:
            rng = np.random.default_rng(ss[j])
            selected.extend(
                int(i)
                for i in rng.choice(
                    np.asarray(band_idx[b], dtype=np.int64), size=want, replace=False
                )
            )
        actual[b] = want
    return selected, {b: requested[i] for i, b in enumerate(BAND_LABELS)}, actual


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--source", required=True,
        help="TSV file, qid<TAB>text per line (MS MARCO queries.dev.tsv)",
    )
    ap.add_argument(
        "--qrels", default=None,
        help="optional TSV qrels (qid run doc rel) enabling --stratify",
    )
    ap.add_argument("--n", type=int, required=True, help="pool size (distinct queries)")
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--out", required=True, help="output pool path, one query per line")
    ap.add_argument(
        "--exclude", action="append", default=[],
        help="existing pool file to keep disjoint from (repeatable)",
    )
    ap.add_argument(
        "--stratify", action="store_true",
        help="sample by qrels bands 0/1-4/5-9/10+ instead of uniform (requires --qrels)",
    )
    ap.add_argument(
        "--manifest", default=None,
        help="JSON manifest path (default: <out>.manifest.json)",
    )
    args = ap.parse_args()

    if args.stratify and args.qrels is None:
        ap.error("--stratify requires --qrels")

    # -- source ----------------------------------------------------------
    qids, texts = load_tsv_source(args.source)
    n_total = len(qids)
    src_sha = sha256_file(args.source)
    if n_total == 0:
        raise SystemExit(f"error: no usable queries in {args.source}")

    # -- exclusions (disjointness, enforced before and after the draw) ---
    excluded_sets: list[set[str]] = [load_pool_texts(p) for p in args.exclude]
    excluded_union: set[str] = set()
    for s in excluded_sets:
        excluded_union |= s
    eligible = [i for i, t in enumerate(texts) if t not in excluded_union]

    # -- sample -----------------------------------------------------------
    if args.stratify:
        qrel_counts = load_qrels_counts(args.qrels)
        n_qrel_queries = sum(1 for q in qids if q in qrel_counts)
        selected, requested, actual = sample_stratified(
            eligible, qids, qrel_counts, args.n, args.seed
        )
        sizes = [
            sum(1 for i in eligible if band_of(qrel_counts.get(qids[i], 0)) == b)
            for b in BAND_LABELS
        ]
        method: dict = {
            "type": "stratified",
            "qrels_file": args.qrels,
            "qrels_sha256": sha256_file(args.qrels),
            "n_qrels_queries_in_source": n_qrel_queries,
            "bands": {
                b: {"size": sizes[i], "requested": requested[b], "actual": actual[b]}
                for i, b in enumerate(BAND_LABELS)
            },
        }
    else:
        rng = np.random.default_rng(args.seed)
        selected = sample_uniform(eligible, args.n, rng)
        method = {
            "type": "uniform",
            "rng": "numpy.random.default_rng(seed)",
            "n_source_queries": n_total,
            "n_eligible_after_exclusion": len(eligible),
        }

    # Deterministic file layout: restore source (qid) order after the draw.
    selected.sort()

    # -- verify disjointness of the actual draw (fail loudly) ------------
    total_violations = 0
    overlap_report: dict[str, int] = {}
    for p, s in zip(args.exclude, excluded_sets):
        v = sum(1 for i in selected if texts[i] in s)
        overlap_report[p] = v
        total_violations += v
        if v:
            common = sorted({texts[i] for i in selected if texts[i] in s})
            print(
                f"DISJOINTNESS VIOLATION vs {p}: {v} shared queries; "
                f"first: {common[:3]}",
                file=sys.stderr,
            )
    if total_violations:
        raise SystemExit(
            f"error: new pool overlaps excluded pools by {total_violations} "
            f"queries; refusing to write {args.out}"
        )

    # -- write pool --------------------------------------------------------
    out_dir = os.path.dirname(args.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    sel_texts = [texts[i] for i in selected]
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(sel_texts) + "\n")
    out_sha = sha256_file(args.out)

    # -- manifest ----------------------------------------------------------
    manifest_path = args.manifest or (args.out + ".manifest.json")
    manifest = {
        "schema": "pdc.query_pool_manifest/v1",
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "seed": args.seed,
        "source": {
            "path": args.source,
            "sha256": src_sha,
            "n_queries": n_total,
        },
        "n": args.n,
        "sampling": method,
        "exclusions": [
            {
                "path": p,
                "sha256": sha256_file(p),
                "n_queries": len(s),
                "overlap_with_new_pool": overlap_report[p],
            }
            for p, s in zip(args.exclude, excluded_sets)
        ],
        "disjointness_asserted": True,
        "output": {
            "path": args.out,
            "sha256": out_sha,
            "line_count": len(sel_texts),
            "first_query_id": qids[selected[0]],
            "last_query_id": qids[selected[-1]],
            "first_query": sel_texts[0],
            "last_query": sel_texts[-1],
        },
    }
    with open(manifest_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")

    print(
        f"pool written: {args.out} n={len(sel_texts)} "
        f"(source {n_total}, eligible after exclusion {len(eligible)}, "
        f"excluded {n_total - len(eligible)}) sha256={out_sha[:16]}… "
        f"manifest={manifest_path}"
    )


if __name__ == "__main__":
    main()
