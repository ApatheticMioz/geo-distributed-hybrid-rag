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
    create_app,
    load_config,
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
