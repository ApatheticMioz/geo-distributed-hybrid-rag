"""
Unified Hybrid Retrieval Server for Node B (2-Node Architecture).

Handles BOTH Dense (BGE-M3 + Qdrant) and Sparse (BM25 + Tantivy) retrieval
concurrently, performs Reciprocal Rank Fusion locally, and streams
fused context to Node A for LLM generation.

Replaces the deprecated 3-node design where Node C orchestrated
sparse retrieval and Node B only handled dense retrieval.
"""

import asyncio
import logging
import os
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import grpc
import numpy as np
import yaml
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from FlagEmbedding import BGEM3FlagModel
from pydantic import BaseModel
from qdrant_client import QdrantClient
import torch

# Add generated proto directory to path
_generated_dir = Path(__file__).resolve().parent.parent / "generated"
sys.path.insert(0, str(_generated_dir))

import hybrid_coordination_pb2
import hybrid_coordination_pb2_grpc

from .bm25_retriever import BM25Retriever
from .fusion import reciprocal_rank_fusion

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ============================================================================
# Configuration
# ============================================================================

MODEL_NAME = os.environ.get("BGE_M3_MODEL", "BAAI/bge-m3")
QDRANT_HOST = os.environ.get("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.environ.get("QDRANT_PORT", "6333"))
QDRANT_GRPC_PORT = int(os.environ.get("QDRANT_GRPC_PORT", "6334"))
QDRANT_TIMEOUT_SECONDS = max(1, int(float(os.environ.get("QDRANT_TIMEOUT_SECONDS", "30"))))
COLLECTION_NAME = os.environ.get("QDRANT_COLLECTION", "msmarco_passages")
SERVER_PORT = int(os.environ.get("NODE_B_GRPC_PORT", "50051"))
DEFAULT_TOP_K = int(os.environ.get("NODE_B_TOP_K", "10"))
RRF_K = int(os.environ.get("RRF_K", "60"))
# Number of fused documents sent to Node A (truncation cap on the fused list)
TOP_K_DOCS = int(os.environ.get("NODE_B_TOP_K_DOCS", "5"))
NODE_A_GRPC_HOST = os.environ.get("NODE_A_GRPC_HOST", "10.8.0.1")
NODE_A_GRPC_PORT = int(os.environ.get("NODE_A_GRPC_PORT", "50052"))

# BM25 index path (relative to Node B implementation dir)
BM25_INDEX_PATH = os.environ.get(
    "BM25_INDEX_PATH",
    str(Path(__file__).resolve().parent.parent / "data" / "tantivy_index"),
)

# Global handles
model = None
qdrant_client: Optional[QdrantClient] = None
bm25_retriever: Optional[BM25Retriever] = None
active_tasks: set = set()
# Serializes BGE-M3 model.encode across concurrent requests (encoder is not
# thread-safe; concurrent benchmark requests must not race on it).
_encode_lock = threading.Lock()


# ============================================================================
# Initialization
# ============================================================================

def initialize_globals() -> None:
    global model, qdrant_client, bm25_retriever

    # Load BGE-M3 embedding model
    logger.info("Loading %s with FP16 precision...", MODEL_NAME)
    model = BGEM3FlagModel(MODEL_NAME, use_fp16=torch.cuda.is_available())
    logger.info("BGE-M3 model loaded")

    # Connect to Qdrant
    logger.info(
        "Connecting to Qdrant at %s:%s (grpc=%s) | collection=%s",
        QDRANT_HOST, QDRANT_PORT, QDRANT_GRPC_PORT, COLLECTION_NAME,
    )
    qdrant_client = QdrantClient(
        host=QDRANT_HOST,
        port=QDRANT_PORT,
        grpc_port=QDRANT_GRPC_PORT,
        prefer_grpc=False,
        timeout=QDRANT_TIMEOUT_SECONDS,
    )
    logger.info("Qdrant client configured")

    # Open BM25 index
    try:
        bm25_retriever = BM25Retriever(BM25_INDEX_PATH)
        logger.info("BM25 retriever initialized")
    except FileNotFoundError:
        logger.warning(
            "BM25 index not found at %s. Sparse retrieval will be unavailable. "
            "Run the index building script to enable hybrid retrieval.",
            BM25_INDEX_PATH,
        )
        bm25_retriever = None


def warmup_model() -> None:
    assert model is not None

    warmup_query = "Warmup query to compile CUDA kernels"
    logger.info("Priming GPU with warmup query before opening port %s...", SERVER_PORT)

    with torch.inference_mode():
        embedding = model.encode([warmup_query], return_dense=True)
        _ = np.asarray(embedding["dense_vecs"][0], dtype=np.float32)

    logger.info("GPU warmup complete; server is ready for requests.")


# ============================================================================
# Retrieval Logic
# ============================================================================

def _dense_retrieve(query: str, top_k: int, query_id: str) -> tuple[list[str], float]:
    """
    Perform dense retrieval via BGE-M3 + Qdrant.
    Returns (doc_id_list, elapsed_ms).
    """
    assert model is not None
    assert qdrant_client is not None

    started = time.perf_counter()

    # Serialize concurrent encodes: BGE-M3's model.encode is not thread-safe,
    # and this runs in a worker thread per request (asyncio.to_thread).
    with _encode_lock, torch.inference_mode():
        embedding = model.encode([query], return_dense=True)
    query_vector = np.asarray(embedding["dense_vecs"][0], dtype=np.float32).tolist()

    search_response = qdrant_client.query_points(
        collection_name=COLLECTION_NAME,
        query=query_vector,
        limit=top_k,
        with_payload=["doc_id"],
        timeout=QDRANT_TIMEOUT_SECONDS,
    )
    search_results = search_response.points

    doc_ids = []
    for rank, result in enumerate(search_results, start=1):
        payload = result.payload or {}
        doc_id = str(payload.get("doc_id", result.id))
        doc_ids.append(doc_id)

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    logger.info(
        "[%s] Dense retrieval: %.1f ms, %d docs",
        query_id, elapsed_ms, len(doc_ids),
    )
    return doc_ids, elapsed_ms


def _sparse_retrieve(query: str, top_k: int, query_id: str) -> tuple[list[str], float]:
    """
    Perform sparse BM25 retrieval via Tantivy.
    Returns (doc_id_list, elapsed_ms).
    """
    if bm25_retriever is None:
        logger.warning("[%s] BM25 retriever not available; skipping sparse retrieval", query_id)
        return [], 0.0

    started = time.perf_counter()
    results = bm25_retriever.query(query, top_k)

    doc_ids = [r["doc_id"] for r in results if r.get("doc_id")]
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    logger.info(
        "[%s] Sparse retrieval: %.1f ms, %d docs",
        query_id, elapsed_ms, len(doc_ids),
    )
    return doc_ids, elapsed_ms


async def _hybrid_retrieve(
    query: str,
    top_k: int,
    query_id: str,
    mode: str = "hybrid",
    rrf_k: int = RRF_K,
) -> tuple[list[str], list[str], list[str], list[str], float, float, float]:
    """
    Run retrieval per the requested mode, then (for hybrid) fuse with RRF.

    mode:
      - 'hybrid': run sparse + dense concurrently, fuse with RRF.
      - 'sparse': run only the sparse (BM25) leg; dense leg skipped (0.0 ms).
      - 'dense':  run only the dense (BGE-M3) leg; sparse leg skipped (0.0 ms).

    Returns (final_ids, sparse_ids, dense_ids, fused_ids,
             t_dense_ms, t_sparse_ms, t_fusion_ms).
      - final_ids: the ranking sent to Node A (fused for hybrid, the single
        active leg's list for sparse/dense).
      - fused_ids: the RRF-fused list; only populated in hybrid mode,
        otherwise empty.
      - Skipped legs report 0.0 ms and an empty doc-id list.
    """
    run_dense = mode in ("hybrid", "dense")
    run_sparse = mode in ("hybrid", "sparse")

    dense_future = (
        asyncio.to_thread(_dense_retrieve, query, top_k, query_id)
        if run_dense else None
    )
    sparse_future = (
        asyncio.to_thread(_sparse_retrieve, query, top_k, query_id)
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

    # RRF fusion (only meaningful when both legs ran)
    if mode == "hybrid":
        fusion_start = time.perf_counter()
        fused_ids = reciprocal_rank_fusion(
            sparse_ids, dense_ids if dense_ids else None, rrf_k
        )
        final_ids = fused_ids
        t_fusion_ms = (time.perf_counter() - fusion_start) * 1000.0
    else:
        # Single-leg mode: the active leg's ranking is the final ranking;
        # there is no RRF-fused list to report and no fusion time.
        fused_ids = []
        final_ids = list(sparse_ids) if mode == "sparse" else list(dense_ids)
        t_fusion_ms = 0.0

    logger.info(
        "[%s] %s complete: sparse=%.1fms dense=%.1fms fusion=%.1fms final_docs=%d",
        query_id, mode, t_sparse_ms, t_dense_ms, t_fusion_ms, len(final_ids),
    )
    return final_ids, sparse_ids, dense_ids, fused_ids, t_dense_ms, t_sparse_ms, t_fusion_ms


def _resolve_mode_and_k(req: "QueryRequest") -> tuple[str, int]:
    """
    Normalize the requested retrieval mode and RRF constant from a request.

    mode is validated against {'hybrid','sparse','dense'} (case-insensitive);
    an invalid value raises HTTPException(400). rrf_k falls back to the
    module-level RRF_K when unset or non-positive.
    """
    mode = (req.mode or "hybrid").strip().lower()
    if mode not in ("hybrid", "sparse", "dense"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid mode '{req.mode}'. Must be one of: hybrid, sparse, dense.",
        )
    rrf_k = req.rrf_k if (req.rrf_k is not None and req.rrf_k > 0) else RRF_K
    return mode, rrf_k


# ============================================================================
# gRPC Server — Node A generates tokens from fused context
# ============================================================================

async def stream_to_node_a(
    query_id: str,
    query_text: str,
    fused_doc_ids: list[str] | None = None,
    t_sparse_ms: float = 0.0,
    t_dense_ms: float = 0.0,
    t_fusion_ms: float = 0.0,
    sparse_doc_ids: list[str] | None = None,
    dense_future: Any = None,
    rrf_k: int = RRF_K,
    meta: dict | None = None,
) -> asyncio.Queue:
    """
    Open bidirectional gRPC stream to Node A, send context,
    and yield generated tokens into a queue for downstream consumption.

    Supports SPHP (Speculative Progressive Hydration & Prefill):
    If dense_future is provided, message 1 (sparse hint) is dispatched
    immediately on the stream at t_sparse (~150ms). When dense_future
    completes, RRF fusion is computed and message 2 (final fused docs)
    is dispatched on the same stream for Node A to reconcile.
    """
    token_queue = asyncio.Queue(maxsize=256)
    target = f"{NODE_A_GRPC_HOST}:{NODE_A_GRPC_PORT}"

    async def _stream_worker():
        nonlocal fused_doc_ids, t_dense_ms, t_fusion_ms
        async with grpc.aio.insecure_channel(target) as channel:
            stub = hybrid_coordination_pb2_grpc.GenerationOrchestratorStub(channel)
            request_queue = asyncio.Queue()

            async def _feeder():
                nonlocal fused_doc_ids, t_dense_ms, t_fusion_ms
                if dense_future is not None and sparse_doc_ids:
                    # SPHP Step 1: Send SparseHint immediately
                    sparse_docs = [
                        hybrid_coordination_pb2.FusedDocument(
                            doc_id=doc_id,
                            rrf_score=0.0,
                            rank=rank,
                        )
                        for rank, doc_id in enumerate(sparse_doc_ids[:TOP_K_DOCS], start=1)
                    ]
                    hint_req = hybrid_coordination_pb2.HybridContextRequest(
                        query_id=query_id,
                        query_text=query_text,
                        fused_docs=sparse_docs,
                        t_sparse_ms=t_sparse_ms,
                        is_sparse_hint=True,
                    )
                    logger.info("[%s] SPHP: Dispatched early SparseHint (%d docs) to Node A",
                                query_id, len(sparse_docs))
                    await request_queue.put(hint_req)

                    # Await dense retrieval completing in background
                    dense_ids, t_dense_ms = await dense_future
                    f_start = time.perf_counter()
                    fused_doc_ids = reciprocal_rank_fusion(sparse_doc_ids, dense_ids, rrf_k)
                    t_fusion_ms = (time.perf_counter() - f_start) * 1000.0
                    if meta is not None:
                        meta["dense_ids"] = dense_ids
                        meta["fused_ids"] = fused_doc_ids
                        meta["t_dense_ms"] = t_dense_ms
                        meta["t_fusion_ms"] = t_fusion_ms

                # Final fused context
                active_fused = fused_doc_ids or []
                if len(active_fused) > TOP_K_DOCS:
                    logger.debug(
                        "[%s] Truncating fused docs to top %d (dropped %d)",
                        query_id, TOP_K_DOCS, len(active_fused) - TOP_K_DOCS,
                    )
                fused_docs = [
                    hybrid_coordination_pb2.FusedDocument(
                        doc_id=doc_id,
                        rrf_score=0.0,
                        rank=rank,
                    )
                    for rank, doc_id in enumerate(active_fused[:TOP_K_DOCS], start=1)
                ]

                final_req = hybrid_coordination_pb2.HybridContextRequest(
                    query_id=query_id,
                    query_text=query_text,
                    fused_docs=fused_docs,
                    t_sparse_ms=t_sparse_ms,
                    t_dense_ms=t_dense_ms,
                    t_fusion_ms=t_fusion_ms,
                    is_sparse_hint=False,
                )
                logger.info("[%s] SPHP: Dispatched final context (%d docs) to Node A",
                            query_id, len(fused_docs))
                await request_queue.put(final_req)
                await request_queue.put(None)  # Sentinel to end stream

            asyncio.create_task(_feeder())

            async def request_iterator():
                while True:
                    req = await request_queue.get()
                    if req is None:
                        break
                    yield req

            logger.info("[%s] Streaming context to Node A at %s (sphp=%s)",
                        query_id, target, dense_future is not None)
            try:
                async for token in stub.GenerateStream(request_iterator()):
                    if meta is not None:
                        if token.sphp_hit:
                            meta["sphp_hit"] = True
                            meta["sphp_overlap"] = round(token.sphp_overlap, 2)
                        elif "sphp_hit" not in meta and token.sphp_overlap > 0:
                            meta["sphp_hit"] = False
                            meta["sphp_overlap"] = round(token.sphp_overlap, 2)
                        if token.wasted_prefill_ms > 0:
                            meta["wasted_prefill_ms"] = round(token.wasted_prefill_ms, 2)

                    if token.is_final:
                        await token_queue.put(None)  # Sentinel
                        break
                    if token.token:
                        await token_queue.put(token.token)
            except grpc.aio.AioRpcError as exc:
                logger.error("[%s] Node A gRPC stream failed: %s - %s", query_id, exc.code(), exc.details())
                await token_queue.put(f"[ERROR] Node A stream failed: {exc.code()} - {exc.details()}")
                await token_queue.put(None)

    asyncio.create_task(_stream_worker())
    return token_queue


# ============================================================================
# FastAPI Gateway (replaces Node C)
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan: initialize models and connections on startup."""
    logger.info("Starting Node B...")
    initialize_globals()
    warmup_model()
    yield
    logger.info("Shutting down Node B...")


app = FastAPI(
    title="Node B - Hybrid Retrieval Gateway",
    version="2.0.0",
    lifespan=lifespan,
)


class QueryRequest(BaseModel):
    query: str
    top_k: int = 10
    # Retrieval mode: 'hybrid' (both legs + RRF), 'sparse' (BM25 only),
    # 'dense' (BGE-M3 only). Default 'hybrid' preserves prior behavior.
    mode: str = "hybrid"
    # RRF constant k, wired into reciprocal_rank_fusion. Default 60.
    rrf_k: int = RRF_K
    # Retrieval-only fast path: when true, /query/benchmark skips the Node A
    # gRPC generation entirely and returns per-mode rankings + retrieval
    # timings with zeroed generation metrics (no gRPC call). Default false.
    retrieval_only: bool = False
    # SPHP (Speculative Progressive Hydration & Prefill): dispatch sparse
    # hint early while dense leg runs in background. Default false.
    sphp: bool = False


@app.post("/query")
async def query_endpoint(
    req: QueryRequest,
    simulate_wan_delay_ms: int | None = Header(default=None, alias="X-Simulate-WAN-Delay"),
):
    """
    Main query endpoint. Executes hybrid retrieval (BM25 + Dense),
    fuses with RRF, streams fused context to Node A, and returns
    generated tokens to the client.
    """
    query_id = str(uuid.uuid4())[:8]
    k = req.top_k or DEFAULT_TOP_K
    query_text = req.query.strip()
    mode, rrf_k = _resolve_mode_and_k(req)
    delay_seconds = max(simulate_wan_delay_ms or 0, 0) / 1000.0

    t0 = time.perf_counter()
    logger.info("[%s] Pipeline start | mode=%s rrf_k=%d sphp=%s query='%s'",
                query_id, mode, rrf_k, req.sphp, query_text[:60])

    if delay_seconds > 0:
        logger.info("[%s] Simulating WAN delay: %.0f ms", query_id, delay_seconds * 1000)
        await asyncio.sleep(delay_seconds)

    meta: dict[str, Any] = {}
    if req.sphp and mode == "hybrid":
        dense_future = asyncio.to_thread(_dense_retrieve, query_text, k, query_id)
        sparse_future = asyncio.to_thread(_sparse_retrieve, query_text, k, query_id)
        sparse_ids, t_sparse_ms = await sparse_future

        token_queue = await stream_to_node_a(
            query_id=query_id,
            query_text=query_text,
            t_sparse_ms=t_sparse_ms,
            sparse_doc_ids=sparse_ids,
            dense_future=dense_future,
            rrf_k=rrf_k,
            meta=meta,
        )
        final_ids = sparse_ids
        dense_ids, fused_ids = [], []
        t_dense_ms, t_fusion_ms = 0.0, 0.0
    else:
        # Step 1: Retrieval (mode-aware)
        (final_ids, sparse_ids, dense_ids, fused_ids,
         t_dense_ms, t_sparse_ms, t_fusion_ms) = await _hybrid_retrieve(
            query=query_text, top_k=k, query_id=query_id,
            mode=mode, rrf_k=rrf_k,
        )

        if not final_ids:
            logger.warning("[%s] %s retrieval produced no documents", query_id, mode)
            return StreamingResponse(
                iter(["[No relevant documents found]"]),
                media_type="text/plain",
            )

        # Step 2: Stream the final ranking to Node A and return tokens
        token_queue = await stream_to_node_a(
            query_id=query_id,
            query_text=query_text,
            fused_doc_ids=final_ids,
            t_sparse_ms=t_sparse_ms,
            t_dense_ms=t_dense_ms,
            t_fusion_ms=t_fusion_ms,
            meta=meta,
        )

    first_token = True
    ttft_recorded = 0.0

    async def token_stream():
        nonlocal first_token, ttft_recorded
        while True:
            token = await token_queue.get()
            if token is None:  # Sentinel
                break
            if first_token:
                ttft_recorded = (time.perf_counter() - t0) * 1000
                logger.info("[%s] First token in %.1f ms", query_id, ttft_recorded)
                first_token = False
            yield token

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

    t_total_ms = (time.perf_counter() - t0) * 1000
    logger.info("[%s] Pipeline headers ready: %.1f ms", query_id, t_total_ms)

    return StreamingResponse(token_stream(), media_type="text/plain", headers=headers)


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
    sphp: bool = False
    sphp_hit: bool | None = None
    sphp_overlap: float | None = None
    wasted_prefill_ms: float | None = None


@app.post("/query/benchmark", response_model=BenchmarkResponse)
async def benchmark_endpoint(
    req: QueryRequest,
    simulate_wan_delay_ms: int | None = Header(default=None, alias="X-Simulate-WAN-Delay"),
):
    """
    Synchronous benchmark endpoint that returns complete structured metrics,
    token counts, and decode throughput as JSON for automated evaluation.
    """
    query_id = str(uuid.uuid4())[:8]
    k = req.top_k or DEFAULT_TOP_K
    query_text = req.query.strip()
    mode, rrf_k = _resolve_mode_and_k(req)
    delay_seconds = max(simulate_wan_delay_ms or 0, 0) / 1000.0

    t0 = time.perf_counter()
    logger.info("[%s] Benchmark query start | mode=%s rrf_k=%d sphp=%s query='%s'",
                query_id, mode, rrf_k, req.sphp, query_text[:60])

    if delay_seconds > 0:
        logger.info("[%s] Simulating WAN delay: %.0f ms", query_id, delay_seconds * 1000)
        await asyncio.sleep(delay_seconds)

    meta: dict[str, Any] = {}
    if req.sphp and mode == "hybrid":
        dense_future = asyncio.to_thread(_dense_retrieve, query_text, k, query_id)
        sparse_future = asyncio.to_thread(_sparse_retrieve, query_text, k, query_id)
        sparse_ids, t_sparse_ms = await sparse_future

        token_queue = await stream_to_node_a(
            query_id=query_id,
            query_text=query_text,
            t_sparse_ms=t_sparse_ms,
            sparse_doc_ids=sparse_ids,
            dense_future=dense_future,
            rrf_k=rrf_k,
            meta=meta,
        )
        final_ids = sparse_ids
        dense_ids, fused_ids = [], []
        t_dense_ms, t_fusion_ms = 0.0, 0.0
    else:
        (final_ids, sparse_ids, dense_ids, fused_ids,
         t_dense_ms, t_sparse_ms, t_fusion_ms) = await _hybrid_retrieve(
            query=query_text, top_k=k, query_id=query_id,
            mode=mode, rrf_k=rrf_k,
        )

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

        token_queue = await stream_to_node_a(
            query_id=query_id,
            query_text=query_text,
            fused_doc_ids=final_ids,
            t_sparse_ms=t_sparse_ms,
            t_dense_ms=t_dense_ms,
            t_fusion_ms=t_fusion_ms,
            meta=meta,
        )

    first_token_time = None
    tokens = []
    while True:
        token = await token_queue.get()
        if token is None:
            break
        if first_token_time is None:
            first_token_time = time.perf_counter()
        tokens.append(token)

    t_end = time.perf_counter()
    ttft_ms = (first_token_time - t0) * 1000.0 if first_token_time else (t_end - t0) * 1000.0
    total_ms = (t_end - t0) * 1000.0
    decode_ms = (t_end - first_token_time) * 1000.0 if first_token_time else 0.0
    token_count = len(tokens)
    tps = (token_count - 1) / (decode_ms / 1000.0) if (decode_ms > 0 and token_count > 1) else 0.0

    answer = "".join(tokens)
    preview = answer[:120].replace("\n", " ") + "..." if len(answer) > 120 else answer

    if req.sphp and mode == "hybrid":
        fused_ids = meta.get("fused_ids", [])
        dense_ids = meta.get("dense_ids", [])
        t_dense_ms = meta.get("t_dense_ms", 0.0)
        t_fusion_ms = meta.get("t_fusion_ms", 0.0)

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

    # Append to local telemetry.jsonl for persistent logging
    os.makedirs("benchmarks", exist_ok=True)
    with open("benchmarks/telemetry.jsonl", "a", encoding="utf-8") as f_log:
        import json
        f_log.write(json.dumps({
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
        }) + "\n")

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
        sphp=req.sphp,
        sphp_hit=meta.get("sphp_hit"),
        sphp_overlap=meta.get("sphp_overlap"),
        wasted_prefill_ms=meta.get("wasted_prefill_ms"),
    )


@app.get("/health")
async def health():
    return {"status": "ok", "node": "B", "role": "hybrid_retrieval_gateway"}


# ============================================================================
# gRPC Server Entry Point
# ============================================================================

async def run_async_server() -> None:
    """Legacy gRPC-only server mode (no FastAPI gateway)."""
    initialize_globals()

    server = grpc.aio.server(options=[
        ("grpc.http2.min_recv_ping_interval_without_data_ms", 10000),
        ("grpc.http2.max_pings_without_data", 0),
        ("grpc.keepalive_permit_without_calls", 1),
    ])

    warmup_model()

    port = server.add_insecure_port(f"0.0.0.0:{SERVER_PORT}")
    if port == 0:
        raise RuntimeError(f"Failed to bind Node B gRPC server to port {SERVER_PORT}")

    await server.start()
    logger.info("gRPC server listening on 0.0.0.0:%s", SERVER_PORT)

    try:
        await server.wait_for_termination()
    finally:
        await server.stop(0)


if __name__ == "__main__":
    asyncio.run(run_async_server())