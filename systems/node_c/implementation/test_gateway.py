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

import socket
import sys
import threading
import time
from pathlib import Path

import pytest
import tantivy
import yaml
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))

import src.gateway as _gw  # noqa: E402

from src.gateway import (  # noqa: E402
    BenchmarkResponse,
    ConfigError,
    QueryRequest,
    _bm25_query,
    _drain_tokens,
    _escape_lucene,
    _LUCENE_SPECIALS,
    _open_final_stream,
    create_app,
    dense_forward,
    load_config,
    sphp_hint_dispatch,
    sparse_retrieve,
    SphpStream,
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

def test_query_route_empty_result_streaming(tmp_path):
    # NC-4: a query matching nothing (sparse empty, dense degrades to []
    # because B is unavailable) must return the B-mirrored empty-result
    # StreamingResponse "[No relevant documents found]" (200, text/plain).
    idx_path = tmp_path / "tantivy_index"
    _build_tiny_index(idx_path)
    app = create_app(_make_config(tmp_path, idx_path))
    saved = _gw._NODE_B_TARGET
    _gw._NODE_B_TARGET = None  # B unavailable -> dense_forward degrades to ([], 0.0)
    try:
        with TestClient(app) as client:
            r = client.post("/query", json={"query": "zzz nonexistent term", "top_k": 3})
    finally:
        _gw._NODE_B_TARGET = saved
    assert r.status_code == 200
    assert r.text == "[No relevant documents found]"


def test_benchmark_route_retrieval_only(tmp_path):
    # NC-4: retrieval_only=true skips generation and returns a
    # BenchmarkResponse with zeroed generation metrics (no gRPC to A).
    idx_path = tmp_path / "tantivy_index"
    _build_tiny_index(idx_path)
    app = create_app(_make_config(tmp_path, idx_path))
    saved = _gw._NODE_B_TARGET
    _gw._NODE_B_TARGET = None  # B unavailable -> dense leg degrades to ([], 0.0)
    try:
        with TestClient(app) as client:
            r = client.post(
                "/query/benchmark",
                json={"query": "distributed computing", "top_k": 3, "retrieval_only": True},
            )
    finally:
        _gw._NODE_B_TARGET = saved
    assert r.status_code == 200
    body = r.json()
    assert body["token_count"] == 0
    assert body["decode_tps"] == 0
    assert body["answer_preview"] == ""
    assert body["timings"]["ttft_ms"] == 0
    assert body["timings"]["decode_ms"] == 0
    # The local sparse leg found d1; dense degrades to [] (B unavailable).
    assert body["sparse_doc_ids"] == ["d1"]
    assert body["dense_doc_ids"] == []
    assert body["fused_doc_ids"] == ["d1"]


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


# ============================================================================
# NC-3 — dense forward (httpx) + SPHP hint dispatch (gRPC)
# ============================================================================

import asyncio  # noqa: E402
import json  # noqa: E402

import grpc  # noqa: E402
import httpx  # noqa: E402

import hybrid_coordination_pb2  # noqa: E402
import hybrid_coordination_pb2_grpc  # noqa: E402


def test_dense_forward_httpx_mocktransport():
    """dense_forward POSTs the retrieval-only dense body to B and parses the response."""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "dense_doc_ids": ["d9", "d8"],
                "timings": {"dense_ms": 42.5},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    ids, ms = dense_forward(
        "distributed computing", 10, "q1",
        target="http://10.8.0.2:8000", client=client,
    )
    assert ids == ["d9", "d8"]
    assert ms == 42.5
    assert captured["method"] == "POST"
    assert captured["url"] == "http://10.8.0.2:8000/query/benchmark"
    assert captured["body"] == {
        "query": "distributed computing",
        "top_k": 10,
        "mode": "dense",
        "retrieval_only": True,
    }


def test_dense_forward_degrades_on_transport_error():
    """A Node B transport failure must degrade to ([], 0.0), not raise."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    ids, ms = dense_forward("q", 5, "q1", target="http://10.8.0.2:8000", client=client)
    assert ids == []
    assert ms == 0.0


def _slow_b_server(delay_s: float) -> tuple[socket.socket, int, threading.Thread]:
    """
    A real TCP server that accepts a connection, sleeps ``delay_s`` (simulating
    a slow-but-healthy Node B dense leg), then returns a valid
    /query/benchmark JSON body. A real socket (not httpx.MockTransport) is
    required: httpx only fires ReadTimeout between network events, and a mock
    handler is a single atomic event that never trips the read timeout.
    """
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.listen(1)

    def _serve():
        conn, _ = srv.accept()
        try:
            # Drain the request headers/body (best-effort; we only need the
            # connection held open so the client's read blocks).
            conn.settimeout(5.0)
            try:
                conn.recv(65536)
            except socket.timeout:
                pass
            time.sleep(delay_s)
            body = (
                b'{"dense_doc_ids": ["d1", "d2", "d3"], '
                b'"timings": {"dense_ms": ' + str(int(delay_s * 1000)).encode() + b'}}'
            )
            resp = (
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: " + str(len(body)).encode() + b"\r\n"
                b"\r\n" + body
            )
            conn.sendall(resp)
        finally:
            conn.close()

    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    return srv, port, t


def test_dense_forward_timeout_is_config_driven():
    """
    The dense-leg read timeout must come from config (retrieval.dense_timeout_ms),
    not httpx's 5 s default. A Node B that is slow-but-healthy (~24 s cold,
    measured) must return real doc ids under the configured timeout, while the
    same delay must degrade to ([], 0.0) under a too-small timeout.

    We use a 1.0 s server delay (well under the 5 s httpx default, so the
    old code would have succeeded) and bracket it with two configured
    timeouts: 0.5 s (below the delay -> ReadTimeout -> degrade) and 3.0 s
    (above the delay -> real ids). This proves the timeout is actually
    config-driven and that a slow B is no longer silently cut off.
    """
    # (1) Configured timeout BELOW the server delay -> ReadTimeout -> ([], 0.0).
    srv, port, t = _slow_b_server(1.0)
    try:
        _gw._DENSE_TIMEOUT_S = 0.5
        ids, ms = dense_forward("q", 5, "q1", target=f"http://127.0.0.1:{port}")
        assert ids == []
        assert ms == 0.0
    finally:
        srv.close()
        t.join(timeout=5)

    # (2) Configured timeout ABOVE the server delay -> real doc ids come back.
    srv, port, t = _slow_b_server(1.0)
    try:
        _gw._DENSE_TIMEOUT_S = 3.0
        ids, ms = dense_forward("q", 5, "q1", target=f"http://127.0.0.1:{port}")
        assert ids == ["d1", "d2", "d3"]
        assert ms == 1000.0
    finally:
        srv.close()
        t.join(timeout=5)


class _FakeOrchestrator(hybrid_coordination_pb2_grpc.GenerationOrchestratorServicer):
    """In-process fake of Node A's GenerationOrchestrator that records requests."""

    def __init__(self):
        self.received = []
        self.hint_received = asyncio.Event()
        self.final_received = asyncio.Event()

    async def GenerateStream(self, request_iterator, context):
        async for req in request_iterator:
            self.received.append(req)
            if req.is_sparse_hint:
                self.hint_received.set()
                continue
            # Final fused message: emit a final token and end the sequence.
            self.final_received.set()
            yield hybrid_coordination_pb2.GenerationToken(
                query_id=req.query_id, token="", is_final=True,
            )
            break


async def _run_sphp_fake_servicer():
    server = grpc.aio.server()
    servicer = _FakeOrchestrator()
    hybrid_coordination_pb2_grpc.add_GenerationOrchestratorServicer_to_server(
        servicer, server
    )
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
    try:
        # 6 sparse ids, hint_depth=5 -> must truncate to the first 5.
        stream = await sphp_hint_dispatch(
            query_id="q1",
            query_text="distributed computing",
            sparse_doc_ids=["d1", "d2", "d3", "d4", "d5", "d6"],
            t_sparse_ms=12.5,
            hint_depth=5,
            target=f"127.0.0.1:{port}",
            channel=channel,
        )
        assert isinstance(stream, SphpStream)
        await asyncio.wait_for(servicer.hint_received.wait(), timeout=5)

        # The hint (message 1) must be captured with the right shape.
        hint = servicer.received[0]
        assert hint.is_sparse_hint is True
        assert hint.query_id == "q1"
        assert hint.query_text == "distributed computing"
        assert hint.t_sparse_ms == 12.5
        # Truncated to hint_depth=5, ranks numbered 1..5, doc order preserved.
        assert [d.doc_id for d in hint.fused_docs] == ["d1", "d2", "d3", "d4", "d5"]
        assert [d.rank for d in hint.fused_docs] == [1, 2, 3, 4, 5]
        assert all(d.rrf_score == 0.0 for d in hint.fused_docs)

        # The SAME handle accepts a second send (message 2) on the same stream.
        stream.send_final(
            query_id="q1",
            query_text="distributed computing",
            fused_doc_ids=["d2", "d1"],
            t_sparse_ms=12.5,
            t_dense_ms=30.0,
            t_fusion_ms=5.0,
        )
        await asyncio.wait_for(servicer.final_received.wait(), timeout=5)

        final = servicer.received[1]
        assert final.is_sparse_hint is False
        assert [d.doc_id for d in final.fused_docs] == ["d2", "d1"]
        assert [d.rank for d in final.fused_docs] == [1, 2]
        assert final.t_dense_ms == 30.0
        assert final.t_fusion_ms == 5.0
        # Both messages landed on the same stream, in order.
        assert len(servicer.received) == 2
    finally:
        await channel.close()
        await server.stop(0)


def test_sphp_hint_dispatch_fake_servicer():
    """SPHP hint: is_sparse_hint, doc order + rank numbering, hint_depth truncation,
    t_sparse_ms/query_id passthrough, and a second send on the same stream."""
    asyncio.run(_run_sphp_fake_servicer())


def test_config_rejects_hint_depth_not_5(tmp_path):
    # Fault injection: Node A's SPHP reconciliation hardcodes top-5 / 0.5
    # overlap (node_a/src/main.py:336-345), so hint_depth != 5 must be rejected.
    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    raw["retrieval"]["hint_depth"] = 3
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ConfigError, match="hint_depth"):
        load_config(p)


def test_config_accepts_hint_depth_5(tmp_path):
    # The real config (hint_depth=5) is the contract and must be accepted.
    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    raw["retrieval"]["hint_depth"] = 5
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(raw), encoding="utf-8")
    cfg = load_config(p)
    assert cfg["retrieval"]["hint_depth"] == 5


# ============================================================================
# NC-4 — fused reconcile (RRF) + full query orchestration
# ============================================================================

# Node B's RRF implementation (source of truth for byte-identity).
_B_IMPL_DIR = Path(__file__).resolve().parent.parent.parent / "node_b" / "implementation" / "src"
sys.path.insert(0, str(_B_IMPL_DIR))
from fusion import reciprocal_rank_fusion as _b_rrf  # noqa: E402

from src.gateway import reciprocal_rank_fusion as _c_rrf  # noqa: E402


def test_rrf_identity_vs_node_b():
    """C's RRF must be byte-identical to Node B's for a range of inputs."""
    cases = [
        (["a", "b", "c"], ["b", "c", "d"], 60),
        (["a", "b"], None, 60),
        ([], ["x", "y"], 60),
        (["a", "b", "c"], [], 30),
        (["a", "b", "c", "d", "e"], ["e", "d", "c", "b", "a"], 60),
        (["solo"], None, 100),
    ]
    for sparse, dense, k in cases:
        assert _c_rrf(sparse, dense, k) == _b_rrf(sparse, dense, k), (sparse, dense, k)


def test_mode_matrix_retrieval_only(tmp_path):
    """Mode matrix: sparse/dense/hybrid each run the correct leg(s) and
    produce the correct final/fused lists (retrieval_only, no generation)."""
    idx_path = tmp_path / "tantivy_index"
    _build_tiny_index(idx_path)  # d1: 'distributed computing'
    app = create_app(_make_config(tmp_path, idx_path))
    saved = _gw._NODE_B_TARGET
    _gw._NODE_B_TARGET = None  # B unavailable -> dense leg degrades to ([], 0.0)
    try:
        with TestClient(app) as client:
            # sparse: local leg only; dense skipped (0.0 ms, []).
            r = client.post("/query/benchmark", json={"query": "distributed", "mode": "sparse", "retrieval_only": True})
            b = r.json()
            assert b["sparse_doc_ids"] == ["d1"]
            assert b["dense_doc_ids"] == []
            assert b["fused_doc_ids"] == []  # no RRF in single-leg mode
            assert b["timings"]["dense_ms"] == 0

            # dense: forward leg only (B unavailable -> []); sparse skipped.
            r = client.post("/query/benchmark", json={"query": "distributed", "mode": "dense", "retrieval_only": True})
            b = r.json()
            assert b["sparse_doc_ids"] == []
            assert b["dense_doc_ids"] == []
            assert b["fused_doc_ids"] == []
            assert b["timings"]["sparse_ms"] == 0

            # hybrid: both legs; fused = RRF(sparse, dense). dense empty -> fused == sparse.
            r = client.post("/query/benchmark", json={"query": "distributed", "mode": "hybrid", "retrieval_only": True})
            b = r.json()
            assert b["sparse_doc_ids"] == ["d1"]
            assert b["fused_doc_ids"] == ["d1"]  # RRF with empty dense == sparse
            assert b["timings"]["fusion_ms"] >= 0
    finally:
        _gw._NODE_B_TARGET = saved


class _FakeOrchestratorFull(hybrid_coordination_pb2_grpc.GenerationOrchestratorServicer):
    """Fake Node A that records requests and streams tokens back, reporting
    SPHP reconciliation fields on the final token (overlap / wasted_prefill)."""

    def __init__(self, overlap=0.0, wasted=0.0, hit=False):
        self.received = []
        self.hint_received = asyncio.Event()
        self.final_received = asyncio.Event()
        self.overlap = overlap
        self.wasted = wasted
        self.hit = hit

    async def GenerateStream(self, request_iterator, context):
        async for req in request_iterator:
            self.received.append(req)
            if req.is_sparse_hint:
                self.hint_received.set()
                continue
            self.final_received.set()
            # Stream a couple of answer tokens, then a final sentinel carrying
            # the SPHP reconciliation fields.
            for tok in ("Hel", "lo"):
                yield hybrid_coordination_pb2.GenerationToken(
                    query_id=req.query_id, token=tok, is_final=False,
                )
            yield hybrid_coordination_pb2.GenerationToken(
                query_id=req.query_id, token="", is_final=True,
                sphp_hit=self.hit, sphp_overlap=self.overlap,
                wasted_prefill_ms=self.wasted,
            )
            break


async def _run_full_sphp_flow(overlap, wasted, hit):
    server = grpc.aio.server()
    servicer = _FakeOrchestratorFull(overlap=overlap, wasted=wasted, hit=hit)
    hybrid_coordination_pb2_grpc.add_GenerationOrchestratorServicer_to_server(servicer, server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
    try:
        stream = await sphp_hint_dispatch(
            query_id="q1",
            query_text="distributed computing",
            sparse_doc_ids=["d1", "d2", "d3", "d4", "d5", "d6"],
            t_sparse_ms=12.5,
            hint_depth=5,
            target=f"127.0.0.1:{port}",
            channel=channel,
        )
        # NC-4: send the final fused context on the SAME stream.
        stream.send_final(
            query_id="q1",
            query_text="distributed computing",
            fused_doc_ids=["d2", "d1"],
            t_sparse_ms=12.5,
            t_dense_ms=30.0,
            t_fusion_ms=5.0,
        )
        tokens, meta, ttft_ms = await _drain_tokens(stream, "q1", time.perf_counter())
        return servicer, tokens, meta, ttft_ms
    finally:
        await channel.close()
        await server.stop(0)


def test_full_sphp_flow_token_drain_and_overlap_passthrough():
    """Full SPHP flow: hint + final on the same stream, token drain, and
    overlap / wasted_prefill / sphp_hit passthrough into meta."""
    async def _go():
        servicer, tokens, meta, ttft_ms = await _run_full_sphp_flow(
            overlap=0.6, wasted=0.0, hit=True,
        )
        # Both messages on the same stream, in order.
        assert [r.is_sparse_hint for r in servicer.received] == [True, False]
        # Tokens drained (non-final only).
        assert tokens == ["Hel", "lo"]
        # SPHP reconciliation fields passed through from the final token.
        assert meta["sphp_hit"] is True
        assert meta["sphp_overlap"] == 0.6
        assert "wasted_prefill_ms" not in meta  # 0.0 -> not recorded
        assert ttft_ms >= 0.0

    asyncio.run(_go())


def test_full_sphp_flow_wasted_prefill_passthrough():
    """SPHP miss: wasted_prefill_ms > 0 must be recorded in meta."""
    async def _go():
        servicer, tokens, meta, ttft_ms = await _run_full_sphp_flow(
            overlap=0.2, wasted=42.0, hit=False,
        )
        assert meta["sphp_hit"] is False
        assert meta["sphp_overlap"] == 0.2
        assert meta["wasted_prefill_ms"] == 42.0

    asyncio.run(_go())


def test_non_sphp_single_message_path():
    """Non-SPHP: a fresh stream carries exactly ONE message (is_sparse_hint=False)."""
    async def _go():
        server = grpc.aio.server()
        servicer = _FakeOrchestratorFull(overlap=0.0, wasted=0.0, hit=False)
        hybrid_coordination_pb2_grpc.add_GenerationOrchestratorServicer_to_server(servicer, server)
        port = server.add_insecure_port("127.0.0.1:0")
        await server.start()
        channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
        try:
            stream = await _open_final_stream(
                query_id="q1",
                query_text="distributed computing",
                fused_doc_ids=["d1", "d2"],
                t_sparse_ms=10.0,
                t_dense_ms=20.0,
                t_fusion_ms=3.0,
                target=f"127.0.0.1:{port}",
                channel=channel,
            )
            await asyncio.wait_for(servicer.final_received.wait(), timeout=5)
            # Exactly one message, and it is the final (non-hint) context.
            assert len(servicer.received) == 1
            assert servicer.received[0].is_sparse_hint is False
            assert [d.doc_id for d in servicer.received[0].fused_docs] == ["d1", "d2"]
            assert servicer.received[0].t_dense_ms == 20.0
            assert servicer.received[0].t_fusion_ms == 3.0
        finally:
            await channel.close()
            await server.stop(0)

    asyncio.run(_go())
