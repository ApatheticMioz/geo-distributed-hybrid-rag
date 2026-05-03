import asyncio
import logging
import os
import sys
import uuid
from typing import List, Dict, Any, AsyncIterator

import grpc
import httpx

_generated_dir = os.path.join(os.path.dirname(__file__), "generated")
if _generated_dir not in sys.path:
    sys.path.insert(0, _generated_dir)

import dispatch_pb2  # noqa: E402
import dispatch_pb2_grpc  # noqa: E402

logger = logging.getLogger(__name__)

class NodeBDenseClient:
    """Dispatches dense retrieval work to Node B."""

    def __init__(self, host: str, port: int, node_a_lan_host: str, node_a_grpc_port: int):
        target = f"{host}:{port}"
        options = [
            ("grpc.keepalive_time_ms", 10_000),
            ("grpc.keepalive_timeout_ms", 5_000),
            ("grpc.keepalive_permit_without_calls", True),
            ("grpc.http2.max_pings_without_data", 0),
        ]
        self._channel = grpc.aio.insecure_channel(target, options=options)
        self._stub = dispatch_pb2_grpc.DenseDispatcherStub(self._channel)
        self._node_a_lan_host = node_a_lan_host
        self._node_a_grpc_port = node_a_grpc_port
        logger.info(f"NodeB dense channel initialized -> {target}")

    async def retrieve(
        self,
        query_text: str,
        top_k: int,
    ) -> List[Dict[str, Any]]:
        request = dispatch_pb2.DenseDispatchRequest(
            query_id=uuid.uuid4().hex,
            query_text=query_text,
            top_k=top_k,
            node_a_lan_host=self._node_a_lan_host,
            node_a_grpc_port=self._node_a_grpc_port,
        )
        try:
            response = await self._stub.Dispatch(request)
            logger.info(
                "Node B dispatch ack for %s -> accepted=%s",
                query_text[:40],
                response.accepted,
            )
            return []
        except grpc.aio.AioRpcError as e:
            logger.error(f"Node B dispatch failed: {e.code()} - {e.details()}")
            return []

    async def close(self):
        await self._channel.close()
