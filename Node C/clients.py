import logging
import os
import sys
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
            ("grpc.keepalive_time_ms", 60_000),
            ("grpc.keepalive_timeout_ms", 10_000),
            ("grpc.keepalive_permit_without_calls", 1),
            ("grpc.http2.max_pings_without_data", 0),
        ]
        self._channel = grpc.aio.insecure_channel(target, options=options)
        self._stub = dispatch_pb2_grpc.DenseDispatcherStub(self._channel)
        self._node_a_lan_host = node_a_lan_host
        self._node_a_grpc_port = node_a_grpc_port
        logger.info(f"NodeB dense channel initialized -> {target}")

    async def retrieve(
        self,
        query_id: str,
        query_text: str,
        top_k: int,
    ) -> bool:
        request = dispatch_pb2.DenseDispatchRequest(
            query_id=query_id,
            query_text=query_text,
            top_k=top_k,
            node_a_lan_host=self._node_a_lan_host,
            node_a_grpc_port=self._node_a_grpc_port,
        )
        try:
            response = await self._stub.Dispatch(request)
            logger.info(
                "[%s] node_b_dispatch_ack accepted=%s",
                query_id,
                response.accepted,
            )
            return bool(response.accepted)
        except grpc.aio.AioRpcError as e:
            logger.error("[%s] node_b_dispatch_failed %s - %s", query_id, e.code(), e.details())
            return False

    async def close(self):
        await self._channel.close()
