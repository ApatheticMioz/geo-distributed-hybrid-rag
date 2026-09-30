"""
Discriminative tests for the Node C gateway skeleton (NC-1).

Fault-injection coverage:
  * config loading — a config with a dropped section/key must be
    rejected (ConfigError), not silently accepted;
  * /health gating — without a Tantivy index the gateway must report
    503 not_ready (a skeleton that always reports ok fails this);
  * schema round-trip — the request/response models must be
    field-compatible with Node B's server.py schemas (renaming or
    dropping a field fails this).

Run from systems/node_c/implementation:
    .venv/bin/python -m pytest test_gateway.py -v
"""

import sys
from pathlib import Path

import pytest
import tantivy
import yaml
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.gateway import (  # noqa: E402
    BenchmarkResponse,
    ConfigError,
    QueryRequest,
    _bm25_query,
    _escape_lucene,
    _LUCENE_SPECIALS,
    create_app,
    load_config,
    sparse_retrieve,
)

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


# ============================================================================
# Config loading
# ============================================================================

def test_load_config_real_file():
    cfg = load_config(CONFIG_PATH)
    assert cfg["wireguard_ip"] == "10.8.0.3"
    assert cfg["node_b"] == {"host": "10.8.0.2", "port": 8000}
    assert cfg["node_a"]["host"] == "10.8.0.1"
    assert cfg["node_a"]["grpc_port"] == 50052
    assert cfg["retrieval"]["top_k"] == 10
    assert cfg["retrieval"]["hint_depth"] == 5
    assert cfg["gateway"]["host"] == "0.0.0.0"
    assert cfg["gateway"]["port"] == 8000
    # Relative corpus paths must be resolved against the config dir.
    assert Path(cfg["corpus"]["tantivy_index_path"]).is_absolute()
    assert cfg["corpus"]["tantivy_index_path"].endswith("data/tantivy_index")
    assert cfg["corpus"]["raw_corpus_path"].endswith("data/documents.jsonl")


def test_load_config_missing_file_rejected(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")


def test_load_config_missing_section_rejected(tmp_path):
    # Fault injection: drop the 'gateway' section — must be rejected.
    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    del raw["gateway"]
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ConfigError, match="gateway"):
        load_config(p)


def test_load_config_missing_nested_key_rejected(tmp_path):
    # Fault injection: drop node_b.port — must be rejected.
    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    del raw["node_b"]["port"]
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ConfigError, match="port"):
        load_config(p)


# ============================================================================
# /health readiness gating
# ============================================================================

def _make_config(tmp_path: Path, index_path: Path) -> dict:
    return {
        "wireguard_ip": "10.8.0.3",
        "node_b": {"host": "10.8.0.2", "port": 8000},
        "node_a": {"host": "10.8.0.1", "grpc_port": 50052},
        "corpus": {
            "tantivy_index_path": str(index_path),
            "raw_corpus_path": str(tmp_path / "documents.jsonl"),
        },
        "retrieval": {"top_k": 10, "hint_depth": 5},
        "gateway": {"host": "0.0.0.0", "port": 8000},
    }


def _build_tiny_index(index_path: Path) -> None:
    index_path.mkdir(parents=True, exist_ok=True)
    sb = tantivy.SchemaBuilder()
    sb.add_text_field("doc_id", stored=True, tokenizer_name="raw")
    sb.add_text_field("body", stored=True, tokenizer_name="en_stem")
    idx = tantivy.Index(sb.build(), path=str(index_path))
    writer = idx.writer(16 * 1024 * 1024)
    writer.add_document(tantivy.Document(doc_id=["d1"], body=["distributed computing"]))
    writer.commit()
    idx.reload()


def test_health_not_ready_without_index(tmp_path):
    # Fault injection: no index on disk -> /health must be 503, not ok.
    cfg = _make_config(tmp_path, tmp_path / "no_such_index")
    app = create_app(cfg)
    with TestClient(app) as client:
        r = client.get("/health")
    assert r.status_code == 503
    assert r.json() == {"status": "not_ready", "node": "C"}


def test_health_ready_after_index_loads(tmp_path):
    idx_path = tmp_path / "tantivy_index"
    _build_tiny_index(idx_path)
    cfg = _make_config(tmp_path, idx_path)
    app = create_app(cfg)
    with TestClient(app) as client:
        r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "node": "C"}


# ============================================================================
# Schema round-trip (field-compatibility with Node B's server.py)
# ============================================================================

# Field sets copied from systems/node_b/implementation/src/server.py
# (QueryRequest / BenchmarkResponse). Renaming or dropping a field in
# the Node C models breaks gateway-agnosticism for the campaign harness.
NODE_B_QUERY_FIELDS = {
    "query", "top_k", "mode", "rrf_k", "retrieval_only", "sphp",
}
NODE_B_BENCH_FIELDS = {
    "query_id", "query", "top_k", "mode", "rrf_k", "timings",
    "fused_doc_ids", "sparse_doc_ids", "dense_doc_ids", "token_count",
    "decode_tps", "answer_preview", "answer_full", "sphp", "sphp_hit",
    "sphp_overlap", "wasted_prefill_ms",
}


def test_query_request_fields_match_node_b():
    assert set(QueryRequest.model_fields) == NODE_B_QUERY_FIELDS


def test_benchmark_response_fields_match_node_b():
    assert set(BenchmarkResponse.model_fields) == NODE_B_BENCH_FIELDS


def test_query_request_round_trip():
    payload = {
        "query": "distributed computing",
        "top_k": 5,
        "mode": "hybrid",
        "rrf_k": 60,
        "retrieval_only": False,
        "sphp": True,
    }
    req = QueryRequest.model_validate(payload)
    assert req.model_dump() == payload


def test_benchmark_response_round_trip():
    payload = {
        "query_id": "ab12cd34",
        "query": "distributed computing",
        "top_k": 10,
        "mode": "hybrid",
        "rrf_k": 60,
        "timings": {
            "sparse_ms": 12.3, "dense_ms": 45.6, "fusion_ms": 0.4,
            "ttft_ms": 0, "decode_ms": 0, "total_ms": 58.3,
            "simulated_wan_ms": 0,
        },
        "fused_doc_ids": ["d1", "d2"],
        "sparse_doc_ids": ["d1"],
        "dense_doc_ids": ["d2"],
        "token_count": 0,
        "decode_tps": 0.0,
        "answer_preview": "",
        "answer_full": "",
        "sphp": False,
        "sphp_hit": None,
        "sphp_overlap": None,
        "wasted_prefill_ms": None,
    }
    resp = BenchmarkResponse.model_validate(payload)
    assert resp.model_dump() == payload


# ============================================================================
# Route contracts
# ============================================================================

def test_query_route_returns_501_until_nc2_nc4(tmp_path):
    # The route must exist and be reachable; the pipeline legs are
    # NC-2..NC-4 stubs, so a valid request must surface as 501.
    idx_path = tmp_path / "tantivy_index"
    _build_tiny_index(idx_path)
    app = create_app(_make_config(tmp_path, idx_path))
    with TestClient(app) as client:
        r = client.post("/query", json={"query": "distributed computing", "top_k": 3})
    assert r.status_code == 501
    assert "not yet implemented" in r.json()["detail"]


def test_benchmark_route_returns_501_until_nc2_nc4(tmp_path):
    idx_path = tmp_path / "tantivy_index"
    _build_tiny_index(idx_path)
    app = create_app(_make_config(tmp_path, idx_path))
    with TestClient(app) as client:
        r = client.post(
            "/query/benchmark",
            json={"query": "distributed computing", "top_k": 3},
        )
    assert r.status_code == 501
    assert "not yet implemented" in r.json()["detail"]


def test_invalid_mode_rejected_400(tmp_path):
    # Fault injection: an unknown mode must be rejected before any
    # pipeline leg runs (mirrors Node B's _resolve_mode_and_k).
    idx_path = tmp_path / "tantivy_index"
    _build_tiny_index(idx_path)
    app = create_app(_make_config(tmp_path, idx_path))
    with TestClient(app) as client:
        r = client.post("/query", json={"query": "x", "mode": "bogus"})
    assert r.status_code == 400
    assert "Invalid mode" in r.json()["detail"]


# ============================================================================
# NC-2 — sparse leg (Tantivy BM25) against a fixture index
# ============================================================================

def _build_fixture_index(index_path: Path) -> None:
    """
    Build a small multi-doc Tantivy index with a known, discriminative
    BM25 ordering. Documents share the term 'distributed' with varying
    frequency so BM25 scores are ordered deterministically:

      d1: 'distributed computing'            (1x distributed)
      d2: 'distributed distributed systems'  (2x distributed)
      d3: 'quantum physics'                  (0x distributed)

    For the query 'distributed' the expected rank order is d2 > d1 > d3
    (d3 only if it matches at all — it does not, so it is excluded).
    """
    index_path.mkdir(parents=True, exist_ok=True)
    sb = tantivy.SchemaBuilder()
    sb.add_text_field("doc_id", stored=True, tokenizer_name="raw")
    sb.add_text_field("body", stored=True, tokenizer_name="en_stem")
    idx = tantivy.Index(sb.build(), path=str(index_path))
    writer = idx.writer(16 * 1024 * 1024)
    writer.add_document(tantivy.Document(doc_id=["d1"], body=["distributed computing"]))
    writer.add_document(tantivy.Document(doc_id=["d2"], body=["distributed distributed systems"]))
    writer.add_document(tantivy.Document(doc_id=["d3"], body=["quantum physics"]))
    writer.commit()
    idx.reload()


def _open_fixture_index(index_path: Path) -> "tantivy.Index":
    return tantivy.Index.open(str(index_path))


def test_sparse_topk_order_discriminative(tmp_path):
    """(a) Discriminative top-k order: BM25 ranks d2 > d1 for 'distributed'."""
    idx_path = tmp_path / "tantivy_index"
    _build_fixture_index(idx_path)
    idx = _open_fixture_index(idx_path)

    results = _bm25_query(idx, "distributed", top_k=10)
    # Only the two docs containing 'distributed' match; d3 (quantum) is excluded.
    assert [r["doc_id"] for r in results] == ["d2", "d1"]
    # Ranks are 1-based and sequential.
    assert [r["rank"] for r in results] == [1, 2]
    # Scores are strictly decreasing in rank order.
    assert results[0]["score"] > results[1]["score"] > 0.0

    # top_k=1 must truncate to the single best doc.
    top1 = _bm25_query(idx, "distributed", top_k=1)
    assert [r["doc_id"] for r in top1] == ["d2"]
    assert top1[0]["rank"] == 1


def test_sparse_topk_honored_across_configs(tmp_path):
    """(b) top_k is honored across two different config values."""
    idx_path = tmp_path / "tantivy_index"
    _build_fixture_index(idx_path)
    idx = _open_fixture_index(idx_path)

    # A query matching all three docs (use a term present in each? none is).
    # Instead use two queries that each match a known count and assert the
    # returned list length never exceeds the requested top_k.
    # 'distributed' matches 2 docs; top_k=1 -> 1, top_k=5 -> 2 (capped by matches).
    assert len(_bm25_query(idx, "distributed", top_k=1)) == 1
    assert len(_bm25_query(idx, "distributed", top_k=5)) == 2

    # 'quantum' matches exactly 1 doc; top_k=10 must still return just 1.
    assert len(_bm25_query(idx, "quantum", top_k=10)) == 1

    # sparse_retrieve returns (doc_ids, elapsed_ms) and respects top_k.
    ids, ms = sparse_retrieve(idx, "distributed", 1, "q1")
    assert ids == ["d2"]
    assert ms >= 0.0
    ids, ms = sparse_retrieve(idx, "distributed", 5, "q1")
    assert ids == ["d2", "d1"]
    assert ms >= 0.0


def test_sparse_empty_whitespace_matches_node_b(tmp_path):
    """(c) Empty / whitespace-only queries match nothing (== Node B behavior)."""
    idx_path = tmp_path / "tantivy_index"
    _build_fixture_index(idx_path)
    idx = _open_fixture_index(idx_path)

    for q in ("", "   ", "  \t "):
        # _bm25_query must not raise and must return an empty list.
        assert _bm25_query(idx, q, top_k=10) == []
        # sparse_retrieve must return an empty id list with a non-negative time.
        ids, ms = sparse_retrieve(idx, q, 10, "q1")
        assert ids == []
        assert ms >= 0.0

    # A real query still returns results (control: the empty case is not
    # just 'always empty').
    assert [r["doc_id"] for r in _bm25_query(idx, "distributed", top_k=10)] == ["d2", "d1"]


def test_sparse_field_names_identical_to_node_b(tmp_path):
    """(d) Result field names are byte-identical to Node B's BM25Retriever.query."""
    idx_path = tmp_path / "tantivy_index"
    _build_fixture_index(idx_path)
    idx = _open_fixture_index(idx_path)

    results = _bm25_query(idx, "distributed", top_k=10)
    assert results, "expected at least one hit"
    # Node B's BM25Retriever.query returns exactly these four keys.
    for r in results:
        assert set(r.keys()) == {"doc_id", "text", "score", "rank"}
        assert isinstance(r["doc_id"], str)
        assert isinstance(r["text"], str)
        assert isinstance(r["score"], float)
        assert isinstance(r["rank"], int)

    # The Lucene-escape helper must be byte-identical to Node B's: an
    # apostrophe (a Lucene special) is escaped, ordinary text is untouched.
    assert _escape_lucene("paula deen's") == "paula deen\\'s"
    assert _escape_lucene("distributed computing") == "distributed computing"
    assert _LUCENE_SPECIALS == set('+-!(){}[]^"~*?:/\\\'')
