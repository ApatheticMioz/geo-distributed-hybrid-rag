"""
One-time build script: converts JSONL corpus -> Tantivy disk-backed BM25 index.

Run from nodeC/ directory:
    python scripts/build_index.py --corpus retrieval/corpus/documents.jsonl \
                                  --index  retrieval/tantivy_index
"""

import argparse
import json
import logging
import time
from pathlib import Path

import tantivy

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
logger = logging.getLogger(__name__)


def build(corpus_path: str, index_path: str, commit_every: int = 50_000):
    corpus_path = Path(corpus_path)
    index_path = Path(index_path)
    index_path.mkdir(parents=True, exist_ok=True)

    schema_builder = tantivy.SchemaBuilder()
    schema_builder.add_text_field("doc_id", stored=True, tokenizer_name="raw")
    schema_builder.add_text_field("body", stored=True, tokenizer_name="en_stem")
    schema = schema_builder.build()

    index = tantivy.Index(schema, path=str(index_path))
    # Tantivy writer takes heap size as a positional argument.
    writer = index.writer(512 * 1024 * 1024)

    t_start = time.perf_counter()
    count = 0
    with open(corpus_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            doc_data = json.loads(line)
            writer.add_document(
                tantivy.Document(
                    doc_id=[doc_data["id"]],
                    body=[doc_data["text"]],
                )
            )
            count += 1
            if count % commit_every == 0:
                writer.commit()
                elapsed = time.perf_counter() - t_start
                logger.info(f"  {count:,} docs indexed | {elapsed:.0f}s elapsed")

    writer.commit()
    index.reload()
    elapsed = time.perf_counter() - t_start
    logger.info(f"Index build complete: {count:,} docs in {elapsed:.1f}s")
    logger.info(f"Index location: {index_path.resolve()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--index", required=True)
    parser.add_argument("--commit-every", type=int, default=50_000)
    args = parser.parse_args()
    build(args.corpus, args.index, args.commit_every)
