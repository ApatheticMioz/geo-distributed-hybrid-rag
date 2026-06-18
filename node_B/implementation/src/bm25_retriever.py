"""
BM25 Sparse Retrieval module for Node B.

Ported from Node C's Tantivy-based implementation so Node B can perform
both dense (BGE-M3) and sparse (BM25) retrieval concurrently,
then fuse results locally via Reciprocal Rank Fusion.
"""

import logging
import time
from pathlib import Path
from typing import List, Dict, Any

import tantivy

logger = logging.getLogger(__name__)


class BM25Retriever:
    """
    Disk-backed BM25 index using Tantivy (Rust/Lucene-equivalent).

    The index lives on disk. Tantivy memory-maps active segments only.
    Tokenization is handled by Tantivy's compiled Rust tokenizer.
    """

    def __init__(self, index_path: str):
        self.index_path = Path(index_path)
        if not self.index_path.exists():
            raise FileNotFoundError(
                f"Tantivy index not found at '{index_path}'. "
                "Run the index building script first."
            )
        self._index = tantivy.Index.open(str(self.index_path))
        self._searcher = self._index.searcher()
        logger.info(
            "BM25 index opened: %s | %d docs",
            self.index_path,
            self._searcher.num_docs,
        )

    def query(self, query_text: str, top_k: int = 10) -> List[Dict[str, Any]]:
        """
        Returns top_k BM25-ranked results.
        Tokenization and scoring are performed inside the Rust runtime.
        """
        t_start = time.perf_counter()

        query = self._index.parse_query(query_text, default_field_names=["body"])
        search_result = self._searcher.search(query, top_k)
        results_list = search_result.hits if hasattr(search_result, 'hits') else search_result

        results = []
        for rank, result in enumerate(results_list, start=1):
            score, doc_address = result
            doc = self._searcher.doc(doc_address)
            results.append({
                "doc_id": doc.get_first("doc_id"),
                "text": doc.get_first("body"),
                "score": float(score),
                "rank": rank,
            })

        elapsed_ms = (time.perf_counter() - t_start) * 1000
        logger.debug("BM25 query: %.1f ms, %d results", elapsed_ms, len(results))
        return results
