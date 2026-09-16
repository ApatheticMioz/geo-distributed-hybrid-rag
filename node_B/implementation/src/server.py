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
    query: str, top_k: int, query_id: str
) -> tuple[list[str], float, float, float]:
    """
    Run dense + sparse retrieval concurrently, then fuse with RRF.
    Returns (fused_doc_ids, t_dense_ms, t_sparse_ms, t_fusion_ms).
    """
    # Run both retrieval methods concurrently in separate threads
    dense_future = asyncio.to_thread(_dense_retrieve, query, top_k, query_id)
    sparse_future = asyncio.to_thread(_sparse_retrieve, query, top_k, query_id)

    (dense_ids, t_dense_ms), (sparse_ids, t_sparse_ms) = await asyncio.gather(
        dense_future, sparse_future,
    )

    # RRF fusion
    fusion_start = time.perf_counter()
    fused_ids = reciprocal_rank_fusion(sparse_ids, dense_ids if dense_ids else None, RRF_K)
    t_fusion_ms = (time.perf_counter() - fusion_start) * 1000.0

    logger.info(
        "[%s] Hybrid complete: sparse=%.1fms dense=%.1fms fusion=%.1fms fused_docs=%d",
        query_id, t_sparse_ms, t_dense_ms, t_fusion_ms, len(fused_ids),
    )
    return fused_ids, t_dense_ms, t_sparse_ms, t_fusion_ms


# ============================================================================
# gRPC Server — Node A generates tokens from fused context
# ============================================================================

async def stream_to_node_a(
    query_id: str,
    query_text: str,
    fused_doc_ids: list[str],
    t_sparse_ms: float,
    t_dense_ms: float,
    t_fusion_ms: float,
) -> asyncio.Queue:
    """
    Open bidirectional gRPC stream to Node A, send fused context,
    and yield generated tokens into a queue for downstream consumption.
    """
    token_queue = asyncio.Queue(maxsize=256)
    target = f"{NODE_A_GRPC_HOST}:{NODE_A_GRPC_PORT}"

    async def _stream_worker():
        async with grpc.aio.insecure_channel(target) as channel:
            stub = hybrid_coordination_pb2_grpc.GenerationOrchestratorStub(channel)

            # Build the fused document list (truncated to TOP_K_DOCS)
            if len(fused_doc_ids) > TOP_K_DOCS:
                logger.debug(
                    "[%s] Truncating fused docs to top %d (dropped %d)",
                    query_id, TOP_K_DOCS, len(fused_doc_ids) - TOP_K_DOCS,
                )
            fused_docs = [
                hybrid_coordination_pb2.FusedDocument(
                    doc_id=doc_id,
                    rrf_score=0.0,  # Score already baked into rank order
                    rank=rank,
                )
                for rank, doc_id in enumerate(fused_doc_ids[:TOP_K_DOCS], start=1)
            ]

            request = hybrid_coordination_pb2.HybridContextRequest(
                query_id=query_id,
                query_text=query_text,
                fused_docs=fused_docs,
                t_sparse_ms=t_sparse_ms,
                t_dense_ms=t_dense_ms,
                t_fusion_ms=t_fusion_ms,
            )

            async def request_iterator():
                yield request

            logger.info("[%s] Streaming fused context to Node A at %s", query_id, target)
            try:
                async for token in stub.GenerateStream(request_iterator()):
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
    delay_seconds = max(simulate_wan_delay_ms or 0, 0) / 1000.0

    t0 = time.perf_counter()
    logger.info("[%s] Pipeline start | query='%s'", query_id, query_text[:60])

    if delay_seconds > 0:
        logger.info("[%s] Simulating WAN delay: %.0f ms", query_id, delay_seconds * 1000)
        await asyncio.sleep(delay_seconds)

    # Step 1: Hybrid retrieval
    fused_ids, t_dense_ms, t_sparse_ms, t_fusion_ms = await _hybrid_retrieve(
        query=query_text, top_k=k, query_id=query_id,
    )

    if not fused_ids:
        logger.warning("[%s] Hybrid retrieval produced no documents", query_id)
        return StreamingResponse(
            iter(["[No relevant documents found]"]),
            media_type="text/plain",
        )

    # Step 2: Stream fused context to Node A and return tokens
    token_queue = await stream_to_node_a(
        query_id=query_id,
        query_text=query_text,
        fused_doc_ids=fused_ids,
        t_sparse_ms=t_sparse_ms,
        t_dense_ms=t_dense_ms,
        t_fusion_ms=t_fusion_ms,
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
        "X-Sparse-Time-Ms": f"{t_sparse_ms:.2f}",
        "X-Dense-Time-Ms": f"{t_dense_ms:.2f}",
        "X-Fusion-Time-Ms": f"{t_fusion_ms:.2f}",
        "X-Fused-Docs-Count": str(len(fused_ids)),
        "X-Fused-Doc-Ids": ",".join(fused_ids[:10]),
        "X-Simulated-WAN-Ms": str(int(delay_seconds * 1000)),
    }

    t_total_ms = (time.perf_counter() - t0) * 1000
    logger.info("[%s] Pipeline headers ready: %.1f ms", query_id, t_total_ms)

    return StreamingResponse(token_stream(), media_type="text/plain", headers=headers)


class BenchmarkResponse(BaseModel):
    query_id: str
    query: str
    top_k: int
    timings: dict[str, float]
    fused_doc_ids: list[str]
    token_count: int
    decode_tps: float
    answer_preview: str


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
    delay_seconds = max(simulate_wan_delay_ms or 0, 0) / 1000.0

    t0 = time.perf_counter()
    logger.info("[%s] Benchmark query start | query='%s'", query_id, query_text[:60])

    if delay_seconds > 0:
        logger.info("[%s] Simulating WAN delay: %.0f ms", query_id, delay_seconds * 1000)
        await asyncio.sleep(delay_seconds)

    fused_ids, t_dense_ms, t_sparse_ms, t_fusion_ms = await _hybrid_retrieve(
        query=query_text, top_k=k, query_id=query_id,
    )

    token_queue = await stream_to_node_a(
        query_id=query_id,
        query_text=query_text,
        fused_doc_ids=fused_ids,
        t_sparse_ms=t_sparse_ms,
        t_dense_ms=t_dense_ms,
        t_fusion_ms=t_fusion_ms,
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

    timings = {
        "sparse_ms": round(t_sparse_ms, 2),
        "dense_ms": round(t_dense_ms, 2),
        "fusion_ms": round(t_fusion_ms, 2),
        "ttft_ms": round(ttft_ms, 2),
        "decode_ms": round(decode_ms, 2),
        "total_ms": round(total_ms, 2),
        "simulated_wan_ms": round(delay_seconds * 1000, 2),
    }

    # Append to local telemetry.jsonl for persistent logging
    os.makedirs("benchmarks", exist_ok=True)
    with open("benchmarks/telemetry.jsonl", "a", encoding="utf-8") as f_log:
        import json
        f_log.write(json.dumps({
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "query_id": query_id,
            "query": query_text,
            "top_k": k,
            "timings": timings,
            "fused_docs": fused_ids[:10],
            "token_count": token_count,
            "decode_tps": round(tps, 2),
        }) + "\n")

    return BenchmarkResponse(
        query_id=query_id,
        query=query_text,
        top_k=k,
        timings=timings,
        fused_doc_ids=fused_ids[:10],
        token_count=token_count,
        decode_tps=round(tps, 2),
        answer_preview=preview,
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