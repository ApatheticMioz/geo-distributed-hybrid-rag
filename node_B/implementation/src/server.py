<<<<<<< HEAD
"""
gRPC Server for Node B - Dense Retrieval Engine
Implements DenseDispatcher.Dispatch() for fire-and-forget dispatch + async forwarding to Node A
"""

import asyncio
import logging
import os
import sys
import time
from concurrent import futures
=======
"""Dense retrieval gRPC server for Node B."""

from concurrent import futures
import logging
import os
import sqlite3
import sys
from pathlib import Path
>>>>>>> eb16f7786a9a2df01f287242cf04903c033f7b3f
from typing import Optional
from pathlib import Path

import grpc
import numpy as np
from FlagEmbedding import BGEM3FlagModel
from qdrant_client import QdrantClient

<<<<<<< HEAD
# Import generated proto modules
sys.path.insert(0, str(Path(__file__).parent.parent / "generated"))
import dispatch_pb2
import dispatch_pb2_grpc
import coordination_pb2
import coordination_pb2_grpc
=======
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import retrieval_pb2  # noqa: E402
import retrieval_pb2_grpc  # noqa: E402
>>>>>>> eb16f7786a9a2df01f287242cf04903c033f7b3f

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

<<<<<<< HEAD
# Configuration
QDRANT_HOST = os.environ.get("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.environ.get("QDRANT_PORT", "6333"))
NODE_A_LAN_HOST = os.environ.get("NODE_A_LAN_HOST", "10.8.0.1")
COLLECTION_NAME = "msmarco_passages"
TOP_K = 10
MODEL_NAME = "BAAI/bge-m3"
SERVER_PORT = int(os.environ.get("SERVER_PORT", "50051"))
=======
MODEL_NAME = os.environ.get("BGE_M3_MODEL", "BAAI/bge-m3")
QDRANT_HOST = os.environ.get("QDRANT_HOST", "127.0.0.1")
QDRANT_PORT = int(os.environ.get("QDRANT_PORT", "6333"))
QDRANT_GRPC_PORT = int(os.environ.get("QDRANT_GRPC_PORT", "6334"))
COLLECTION_NAME = os.environ.get("QDRANT_COLLECTION", "msmarco_passages")
SERVER_PORT = int(os.environ.get("NODE_B_GRPC_PORT", "50051"))
DEFAULT_TOP_K = int(os.environ.get("NODE_B_TOP_K", "10"))
DB_PATH = Path(__file__).resolve().parents[2] / "corpus.sqlite"
>>>>>>> eb16f7786a9a2df01f287242cf04903c033f7b3f

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


<<<<<<< HEAD
async def forward_dense_results_to_node_a(
    node_a_lan_host: str,
    node_a_grpc_port: int,
    query_id: str,
    results: list,
    t_dense_ms: float,
):
    """Asynchronously forward dense results to Node A."""
    try:
        target = f"{node_a_lan_host}:{node_a_grpc_port}"
        logger.debug(f"[{query_id}] Forwarding dense results to Node A at {target}")
        
        options = [
            ("grpc.keepalive_time_ms", 10_000),
            ("grpc.keepalive_timeout_ms", 5_000),
            ("grpc.keepalive_permit_without_calls", True),
            ("grpc.http2.max_pings_without_data", 0),
        ]
        async with grpc.aio.insecure_channel(target, options=options) as channel:
            stub = coordination_pb2_grpc.ResultForwarderStub(channel)
            
            # Build DenseResultForward message
            dense_docs = []
            for rank, result in enumerate(results, start=1):
                doc_id = result.payload.get("doc_id", "")
                text = result.payload.get("text", "")
                score = float(result.score)
                doc = coordination_pb2.RetrievedDocument(
                    doc_id=doc_id,
                    text=text,
                    score=score,
                    rank=rank,
                )
                dense_docs.append(doc)
            
            forward_msg = coordination_pb2.DenseResultForward(
                query_id=query_id,
                docs=dense_docs,
                t_dense_ms=t_dense_ms,
            )
            
            ack = await stub.ForwardDenseResults(forward_msg, timeout=5.0)
            logger.info(f"[{query_id}] Dense results forwarded to Node A | accepted={ack.accepted}")
    except Exception as e:
        logger.error(f"[{query_id}] Failed to forward dense results to Node A: {e}")


class DenseDispatcherServicer(dispatch_pb2_grpc.DenseDispatcherServicer):
    """Implements the DenseDispatcher service (fire-and-forget dispatch)."""
    
    def Dispatch(
        self, request: dispatch_pb2.DenseDispatchRequest, context: grpc.ServicerContext
    ) -> dispatch_pb2.DenseDispatchAck:
        """
        Fire-and-forget dispatch: return ACK immediately, compute async, forward to Node A.
        """
        query_id = request.query_id
        query_text = request.query_text
        top_k = request.top_k if request.top_k > 0 else TOP_K
        node_a_lan_host = request.node_a_lan_host or NODE_A_LAN_HOST
        node_a_grpc_port = request.node_a_grpc_port or 50052
        
        logger.info(f"[{query_id}] Dispatch received | query_len={len(query_text)} | top_k={top_k}")
        
        # Return ACK immediately (fire-and-forget)
        ack = dispatch_pb2.DenseDispatchAck(query_id=query_id, accepted=True)
        
        # Launch async task to compute dense retrieval and forward
        asyncio.create_task(self._async_retrieve_and_forward(
            query_id, query_text, top_k, node_a_lan_host, node_a_grpc_port
        ))
        
        return ack
    
    async def _async_retrieve_and_forward(
        self, query_id: str, query_text: str, top_k: int, node_a_lan_host: str, node_a_grpc_port: int
    ):
        """Compute dense retrieval and forward results to Node A."""
        try:
            t_start = time.perf_counter()
            logger.debug(f"[{query_id}] Starting dense retrieval...")
            
            # Encode query
            query_embedding = model.encode([query_text], return_dense=True)
            query_vector = np.array(query_embedding["dense_vecs"][0], dtype=np.float32)
            
            # Search Qdrant
            logger.debug(f"[{query_id}] Searching for top-{top_k} matches in '{COLLECTION_NAME}'...")
            search_results = qdrant_client.search(
                collection_name=COLLECTION_NAME,
                query_vector=query_vector.tolist(),
                limit=top_k,
            )
            
            t_dense_ms = (time.perf_counter() - t_start) * 1000
            logger.info(f"[{query_id}] Dense retrieval complete: {len(search_results)} docs in {t_dense_ms:.1f}ms")
            
            # Forward to Node A
            await forward_dense_results_to_node_a(
                node_a_lan_host, node_a_grpc_port, query_id, search_results, t_dense_ms
            )
        except Exception as e:
            logger.error(f"[{query_id}] Error in async retrieval & forward: {e}", exc_info=True)


def run_async_server():
    """Run async-capable gRPC server."""
    logger.info("Initializing server resources...")
    initialize_globals()
    
    async def start_server():
        logger.info("Starting async gRPC server...")
        server = grpc.aio.server()
        dispatch_pb2_grpc.add_DenseDispatcherServicer_to_server(
            DenseDispatcherServicer(), server
        )
        server.add_insecure_port(f"0.0.0.0:{SERVER_PORT}")
        
        await server.start()
        logger.info(f"gRPC server listening on 0.0.0.0:{SERVER_PORT}")
        
        try:
            await server.wait_for_termination()
        except KeyboardInterrupt:
            logger.info("Shutting down server...")
            await server.stop(0)
            logger.info("Server stopped")
    
    # Run the async server with event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(start_server())


if __name__ == "__main__":
    run_async_server()

        """
        Retrieve top-k documents based on dense similarity.
        
        Args:
            request: QueryRequest containing the query string
            context: gRPC service context
            
        Returns:
            RetrievalResponse with doc_ids and scores
        """
=======
class DenseRetrievalServicer(retrieval_pb2_grpc.DenseRetrievalServicer):
    def Retrieve(
        self,
        request: retrieval_pb2.QueryRequest,
        context: grpc.ServicerContext,
    ) -> retrieval_pb2.RetrievalResponse:
>>>>>>> eb16f7786a9a2df01f287242cf04903c033f7b3f
        try:
            query = request.query.strip()
            top_k = request.top_k or DEFAULT_TOP_K

            if not query:
                return retrieval_pb2.RetrievalResponse(documents=[])

            assert model is not None
            assert qdrant_client is not None

            embedding = model.encode([query], return_dense=True)
            query_vector = np.asarray(embedding["dense_vecs"][0], dtype=np.float32).tolist()

            search_results = qdrant_client.search(
                collection_name=COLLECTION_NAME,
                query_vector=query_vector,
                limit=top_k,
                with_payload=True,
            )

            documents = []
            for rank, result in enumerate(search_results, start=1):
                payload = result.payload or {}
                doc_id = str(payload.get("doc_id", result.id))
                documents.append(
                    retrieval_pb2.RetrievedDocument(
                        doc_id=doc_id,
                        text=_load_text(doc_id),
                        score=float(result.score),
                        rank=rank,
                    )
                )

            return retrieval_pb2.RetrievalResponse(documents=documents)
        except Exception as exc:
            logger.exception("Dense retrieval failed: %s", exc)
            context.set_details(str(exc))
            context.set_code(grpc.StatusCode.INTERNAL)
            return retrieval_pb2.RetrievalResponse(documents=[])


def serve() -> None:
    initialize_globals()
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    retrieval_pb2_grpc.add_DenseRetrievalServicer_to_server(
        DenseRetrievalServicer(),
        server,
    )
    server.add_insecure_port(f"0.0.0.0:{SERVER_PORT}")
    logger.info("Starting gRPC server on 0.0.0.0:%s", SERVER_PORT)
    server.start()
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
