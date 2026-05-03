import asyncio
import logging
import os
import sys
from typing import List, Dict, Any, AsyncIterator

import grpc
import httpx

_generated_dir = os.path.join(os.path.dirname(__file__), "generated")
if _generated_dir not in sys.path:
    sys.path.insert(0, _generated_dir)

import retrieval_pb2  # noqa: E402
import retrieval_pb2_grpc  # noqa: E402

logger = logging.getLogger(__name__)

class NodeBDenseClient:
    """
    Sends a query to Node B and receives a list of matched documents.
    """

    def __init__(self, host: str, port: int):
        target = f"{host}:{port}"
        options = [
            ("grpc.keepalive_time_ms", 10_000),
            ("grpc.keepalive_timeout_ms", 5_000),
            ("grpc.keepalive_permit_without_calls", True),
            ("grpc.http2.max_pings_without_data", 0),
        ]
        self._channel = grpc.aio.insecure_channel(target, options=options)
        self._stub = retrieval_pb2_grpc.DenseRetrievalServiceStub(self._channel)
        logger.info(f"NodeB dense channel initialized -> {target}")

    async def retrieve(
        self,
        query_text: str,
        top_k: int,
    ) -> List[Dict[str, Any]]:
        request = retrieval_pb2.QueryRequest(
            query=query_text,
            top_k=top_k,
        )
        try:
            response = await self._stub.Retrieve(request)
            return [
                {
                    "doc_id": doc.doc_id,
                    "text": doc.text,
                    "score": doc.score,
                    "rank": doc.rank
                }
                for doc in response.documents
            ]
        except grpc.aio.AioRpcError as e:
            logger.error(f"Node B retrieve failed: {e.code()} - {e.details()}")
            return []

    async def close(self):
        await self._channel.close()
