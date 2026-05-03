from typing import List


def reciprocal_rank_fusion(sparse_results: List[str], dense_results: List[str] | None, k: int) -> List[str]:
    """Fuse two ranked lists of doc_ids using Reciprocal Rank Fusion (RRF).

    Both inputs are expected to be lists ordered by rank (best first). If
    `dense_results` is None or empty, only `sparse_results` is used.

    Returns a list of doc_ids sorted by RRF score descending.
    """
    scores: dict[str, float] = {}

    def _add_list(results: List[str]):
        for rank, doc_id in enumerate(results, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)

    if sparse_results:
        _add_list(sparse_results)

    if dense_results:
        _add_list(dense_results)

    # Sort by score descending and return doc_ids
    fused = sorted(scores.keys(), key=lambda did: scores[did], reverse=True)
    return fused
