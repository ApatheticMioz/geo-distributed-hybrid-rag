import logging
import time
from pathlib import Path
from typing import List, Dict, Any

import tantivy

logger = logging.getLogger(__name__)


class TantivyBM25Index:
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
                "Run scripts/build_index.py first."
            )
        self._index = tantivy.Index.open(str(self.index_path))
        self._searcher = self._index.searcher()
        logger.info(
            f"Tantivy BM25 index opened: {self.index_path} "
            f"| {self._searcher.num_docs} docs"
        )

    def query(self, query_text: str, top_k: int = 10) -> List[Dict[str, Any]]:
        """
        Returns top_k BM25-ranked results.
        Tokenization and scoring are performed inside the Rust runtime.
        """
        t_start = time.perf_counter()

        query = self._index.parse_query(query_text, ["body"])
        hits = self._searcher.search(query, top_k).hits

        results = []
        for rank, (score, doc_address) in enumerate(hits, start=1):
            doc = self._searcher.doc(doc_address)
            results.append({
                "doc_id": doc.get_first("doc_id"),
                "text": doc.get_first("body"),
                "score": float(score),
                "rank": rank,
            })

        elapsed_ms = (time.perf_counter() - t_start) * 1000
        logger.debug(f"Tantivy BM25 query: {elapsed_ms:.1f} ms, {len(results)} results")
        return results
