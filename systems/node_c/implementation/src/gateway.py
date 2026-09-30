"""
Node C — Sparse Retrieval + User-Facing Gateway (3-Node Topology).

Role (per systems/node_c/config.yaml, decided 2026-09-18):
  * serves the client directly (user-facing FastAPI gateway), and
  * hosts the Tantivy BM25 sparse index, so the sparse leg — and
    therefore the SPHP sparse hint — originates one hop closer to the
    user. The dense leg + RRF fusion stay on Node B (10.8.0.2:8000);
    generation stays on Node A behind Node B's existing P2 path.

This module is the NC-1 skeleton:
  * config loading from systems/node_c/config.yaml,
  * /health readiness gating on the Tantivy index (reports ok ONLY
    after the index loads — same readiness semantics as Node B),
  * /query and /query/benchmark route contracts with request/response
    models field-compatible with Node B's server.py schemas, so the
    campaign harness stays gateway-agnostic.

The four pipeline legs are separate stub functions raising
NotImplementedError with their contracts documented — they are the
NC-2..NC-4 targets and are intentionally NOT implemented here.
"""

import asyncio
import json
import logging
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List

import grpc
import httpx
import tantivy
import yaml
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

# Node A's generation contract (hybrid_coordination.proto) is copied UNEDITED
# into systems/node_c/implementation/proto/ and compiled to the generated/
# package. We import the generated pb2 modules directly (committed, not
# regenerated at import time) so the gateway has no build-time dependency on
# grpcio-tools.
_generated_dir = Path(__file__).resolve().parent.parent / "generated"
if str(_generated_dir) not in sys.path:
    sys.path.insert(0, str(_generated_dir))
import hybrid_coordination_pb2  # noqa: E402
import hybrid_coordination_pb2_grpc  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ============================================================================
# Configuration
# ============================================================================

# config.yaml lives one level above implementation/ (systems/node_c/config.yaml)
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config.yaml"

RRF_K = int(os.environ.get("RRF_K", "60"))

# Top-level keys the gateway contract requires (see config.yaml header).
REQUIRED_TOP_LEVEL_KEYS = (
    "wireguard_ip", "node_b", "node_a", "corpus", "retrieval", "gateway",
)
REQUIRED_SECTION_KEYS = {
    "node_b": ("host", "port"),
    "node_a": ("host", "grpc_port"),
    "corpus": ("tantivy_index_path", "raw_corpus_path"),
    "retrieval": ("top_k", "hint_depth"),
    "gateway": ("host", "port"),
}


class ConfigError(ValueError):
    """Raised when config.yaml is missing, malformed, or incomplete."""


def _validate_config(raw: Any, source: str) -> dict:
    if not isinstance(raw, dict):
        raise ConfigError(
            f"{source}: top level must be a mapping, got {type(raw).__name__}"
        )
    missing = [k for k in REQUIRED_TOP_LEVEL_KEYS if k not in raw]
    if missing:
        raise ConfigError(f"{source}: missing top-level key(s): {', '.join(missing)}")
    for section, keys in REQUIRED_SECTION_KEYS.items():
        sec = raw[section]
        if not isinstance(sec, dict):
            raise ConfigError(f"{source}: section '{section}' must be a mapping")
        missing_sub = [k for k in keys if k not in sec]
        if missing_sub:
            raise ConfigError(
                f"{source}: section '{section}' missing key(s): {', '.join(missing_sub)}"
            )
    # SPHP contract: Node A's GenerateStream reconciliation hardcodes the
    # provisional/final top-5 and the overlap threshold (len(intersection)/5.0
    # >= 0.5) — see systems/node_a/implementation/src/main.py:336-345. C's
    # hint_depth must therefore be exactly 5; any other value silently
    # desyncs the overlap denominator and the hit threshold, so reject it.
    if int(raw["retrieval"]["hint_depth"]) != 5:
        raise ConfigError(
            f"{source}: retrieval.hint_depth must be 5 (Node A's SPHP "
            f"reconciliation hardcodes top-5 / 0.5 overlap); got "
            f"{raw['retrieval']['hint_depth']}"
        )
    return raw


def load_config(path: str | Path) -> dict:
    """
    Load and validate the Node C config.yaml.

    Relative paths (corpus.*) are resolved against the config file's
    directory so the gateway works regardless of CWD.
    """
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    cfg = _validate_config(raw, str(path))
    base = path.resolve().parent
    for key in ("tantivy_index_path", "raw_corpus_path"):
        p = cfg["corpus"][key]
        if not Path(p).is_absolute():
            cfg["corpus"][key] = str(base / p)
    return cfg


# ============================================================================
# Pipeline legs — NC-2..NC-4 stubs (contracts only, not implemented yet)
# ============================================================================

# Lucene/Tantivy special characters that must be escaped in a query string
# before it is handed to tantivy's parse_query. An unescaped special (most
# notably the apostrophe, e.g. "paula deen's") makes parse_query raise and the
# /query endpoint return a 500. Escaping each as a literal keeps the query
# parseable while preserving the intended term.
# (Byte-identical to Node B's bm25_retriever._LUCENE_SPECIALS.)
_LUCENE_SPECIALS = set('+-!(){}[]^"~*?:/\\\'')


def _escape_lucene(query_text: str) -> str:
    """
    Escape Lucene special characters so parse_query treats them as literals.

    Each character in ``_LUCENE_SPECIALS`` is prefixed with a backslash, which
    is Lucene's escape character. Normal (non-special) characters pass through
    unchanged, so ordinary queries are unaffected.
    (Byte-identical to Node B's bm25_retriever._escape_lucene.)
    """
    return ''.join('\\' + ch if ch in _LUCENE_SPECIALS else ch for ch in query_text)


def _bm25_query(index: "tantivy.Index", query_text: str, top_k: int = 10) -> List[Dict[str, Any]]:
    """
    Node C's mirror of Node B's BM25Retriever.query.

    Runs a BM25 search against the (lifespan-loaded) Tantivy index and returns
    at most ``top_k`` results. The result dicts carry byte-identical field
    names to Node B's BM25Retriever.query: ``doc_id``, ``text``, ``score``,
    ``rank``. Empty / whitespace-only queries match nothing and yield ``[]``
    (same as Node B — parse_query on an empty string produces a no-match query).
    """
    searcher = index.searcher()
    query = index.parse_query(
        _escape_lucene(query_text), default_field_names=["body"]
    )
    search_result = searcher.search(query, top_k)
    results_list = search_result.hits if hasattr(search_result, "hits") else search_result

    results = []
    for rank, result in enumerate(results_list, start=1):
        score, doc_address = result
        doc = searcher.doc(doc_address)
        results.append({
            "doc_id": doc.get_first("doc_id"),
            "text": doc.get_first("body"),
            "score": float(score),
            "rank": rank,
        })
    return results


def sparse_retrieve(index: "tantivy.Index", query: str, top_k: int, query_id: str) -> tuple[list[str], float]:
    """
    NC-2 — Local Tantivy BM25 sparse leg (the index lives on C).

    Contract:
      * Runs against the disk-backed Tantivy index at
        config['corpus']['tantivy_index_path'] (loaded at startup and reused
        here — the index is opened once in lifespan, not per request).
      * Returns (doc_id_list, elapsed_ms) with at most top_k ids,
        ranked by BM25 score — the same return shape as Node B's
        _sparse_retrieve so the campaign harness is gateway-agnostic.
      * Must be safe to run in a worker thread (asyncio.to_thread).
    """
    if index is None:
        logger.warning("[%s] BM25 index not available; skipping sparse retrieval", query_id)
        return [], 0.0

    started = time.perf_counter()
    results = _bm25_query(index, query, top_k)

    doc_ids = [r["doc_id"] for r in results if r.get("doc_id")]
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    logger.info(
        "[%s] Sparse retrieval: %.1f ms, %d docs",
        query_id, elapsed_ms, len(doc_ids),
    )
    return doc_ids, elapsed_ms


# Module-level mesh targets, populated by create_app() from config so the
# leg functions can resolve their upstream address without the route bodies
# having to thread it through (keeps the /query and /query/benchmark route
# call sites byte-identical to the NC-1 skeleton).
_NODE_B_TARGET: str | None = None
_NODE_A_TARGET: str | None = None


def dense_forward(
    query: str,
    top_k: int,
    query_id: str,
    target: str | None = None,
    client: "httpx.Client | None" = None,
) -> tuple[list[str], float]:
    """
    NC-3 — Dense-leg forward to Node B's FastAPI gateway.

    Contract:
      * POSTs to Node B's /query/benchmark with
        {query, top_k, mode:'dense', retrieval_only:true} — the
        retrieval-only fast path that returns dense_doc_ids +
        timings.dense_ms WITHOUT triggering Node A generation (Node B's
        /query has no retrieval-only flag and always generates).
      * Returns (dense_doc_ids, timings.dense_ms) in Node B's
        _dense_retrieve shape so the campaign harness is gateway-agnostic.
      * Node B's dense leg (BGE-M3 + Qdrant) stays on B; C only forwards
        and times the round trip.
      * Must be safe to run in a worker thread (asyncio.to_thread); uses a
        synchronous httpx.Client. Timeouts use httpx defaults (no explicit
        timeout) unless a caller injects a preconfigured client.
    """
    if target is None:
        target = _NODE_B_TARGET
    if target is None:
        logger.warning("[%s] Node B target not configured; skipping dense forward", query_id)
        return [], 0.0

    body = {
        "query": query,
        "top_k": top_k,
        "mode": "dense",
        "retrieval_only": True,
    }
    owns_client = client is None
    if client is None:
        # httpx default timeouts (connect/read/write/pool) — no explicit
        # timeout is configured in config.yaml, so we rely on the defaults.
        client = httpx.Client()
    try:
        resp = client.post(f"{target}/query/benchmark", json=body)
        resp.raise_for_status()
        data = resp.json()
        dense_ids = list(data.get("dense_doc_ids", []))
        dense_ms = float(data.get("timings", {}).get("dense_ms", 0.0))
        logger.info(
            "[%s] Dense forward: %.1f ms, %d docs",
            query_id, dense_ms, len(dense_ids),
        )
        return dense_ids, dense_ms
    except httpx.HTTPError as exc:
        # Degrade gracefully on a Node B transport/HTTP failure (mirrors
        # Node B's _sparse_retrieve returning ([], 0.0) when its retriever
        # is unavailable): a B outage must not 500 the whole query — the
        # local sparse leg still serves the request.
        logger.warning(
            "[%s] Dense forward to Node B failed (%s); returning empty dense leg",
            query_id, exc,
        )
        return [], 0.0
    finally:
        if owns_client:
            client.close()


class SphpStream:
    """
    Bidirectional gRPC stream to Node A's GenerationOrchestrator.GenerateStream.

    Mirrors Node B's stream_to_node_a: a request_queue feeds the request
    iterator, and the caller pushes HybridContextRequest messages onto the
    queue. Node A reconciles ONE request sequence per stream (sparse hint
    first, then the final fused context) and then breaks — so the SPHP hint
    (NC-3) and the final fused dispatch (NC-4) MUST share this same stream
    handle.

    The gRPC channel is injectable (``channel``) so tests can point the stream
    at an in-process fake servicer instead of the real Node A.
    """

    def __init__(self, target: str, channel: "grpc.aio.Channel | None" = None):
        self._target = target
        self._channel = channel
        self._owns_channel = channel is None
        self._stub = None
        self._response_iter = None
        self._request_queue: asyncio.Queue = asyncio.Queue()

    async def _open(self) -> None:
        if self._channel is None:
            self._channel = grpc.aio.insecure_channel(self._target)
        self._stub = hybrid_coordination_pb2_grpc.GenerationOrchestratorStub(self._channel)
        # Start the bidi RPC; the request iterator is driven by _request_queue.
        self._response_iter = self._stub.GenerateStream(self._request_iterator())

    def _request_iterator(self):
        async def _gen():
            while True:
                req = await self._request_queue.get()
                if req is None:
                    break
                yield req
        return _gen()

    @staticmethod
    def _build_hint(
        query_id: str,
        query_text: str,
        sparse_doc_ids: list[str],
        t_sparse_ms: float,
        hint_depth: int,
    ) -> "hybrid_coordination_pb2.HybridContextRequest":
        # Truncate to hint_depth and number ranks 1..hint_depth (mirrors
        # Node B's stream_to_node_a SPHP step 1).
        docs = [
            hybrid_coordination_pb2.FusedDocument(
                doc_id=doc_id, rrf_score=0.0, rank=rank,
            )
            for rank, doc_id in enumerate(sparse_doc_ids[:hint_depth], start=1)
        ]
        return hybrid_coordination_pb2.HybridContextRequest(
            query_id=query_id,
            query_text=query_text,
            fused_docs=docs,
            t_sparse_ms=t_sparse_ms,
            is_sparse_hint=True,
        )

    def send_hint(
        self,
        query_id: str,
        query_text: str,
        sparse_doc_ids: list[str],
        t_sparse_ms: float,
        hint_depth: int,
    ) -> "hybrid_coordination_pb2.HybridContextRequest":
        """Push the SPHP sparse hint (message 1) onto the stream."""
        req = self._build_hint(query_id, query_text, sparse_doc_ids, t_sparse_ms, hint_depth)
        self._request_queue.put_nowait(req)
        return req

    def send_final(
        self,
        query_id: str,
        query_text: str,
        fused_doc_ids: list[str],
        t_sparse_ms: float,
        t_dense_ms: float,
        t_fusion_ms: float,
    ) -> "hybrid_coordination_pb2.HybridContextRequest":
        """
        Push the final fused context (message 2) onto the SAME stream, then
        close the request side (None sentinel). NC-4 calls this after RRF.
        """
        docs = [
            hybrid_coordination_pb2.FusedDocument(
                doc_id=doc_id, rrf_score=0.0, rank=rank,
            )
            for rank, doc_id in enumerate(fused_doc_ids, start=1)
        ]
        req = hybrid_coordination_pb2.HybridContextRequest(
            query_id=query_id,
            query_text=query_text,
            fused_docs=docs,
            t_sparse_ms=t_sparse_ms,
            t_dense_ms=t_dense_ms,
            t_fusion_ms=t_fusion_ms,
            is_sparse_hint=False,
        )
        self._request_queue.put_nowait(req)
        self._request_queue.put_nowait(None)  # sentinel: end the request stream
        return req

    async def close(self) -> None:
        if self._owns_channel and self._channel is not None:
            await self._channel.close()
            self._channel = None


async def sphp_hint_dispatch(
    query_id: str,
    query_text: str,
    sparse_doc_ids: list[str],
    t_sparse_ms: float,
    hint_depth: int,
    target: str | None = None,
    channel: "grpc.aio.Channel | None" = None,
) -> SphpStream:
    """
    NC-3 — SPHP sparse-hint dispatch (the hint originates at C).

    Contract:
      * Opens a bidirectional gRPC stream to Node A's
        GenerationOrchestrator.GenerateStream (node_a.grpc_port) and sends
        the SPHP sparse hint as message 1: the top ``hint_depth`` sparse ids
        (ranked 1..hint_depth) with is_sparse_hint=True — mirroring Node B's
        stream_to_node_a SPHP step 1, but originating one hop closer to the
        user.
      * RETURNS the open SphpStream handle so NC-4 can send the final fused
        context (message 2) on the SAME stream. Node A's GenerateStream
        reconciles one request sequence per stream and then breaks, so the
        hint and the final dispatch must share this handle.
    """
    if target is None:
        target = _NODE_A_TARGET
    if target is None:
        logger.warning("[%s] Node A target not configured; skipping SPHP hint", query_id)
        return SphpStream("", channel=channel)

    stream = SphpStream(target, channel=channel)
    await stream._open()
    stream.send_hint(query_id, query_text, sparse_doc_ids, t_sparse_ms, hint_depth)
    logger.info(
        "[%s] SPHP: dispatched early sparse hint (%d docs) to Node A",
        query_id, min(len(sparse_doc_ids), hint_depth),
    )
    return stream


def reciprocal_rank_fusion(
    sparse_doc_ids: list[str],
    dense_doc_ids: list[str] | None,
    k: int = 60,
) -> list[str]:
    """
    Fuse two ranked lists of doc_ids using Reciprocal Rank Fusion (RRF).

    Byte-identical to Node B's fusion.reciprocal_rank_fusion so the fused
    ranking is gateway-agnostic for the campaign harness.

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


def fused_reconcile(sparse_ids: list[str], dense_ids: list[str], rrf_k: int) -> list[str]:
    """
    NC-4 — Fused reconciliation (RRF) of the two legs.

    Contract:
      * Fuses the local sparse ranking with the dense ranking forwarded
        from Node B using reciprocal rank fusion (k = rrf_k, default 60),
        producing the final ranking sent to Node A — the 3-node analogue
        of Node B's local RRF.
      * Returns the fused doc-id list in rank order.
    """
    return reciprocal_rank_fusion(sparse_ids, dense_ids, rrf_k)


# ============================================================================
# Telemetry + token drain (mirror Node B's /query/benchmark bookkeeping)
# ============================================================================

# Telemetry is written under data/telemetry/ (gitignored via the root
# .gitignore `*.jsonl` rule — verified with `git check-ignore`). Node B logs
# to benchmarks/telemetry.jsonl; C mirrors the same JSONL record shape.
TELEMETRY_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "telemetry"


def _write_telemetry(record: dict) -> None:
    """Append a single JSONL telemetry record (best-effort; never raises)."""
    try:
        TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)
        with open(TELEMETRY_DIR / "telemetry.jsonl", "a", encoding="utf-8") as f_log:
            f_log.write(json.dumps(record) + "\n")
    except OSError as exc:
        logger.warning("Failed to write telemetry record: %s", exc)


async def _drain_tokens(
    stream: SphpStream,
    query_id: str,
    t0: float,
) -> tuple[list[str], dict[str, Any], float]:
    """
    Consume Node A's GenerationToken stream until the is_final sentinel.

    Returns (tokens, meta, ttft_ms) where:
      * tokens is the list of non-final token strings (the answer),
      * meta carries the SPHP reconciliation fields (sphp_hit /
        sphp_overlap / wasted_prefill_ms) as reported by Node A on the
        token messages — mirroring Node B's stream_to_node_a meta
        bookkeeping,
      * ttft_ms is the time from t0 to the first token arrival at C.
    """
    tokens: list[str] = []
    meta: dict[str, Any] = {}
    ttft_ms = 0.0
    try:
        async for token in stream._response_iter:
            if token.sphp_hit:
                meta["sphp_hit"] = True
                meta["sphp_overlap"] = round(token.sphp_overlap, 2)
            elif "sphp_hit" not in meta and token.sphp_overlap > 0:
                meta["sphp_hit"] = False
                meta["sphp_overlap"] = round(token.sphp_overlap, 2)
            if token.wasted_prefill_ms > 0:
                meta["wasted_prefill_ms"] = round(token.wasted_prefill_ms, 2)
            if token.is_final:
                break
            if token.token:
                if ttft_ms == 0.0:
                    ttft_ms = (time.perf_counter() - t0) * 1000.0
                tokens.append(token.token)
    except grpc.aio.AioRpcError as exc:
        logger.error("[%s] Node A gRPC stream failed: %s - %s", query_id, exc.code(), exc.details())
        tokens.append(f"[ERROR] Node A stream failed: {exc.code()} - {exc.details()}")
    return tokens, meta, ttft_ms


async def _open_final_stream(
    query_id: str,
    query_text: str,
    fused_doc_ids: list[str],
    t_sparse_ms: float,
    t_dense_ms: float,
    t_fusion_ms: float,
    target: str | None = None,
    channel: "grpc.aio.Channel | None" = None,
) -> SphpStream:
    """
    Non-SPHP path: open a fresh stream to Node A and send a single final
    fused-context message (is_sparse_hint=False) — the 3-node analogue of
    Node B's non-SPHP stream_to_node_a (one message, no hint).
    """
    if target is None:
        target = _NODE_A_TARGET
    stream = SphpStream(target, channel=channel)
    await stream._open()
    stream.send_final(query_id, query_text, fused_doc_ids, t_sparse_ms, t_dense_ms, t_fusion_ms)
    return stream


# ============================================================================
# Request / response models — field-compatible with Node B's server.py
# ============================================================================

class QueryRequest(BaseModel):
    query: str
    top_k: int = 10
    # Retrieval mode: 'hybrid' (both legs + RRF), 'sparse' (BM25 only),
    # 'dense' (BGE-M3 only). Default 'hybrid' preserves prior behavior.
    mode: str = "hybrid"
    # RRF constant k, wired into fused_reconcile. Default 60.
    rrf_k: int = RRF_K
    # Retrieval-only fast path: when true, /query/benchmark skips the
    # generation leg entirely and returns per-mode rankings + retrieval
    # timings with zeroed generation metrics. Default false.
    retrieval_only: bool = False
    # SPHP (Speculative Progressive Hydration & Prefill): dispatch sparse
    # hint early while dense leg runs in background. Default false.
    sphp: bool = False


class BenchmarkResponse(BaseModel):
    query_id: str
    query: str
    top_k: int
    mode: str = "hybrid"
    rrf_k: int = RRF_K
    timings: dict[str, Any]
    fused_doc_ids: list[str]
    sparse_doc_ids: list[str] = []
    dense_doc_ids: list[str] = []
    token_count: int
    decode_tps: float
    answer_preview: str
    # Full generated answer (untruncated) for offline answer-level
    # evaluation; answer_preview stays capped at 120 chars for logs/UI.
    answer_full: str = ""
    sphp: bool = False
    sphp_hit: bool | None = None
    sphp_overlap: float | None = None
    wasted_prefill_ms: float | None = None


# ============================================================================
# App factory
# ============================================================================

def _resolve_mode_and_k(req: "QueryRequest") -> tuple[str, int]:
    """
    Normalize the requested retrieval mode and RRF constant (mirrors
    Node B's _resolve_mode_and_k). Invalid mode raises HTTPException(400);
    rrf_k falls back to the module-level RRF_K when unset or non-positive.
    """
    mode = (req.mode or "hybrid").strip().lower()
    if mode not in ("hybrid", "sparse", "dense"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid mode '{req.mode}'. Must be one of: hybrid, sparse, dense.",
        )
    rrf_k = req.rrf_k if (req.rrf_k is not None and req.rrf_k > 0) else RRF_K
    return mode, rrf_k


def load_tantivy_index(index_path: str) -> "tantivy.Index":
    """
    Open the disk-backed Tantivy index and force a searcher load so
    readiness is only reported after the index is actually usable.
    Raises FileNotFoundError if the index directory does not exist.
    """
    p = Path(index_path)
    if not p.is_dir():
        raise FileNotFoundError(f"Tantivy index not found at {p}")
    idx = tantivy.Index.open(str(p))
    _ = idx.searcher()
    return idx


def create_app(config: dict) -> FastAPI:
    """
    Build the Node C FastAPI app from a validated config dict.

    Readiness semantics (mirrors Node B): /health reports
    {"status":"ok","node":"C"} ONLY after the Tantivy index loads at
    startup; until then it returns 503 {"status":"not_ready","node":"C"}.
    """
    _validate_config(config, "config")

    # Publish the mesh targets to the module-level handles so the leg
    # functions (dense_forward / sphp_hint_dispatch) can resolve their
    # upstream address without the route bodies threading it through.
    global _NODE_B_TARGET, _NODE_A_TARGET
    _NODE_B_TARGET = f"{config['node_b']['host']}:{config['node_b']['port']}"
    _NODE_A_TARGET = f"{config['node_a']['host']}:{config['node_a']['grpc_port']}"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("Starting Node C (sparse_user_facing)...")
        app.state.index = None
        app.state.ready = False
        try:
            app.state.index = load_tantivy_index(config["corpus"]["tantivy_index_path"])
            app.state.ready = True
            logger.info("Tantivy index loaded; gateway is ready.")
        # tantivy-py 0.26 raises builtins.ValueError (not a dedicated
        # TantivyError) when Index.open fails; FileNotFoundError covers
        # our own missing-dir check, OSError covers I/O failures.
        except (FileNotFoundError, OSError, ValueError) as exc:
            logger.warning(
                "Tantivy index unavailable (%s). Gateway stays NOT READY "
                "until the index is built (build_index.py).",
                exc,
            )
        yield
        logger.info("Shutting down Node C...")

    app = FastAPI(
        title="Node C - Sparse Retrieval + User-Facing Gateway",
        version="1.0.0",
        lifespan=lifespan,
    )

    @app.exception_handler(NotImplementedError)
    async def _not_implemented_handler(request, exc: NotImplementedError):
        # Pipeline legs are NC-2..NC-4 stubs; surface them as 501 so the
        # campaign harness can distinguish "not built yet" from 4xx/5xx.
        return JSONResponse(status_code=501, content={"detail": str(exc)})

    @app.get("/health")
    async def health():
        if not app.state.ready:
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "node": "C"},
            )
        return {"status": "ok", "node": "C"}

    @app.post("/query")
    async def query_endpoint(
        req: QueryRequest,
        simulate_wan_delay_ms: int | None = Header(default=None, alias="X-Simulate-WAN-Delay"),
    ):
        """
        Main query endpoint (contract mirrors Node B's /query).
        Executes mode-aware retrieval (local sparse + dense_forward), fuses
        with RRF, streams the fused context to Node A, and returns the
        generated tokens to the client as text/plain with B's X- headers.
        """
        query_id = str(uuid.uuid4())[:8]
        k = req.top_k or int(config["retrieval"]["top_k"])
        query_text = req.query.strip()
        mode, rrf_k = _resolve_mode_and_k(req)
        delay_seconds = max(simulate_wan_delay_ms or 0, 0) / 1000.0

        t0 = time.perf_counter()
        logger.info(
            "[%s] Pipeline start | mode=%s rrf_k=%d sphp=%s query='%s'",
            query_id, mode, rrf_k, req.sphp, query_text[:60],
        )

        if delay_seconds > 0:
            logger.info("[%s] Simulating WAN delay: %.0f ms", query_id, delay_seconds * 1000)
            await asyncio.sleep(delay_seconds)

        # Mode-aware retrieval (mirrors Node B's _hybrid_retrieve):
        #   hybrid -> both legs; sparse -> local only; dense -> forward only.
        run_dense = mode in ("hybrid", "dense")
        run_sparse = mode in ("hybrid", "sparse")

        dense_future = (
            asyncio.to_thread(dense_forward, query_text, k, query_id)
            if run_dense else None
        )
        sparse_future = (
            asyncio.to_thread(sparse_retrieve, app.state.index, query_text, k, query_id)
            if run_sparse else None
        )

        if dense_future is not None and sparse_future is not None:
            (dense_ids, t_dense_ms), (sparse_ids, t_sparse_ms) = await asyncio.gather(
                dense_future, sparse_future,
            )
        elif dense_future is not None:
            dense_ids, t_dense_ms = await dense_future
            sparse_ids, t_sparse_ms = [], 0.0
        elif sparse_future is not None:
            sparse_ids, t_sparse_ms = await sparse_future
            dense_ids, t_dense_ms = [], 0.0
        else:
            dense_ids, t_dense_ms = [], 0.0
            sparse_ids, t_sparse_ms = [], 0.0

        # RRF fusion (only meaningful when both legs ran).
        if mode == "hybrid":
            fusion_start = time.perf_counter()
            fused_ids = await asyncio.to_thread(
                fused_reconcile, sparse_ids, dense_ids, rrf_k
            )
            final_ids = fused_ids
            t_fusion_ms = (time.perf_counter() - fusion_start) * 1000.0
        else:
            fused_ids = []
            final_ids = list(sparse_ids) if mode == "sparse" else list(dense_ids)
            t_fusion_ms = 0.0

        logger.info(
            "[%s] %s complete: sparse=%.1fms dense=%.1fms fusion=%.1fms final_docs=%d",
            query_id, mode, t_sparse_ms, t_dense_ms, t_fusion_ms, len(final_ids),
        )

        if not final_ids:
            logger.warning("[%s] %s retrieval produced no documents", query_id, mode)
            return StreamingResponse(
                iter(["[No relevant documents found]"]),
                media_type="text/plain",
            )

        # Generation leg: SPHP (hint already dispatched at t_sparse) or a
        # single final message on a fresh stream (non-SPHP).
        if req.sphp and mode == "hybrid":
            stream = await sphp_hint_dispatch(
                query_id=query_id,
                query_text=query_text,
                sparse_doc_ids=sparse_ids,
                t_sparse_ms=t_sparse_ms,
                hint_depth=int(config["retrieval"]["hint_depth"]),
            )
            stream.send_final(
                query_id=query_id,
                query_text=query_text,
                fused_doc_ids=final_ids,
                t_sparse_ms=t_sparse_ms,
                t_dense_ms=t_dense_ms,
                t_fusion_ms=t_fusion_ms,
            )
        else:
            stream = await _open_final_stream(
                query_id=query_id,
                query_text=query_text,
                fused_doc_ids=final_ids,
                t_sparse_ms=t_sparse_ms,
                t_dense_ms=t_dense_ms,
                t_fusion_ms=t_fusion_ms,
            )

        tokens, meta, ttft_ms = await _drain_tokens(stream, query_id, t0)
        t_total_ms = (time.perf_counter() - t0) * 1000.0
        logger.info("[%s] Pipeline headers ready: %.1f ms", query_id, t_total_ms)

        headers = {
            "X-Query-Id": query_id,
            "X-Mode": mode,
            "X-RRF-K": str(rrf_k),
            "X-Sparse-Time-Ms": f"{t_sparse_ms:.2f}",
            "X-Dense-Time-Ms": f"{t_dense_ms:.2f}",
            "X-Fusion-Time-Ms": f"{t_fusion_ms:.2f}",
            "X-Fused-Docs-Count": str(len(fused_ids)),
            "X-Fused-Doc-Ids": ",".join(fused_ids[:10]),
            "X-Sparse-Doc-Ids": ",".join(sparse_ids[:10]),
            "X-Dense-Doc-Ids": ",".join(dense_ids[:10]),
            "X-Simulated-WAN-Ms": str(int(delay_seconds * 1000)),
        }

        async def token_stream():
            for tok in tokens:
                yield tok

        return StreamingResponse(token_stream(), media_type="text/plain", headers=headers)

    @app.post("/query/benchmark", response_model=BenchmarkResponse)
    async def benchmark_endpoint(
        req: QueryRequest,
        simulate_wan_delay_ms: int | None = Header(default=None, alias="X-Simulate-WAN-Delay"),
    ):
        """
        Synchronous benchmark endpoint (contract mirrors Node B's
        /query/benchmark): complete structured metrics, token counts,
        and decode throughput as JSON for automated evaluation.
        """
        query_id = str(uuid.uuid4())[:8]
        k = req.top_k or int(config["retrieval"]["top_k"])
        query_text = req.query.strip()
        mode, rrf_k = _resolve_mode_and_k(req)
        delay_seconds = max(simulate_wan_delay_ms or 0, 0) / 1000.0

        t0 = time.perf_counter()
        logger.info(
            "[%s] Benchmark query start | mode=%s rrf_k=%d sphp=%s query='%s'",
            query_id, mode, rrf_k, req.sphp, query_text[:60],
        )

        if delay_seconds > 0:
            logger.info("[%s] Simulating WAN delay: %.0f ms", query_id, delay_seconds * 1000)
            await asyncio.sleep(delay_seconds)

        # Mode-aware retrieval (mirrors Node B's _hybrid_retrieve).
        run_dense = mode in ("hybrid", "dense")
        run_sparse = mode in ("hybrid", "sparse")

        dense_future = (
            asyncio.to_thread(dense_forward, query_text, k, query_id)
            if run_dense else None
        )
        sparse_future = (
            asyncio.to_thread(sparse_retrieve, app.state.index, query_text, k, query_id)
            if run_sparse else None
        )

        if dense_future is not None and sparse_future is not None:
            (dense_ids, t_dense_ms), (sparse_ids, t_sparse_ms) = await asyncio.gather(
                dense_future, sparse_future,
            )
        elif dense_future is not None:
            dense_ids, t_dense_ms = await dense_future
            sparse_ids, t_sparse_ms = [], 0.0
        elif sparse_future is not None:
            sparse_ids, t_sparse_ms = await sparse_future
            dense_ids, t_dense_ms = [], 0.0
        else:
            dense_ids, t_dense_ms = [], 0.0
            sparse_ids, t_sparse_ms = [], 0.0

        # RRF fusion (only meaningful when both legs ran).
        if mode == "hybrid":
            fusion_start = time.perf_counter()
            fused_ids = await asyncio.to_thread(
                fused_reconcile, sparse_ids, dense_ids, rrf_k
            )
            final_ids = fused_ids
            t_fusion_ms = (time.perf_counter() - fusion_start) * 1000.0
        else:
            fused_ids = []
            final_ids = list(sparse_ids) if mode == "sparse" else list(dense_ids)
            t_fusion_ms = 0.0

        # Retrieval-only fast path: skip generation entirely (mirrors B).
        if req.retrieval_only:
            t_end = time.perf_counter()
            total_ms = (t_end - t0) * 1000.0
            timings = {
                "sparse_ms": round(t_sparse_ms, 2),
                "dense_ms": round(t_dense_ms, 2),
                "fusion_ms": round(t_fusion_ms, 2),
                "ttft_ms": 0,
                "decode_ms": 0,
                "total_ms": round(total_ms, 2),
                "simulated_wan_ms": round(delay_seconds * 1000, 2),
            }
            logger.info(
                "[%s] Retrieval-only complete: sparse=%.1fms dense=%.1fms fusion=%.1fms total=%.1fms (no generation)",
                query_id, t_sparse_ms, t_dense_ms, t_fusion_ms, total_ms,
            )
            return BenchmarkResponse(
                query_id=query_id,
                query=query_text,
                top_k=k,
                mode=mode,
                rrf_k=rrf_k,
                timings=timings,
                fused_doc_ids=fused_ids,
                sparse_doc_ids=sparse_ids,
                dense_doc_ids=dense_ids,
                token_count=0,
                decode_tps=0,
                answer_preview="",
                sphp=False,
            )

        # Generation leg: SPHP (hint at t_sparse) or single final message.
        meta: dict[str, Any] = {}
        if req.sphp and mode == "hybrid":
            stream = await sphp_hint_dispatch(
                query_id=query_id,
                query_text=query_text,
                sparse_doc_ids=sparse_ids,
                t_sparse_ms=t_sparse_ms,
                hint_depth=int(config["retrieval"]["hint_depth"]),
            )
            stream.send_final(
                query_id=query_id,
                query_text=query_text,
                fused_doc_ids=final_ids,
                t_sparse_ms=t_sparse_ms,
                t_dense_ms=t_dense_ms,
                t_fusion_ms=t_fusion_ms,
            )
        else:
            stream = await _open_final_stream(
                query_id=query_id,
                query_text=query_text,
                fused_doc_ids=final_ids,
                t_sparse_ms=t_sparse_ms,
                t_dense_ms=t_dense_ms,
                t_fusion_ms=t_fusion_ms,
            )

        tokens, meta, ttft_ms = await _drain_tokens(stream, query_id, t0)

        t_end = time.perf_counter()
        total_ms = (t_end - t0) * 1000.0
        decode_ms = (t_end - (t0 + ttft_ms / 1000.0)) * 1000.0 if ttft_ms else 0.0
        token_count = len(tokens)
        tps = (token_count - 1) / (decode_ms / 1000.0) if (decode_ms > 0 and token_count > 1) else 0.0

        answer = "".join(tokens)
        preview = answer[:120].replace("\n", " ") + "..." if len(answer) > 120 else answer

        if req.sphp and mode == "hybrid":
            fused_ids = meta.get("fused_ids", fused_ids)
            dense_ids = meta.get("dense_ids", dense_ids)
            t_dense_ms = meta.get("t_dense_ms", t_dense_ms)
            t_fusion_ms = meta.get("t_fusion_ms", t_fusion_ms)

        timings = {
            "sparse_ms": round(t_sparse_ms, 2),
            "dense_ms": round(t_dense_ms, 2),
            "fusion_ms": round(t_fusion_ms, 2),
            "ttft_ms": round(ttft_ms, 2),
            "decode_ms": round(decode_ms, 2),
            "total_ms": round(total_ms, 2),
            "simulated_wan_ms": round(delay_seconds * 1000, 2),
        }
        if meta.get("sphp_hit") is not None:
            timings["sphp_hit"] = meta["sphp_hit"]
            timings["sphp_overlap"] = meta.get("sphp_overlap", 0.0)
            timings["wasted_prefill_ms"] = meta.get("wasted_prefill_ms", 0.0)

        _write_telemetry({
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "query_id": query_id,
            "query": query_text,
            "top_k": k,
            "mode": mode,
            "rrf_k": rrf_k,
            "timings": timings,
            "fused_docs": fused_ids[:10],
            "sparse_doc_ids": sparse_ids[:10],
            "dense_doc_ids": dense_ids[:10],
            "token_count": token_count,
            "decode_tps": round(tps, 2),
            "sphp": req.sphp,
            "sphp_hit": meta.get("sphp_hit"),
            "sphp_overlap": meta.get("sphp_overlap"),
        })

        return BenchmarkResponse(
            query_id=query_id,
            query=query_text,
            top_k=k,
            mode=mode,
            rrf_k=rrf_k,
            timings=timings,
            fused_doc_ids=fused_ids,
            sparse_doc_ids=sparse_ids,
            dense_doc_ids=dense_ids,
            token_count=token_count,
            decode_tps=round(tps, 2),
            answer_preview=preview,
            answer_full=answer,
            sphp=req.sphp,
            sphp_hit=meta.get("sphp_hit"),
            sphp_overlap=meta.get("sphp_overlap"),
            wasted_prefill_ms=meta.get("wasted_prefill_ms"),
        )

    return app


# Module-level app for `uvicorn src.gateway:app` (index loads in lifespan).
app = create_app(load_config(DEFAULT_CONFIG_PATH))


if __name__ == "__main__":
    import uvicorn

    cfg = load_config(DEFAULT_CONFIG_PATH)
    uvicorn.run(
        app,
        host=cfg["gateway"]["host"],
        port=int(cfg["gateway"]["port"]),
    )
