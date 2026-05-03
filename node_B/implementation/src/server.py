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
    logger.info("Connecting to Qdrant at %s:%s", QDRANT_HOST, QDRANT_PORT)
    qdrant_client = QdrantClient(
        host=QDRANT_HOST,
        port=QDRANT_PORT,
        grpc_port=QDRANT_GRPC_PORT,
        prefer_grpc=True,
    )


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
        started = time.perf_counter()
        embedding = model.encode([query], return_dense=True)
        query_vector = np.asarray(embedding["dense_vecs"][0], dtype=np.float32).tolist()

        search_response = qdrant_client.query_points(
            collection_name=COLLECTION_NAME,
            query=query_vector,
            limit=top_k,
            with_payload=True,
        )
        search_results = search_response.points

        documents: list[coordination_pb2.RetrievedDocument] = []
        for rank, result in enumerate(search_results, start=1):
            payload = result.payload or {}
            doc_id = str(payload.get("doc_id", result.id))
            documents.append(
                coordination_pb2.RetrievedDocument(
                    doc_id=doc_id,
                    text=_load_text(doc_id),
                    score=float(result.score),
                    rank=rank,
                )
            )

        t_dense_ms = (time.perf_counter() - started) * 1000.0
        logger.info("[%s] Dense retrieval complete in %.1fms (%d docs)", query_id, t_dense_ms, len(documents))

        await forward_dense_results_to_node_a(
            query_id=query_id,
            node_a_lan_host=request.node_a_lan_host,
            node_a_grpc_port=request.node_a_grpc_port,
            documents=documents,
            t_dense_ms=t_dense_ms,
        )
    except Exception as exc:
        logger.exception("[%s] Dense retrieval failed: %s", query_id, exc)


class DenseDispatcherServicer(dispatch_pb2_grpc.DenseDispatcherServicer):
    async def Dispatch(
        self,
        request: dispatch_pb2.DenseDispatchRequest,
        context: grpc.aio.ServicerContext,
    ) -> dispatch_pb2.DenseDispatchAck:
        logger.info(
            "[%s] Dispatch received for Node A %s:%s",
            request.query_id,
            request.node_a_lan_host,
            request.node_a_grpc_port,
        )
        asyncio.create_task(_async_retrieve_and_forward(request))
        return dispatch_pb2.DenseDispatchAck(query_id=request.query_id, accepted=True)


async def run_async_server() -> None:
    initialize_globals()
    server = grpc.aio.server()
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
