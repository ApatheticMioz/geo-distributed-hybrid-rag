"""
Reciprocal Rank Fusion (RRF) for hybrid retrieval on Node B.

Fuses sparse (BM25) and dense (BGE-M3) ranked lists of doc_ids
into a single unified ranking before sending to Node A.
"""


def reciprocal_rank_fusion(
    sparse_doc_ids: list[str],
    dense_doc_ids: list[str] | None,
    k: int = 60,
) -> list[str]:
    """
    Fuse two ranked lists of doc_ids using Reciprocal Rank Fusion (RRF).

    Both inputs are expected to be lists ordered by rank (best first).
    If `dense_doc_ids` is None or empty, only `sparse_doc_ids` is used.

    Returns a list of doc_ids sorted by RRF score descending.
    """
    scores: dict[str, float] = {}

    def _add_list(results: list[str]) -> None:
        for rank, doc_id in enumerate(results, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)

    if sparse_doc_ids:
        _add_list(sparse_doc_ids)

    if dense_doc_ids:
        _add_list(dense_doc_ids)

    # Sort by score descending and return doc_ids
    fused = sorted(scores.keys(), key=lambda did: scores[did], reverse=True)
    return fused