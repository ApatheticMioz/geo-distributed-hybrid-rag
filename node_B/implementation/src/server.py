"""Dense retrieval gRPC server for Node B."""

from concurrent import futures
import logging
import os
import sqlite3
import sys
from pathlib import Path
from typing import Optional

import grpc
import numpy as np
from FlagEmbedding import BGEM3FlagModel
from qdrant_client import QdrantClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import retrieval_pb2  # noqa: E402
import retrieval_pb2_grpc  # noqa: E402

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
DB_PATH = Path(__file__).resolve().parents[2] / "corpus.sqlite"

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


class DenseRetrievalServicer(retrieval_pb2_grpc.DenseRetrievalServiceServicer):
    def Retrieve(
        self,
        request: retrieval_pb2.QueryRequest,
        context: grpc.ServicerContext,
    ) -> retrieval_pb2.RetrievalResponse:
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
    retrieval_pb2_grpc.add_DenseRetrievalServiceServicer_to_server(
        DenseRetrievalServicer(),
        server,
    )
    server.add_insecure_port(f"0.0.0.0:{SERVER_PORT}")
    logger.info("Starting gRPC server on 0.0.0.0:%s", SERVER_PORT)
    server.start()
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
