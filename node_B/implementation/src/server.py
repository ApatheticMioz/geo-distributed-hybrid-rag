"""Async dense retrieval and forwarding server for Node B."""

import asyncio
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional

import grpc
import numpy as np
from FlagEmbedding import BGEM3FlagModel
from qdrant_client import QdrantClient
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "generated"))
import coordination_pb2  # noqa: E402
import coordination_pb2_grpc  # noqa: E402
import dispatch_pb2  # noqa: E402
import dispatch_pb2_grpc  # noqa: E402
import result_forward_pb2  # noqa: E402
import result_forward_pb2_grpc  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

MODEL_NAME = os.environ.get("BGE_M3_MODEL", "BAAI/bge-m3")
QDRANT_HOST = os.environ.get("QDRANT_HOST", "127.0.0.1")
QDRANT_PORT = int(os.environ.get("QDRANT_PORT", "6333"))
QDRANT_GRPC_PORT = int(os.environ.get("QDRANT_GRPC_PORT", "6334"))
COLLECTION_NAME = os.environ.get("QDRANT_COLLECTION", "msmarco_passages")
SERVER_PORT = int(os.environ.get("NODE_B_GRPC_PORT", "50051"))
DEFAULT_TOP_K = int(os.environ.get("NODE_B_TOP_K", "10"))
DB_PATH = Path(__file__).resolve().parents[1] / "corpus.sqlite"
NODE_A_GRPC_HOST = os.environ.get("NODE_A_GRPC_HOST", "10.8.0.1")

model: Optional[BGEM3FlagModel] = None
qdrant_client: Optional[QdrantClient] = None
active_tasks: set = set()  # Keep task references alive to prevent garbage collection


def _load_text(doc_id: str) -> str:
    if not DB_PATH.exists():
        return ""

    with sqlite3.connect(DB_PATH) as connection:
        cursor = connection.execute("SELECT text FROM passages WHERE doc_id = ?", (doc_id,))
        row = cursor.fetchone()
        return row[0] if row and row[0] else ""


def initialize_globals() -> None:
    global model, qdrant_client

    logger.info("Loading %s with FP16 precision...", MODEL_NAME)
    model = BGEM3FlagModel(MODEL_NAME, use_fp16=True)
    logger.info(
        "Connecting to Qdrant at %s:%s (grpc=%s) | collection=%s",
        QDRANT_HOST,
        QDRANT_PORT,
        QDRANT_GRPC_PORT,
        COLLECTION_NAME,
    )
    qdrant_client = QdrantClient(
        host=QDRANT_HOST,
        port=QDRANT_PORT,
        grpc_port=QDRANT_GRPC_PORT,
        prefer_grpc=True,
    )


def warmup_model() -> None:
    assert model is not None

    warmup_query = "Warmup query to compile CUDA kernels"
    print(f"Priming GPU with warmup query before opening port {SERVER_PORT}...")
    logger.info("Priming GPU with warmup query before opening port %s...", SERVER_PORT)

    try:
        with torch.inference_mode():
            embedding = model.encode([warmup_query], return_dense=True)
            _ = np.asarray(embedding["dense_vecs"][0], dtype=np.float32)
    except Exception:
        logger.exception("GPU warmup failed")
        raise

    print("GPU warmup complete; server is ready for requests.")
    logger.info("GPU warmup complete; server is ready for requests.")


def _retrieve_documents(query: str, top_k: int, query_id: str) -> tuple[list[coordination_pb2.RetrievedDocument], float]:
    assert model is not None
    assert qdrant_client is not None

    started = time.perf_counter()

    logger.info(
        "[%s] Dense retrieval started | top_k=%d | query='%s'",
        query_id,
        top_k,
        query[:160],
    )

    try:
        with torch.inference_mode():
            embedding = model.encode([query], return_dense=True)
        query_vector = np.asarray(embedding["dense_vecs"][0], dtype=np.float32).tolist()

        search_response = qdrant_client.query_points(
            collection_name=COLLECTION_NAME,
            query=query_vector,
            limit=top_k,
            with_payload=True,
        )
        search_results = search_response.points

        if not search_results:
            logger.warning("[%s] Qdrant returned 0 results", query_id)

        documents: list[coordination_pb2.RetrievedDocument] = []
        missing_text = 0
        for rank, result in enumerate(search_results, start=1):
            payload = result.payload or {}
            doc_id = str(payload.get("doc_id", result.id))
            text = _load_text(doc_id)
            if not text:
                missing_text += 1
            documents.append(
                coordination_pb2.RetrievedDocument(
                    doc_id=doc_id,
                    text=text,
                    score=float(result.score),
                    rank=rank,
                )
            )

        if missing_text:
            logger.warning("[%s] Missing text for %d/%d docs", query_id, missing_text, len(documents))

        return documents, (time.perf_counter() - started) * 1000.0
    except Exception as exc:
        logger.exception("[%s] Dense retrieval failed: %s", query_id, exc)
        return [], (time.perf_counter() - started) * 1000.0


async def forward_dense_results_to_node_a(
    query_id: str,
    node_a_lan_host: str,
    node_a_grpc_port: int,
    documents: list[coordination_pb2.RetrievedDocument],
    t_dense_ms: float,
) -> None:
    request = result_forward_pb2.DenseResultForward(
        query_id=query_id,
        docs=documents,
        t_dense_ms=t_dense_ms,
    )

    targets = [
        f"{NODE_A_GRPC_HOST}:{node_a_grpc_port}",
        f"{node_a_lan_host}:{node_a_grpc_port}",
    ]

    last_error: Exception | None = None
    for target in targets:
        try:
            logger.info(
                "[%s] Forwarding %d dense docs to Node A at %s (t_dense=%.1fms)",
                query_id,
                len(documents),
                target,
                t_dense_ms,
            )
            async with grpc.aio.insecure_channel(target) as channel:
                stub = result_forward_pb2_grpc.ResultForwarderStub(channel)
                await stub.ForwardDenseResults(request, timeout=5.0)
                logger.info("[%s] Forwarded dense results to Node A at %s", query_id, target)
                return
        except Exception as exc:
            last_error = exc
            logger.warning("[%s] Forward to Node A failed at %s: %s", query_id, target, exc)

    if last_error is not None:
        logger.error("[%s] Dense results could not be forwarded to Node A after all targets failed: %s", query_id, last_error)


async def _async_retrieve_and_forward(request: dispatch_pb2.DenseDispatchRequest) -> None:
    query_id = request.query_id
    query = request.query_text.strip()
    top_k = request.top_k or DEFAULT_TOP_K

    if not query:
        logger.info("[%s] Empty dense query; skipping retrieval", query_id)
        return

    assert model is not None
    assert qdrant_client is not None

    try:
        logger.info(
            "[%s] Dispatch accepted | node_a=%s:%s | top_k=%d",
            query_id,
            request.node_a_lan_host,
            request.node_a_grpc_port,
            top_k,
        )
        documents, t_dense_ms = _retrieve_documents(query=query, top_k=top_k, query_id=query_id)
        if not documents:
            logger.warning("[%s] Dense retrieval produced no documents; skipping forward step", query_id)
            return

        logger.info("[%s] Dense retrieval complete in %.1fms (%d docs)", query_id, t_dense_ms, len(documents))

        await forward_dense_results_to_node_a(
            query_id=query_id,
            node_a_lan_host=request.node_a_lan_host,
            node_a_grpc_port=request.node_a_grpc_port,
            documents=documents,
            t_dense_ms=t_dense_ms,
        )
    except Exception as exc:
        logger.exception("[%s] Dense retrieval task failed: %s", query_id, exc)


class DenseDispatcherServicer(dispatch_pb2_grpc.DenseDispatcherServicer):
    async def Dispatch(
        self,
        request: dispatch_pb2.DenseDispatchRequest,
        context: grpc.aio.ServicerContext,
    ) -> dispatch_pb2.DenseDispatchAck:
        logger.info(
            "[%s] Dispatch received | query='%s' | node_a=%s:%s | top_k=%d",
            request.query_id,
            request.query_text[:160],
            request.node_a_lan_host,
            request.node_a_grpc_port,
            request.top_k or DEFAULT_TOP_K,
        )
        
        # Create task and store it to keep it alive
        task = asyncio.create_task(_async_retrieve_and_forward(request))
        active_tasks.add(task)
        
        # Clean up task reference when done
        def task_cleanup(t):
            active_tasks.discard(t)
            if t.cancelled():
                logger.warning("[%s] Task was cancelled", request.query_id)
            elif t.exception():
                logger.error("[%s] Task exception: %s", request.query_id, t.exception())
        
        task.add_done_callback(task_cleanup)
        return dispatch_pb2.DenseDispatchAck(query_id=request.query_id, accepted=True)


async def run_async_server() -> None:
    initialize_globals()
    server = grpc.aio.server(options=[
        ("grpc.http2.min_recv_ping_interval_without_data_ms", 10000),
        ("grpc.http2.max_pings_without_data", 0),
        ("grpc.keepalive_permit_without_calls", 1),
    ])
    warmup_model()
    dispatch_pb2_grpc.add_DenseDispatcherServicer_to_server(DenseDispatcherServicer(), server)
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
