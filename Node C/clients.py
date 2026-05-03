import asyncio
import logging
import os
import sys
from typing import List, Dict, Any, AsyncIterator

import grpc

_generated_dir = os.path.join(os.path.dirname(__file__), "generated")
if _generated_dir not in sys.path:
    sys.path.insert(0, _generated_dir)

import coordination_pb2  # noqa: E402
import coordination_pb2_grpc  # noqa: E402
import dispatch_pb2  # noqa: E402
import dispatch_pb2_grpc  # noqa: E402

logger = logging.getLogger(__name__)


class NodeBDispatchClient:
    """
    Sends a fire-and-forget dispatch request to Node B.
    Node B forwards dense results directly to Node A over the local LAN.
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
        self._stub = dispatch_pb2_grpc.DenseDispatcherStub(self._channel)
        logger.info(f"NodeB dispatch channel initialized -> {target}")

    async def dispatch(
        self,
        query_id: str,
        query_text: str,
        top_k: int,
        node_a_lan_host: str,
        node_a_grpc_port: int,
        ack_timeout: float = 2.0,
    ) -> bool:
        request = dispatch_pb2.DenseDispatchRequest(
            query_id=query_id,
            query_text=query_text,
            top_k=top_k,
            node_a_lan_host=node_a_lan_host,
            node_a_grpc_port=node_a_grpc_port,
        )
        try:
            ack = await self._stub.Dispatch(request, timeout=ack_timeout)
            logger.info(f"[{query_id}] Node B ACK received | accepted={ack.accepted}")
            return ack.accepted
        except grpc.aio.AioRpcError as e:
            logger.error(f"[{query_id}] Node B dispatch failed: {e.code()} - {e.details()}")
            return False

    async def close(self):
        await self._channel.close()


class NodeAStreamClient:
    """
    Sends sparse results to Node A via bidirectional gRPC stream and
    receives a server-side token stream back.
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
        self._stub = coordination_pb2_grpc.GenerationOrchestratorStub(self._channel)
        logger.info(f"NodeA stream channel initialized -> {target}")

    async def generate_stream(
        self,
        query_id: str,
        query_text: str,
        sparse_results: List[Dict[str, Any]],
        t_sparse_ms: float,
        dispatch_task: "asyncio.Task[bool]",
    ) -> AsyncIterator[coordination_pb2.GenerationToken]:
        docs = [
            coordination_pb2.RetrievedDocument(
                doc_id=r["doc_id"],
                text=r["text"],
                score=r["score"],
                rank=r["rank"],
            )
            for r in sparse_results
        ]

        initial_request = coordination_pb2.SparseContextRequest(
            query_id=query_id,
            query_text=query_text,
            docs=docs,
            t_sparse_ms=t_sparse_ms,
            node_b_dispatch_failed=False,
        )

        logger.info(
            f"[{query_id}] Opening bidi stream to Node A "
            f"({len(docs)} sparse docs, t_sparse={t_sparse_ms:.1f} ms)"
        )

        async def request_generator():
            yield initial_request

            try:
                ack_accepted = await dispatch_task
            except Exception as exc:
                logger.error(f"[{query_id}] dispatch_task raised: {exc}")
                ack_accepted = False

            if not ack_accepted:
                logger.warning(
                    f"[{query_id}] Node B dispatch failed - sending "
                    "node_b_dispatch_failed=True to Node A"
                )
                yield coordination_pb2.SparseContextRequest(
                    query_id=query_id,
                    query_text=query_text,
                    docs=[],
                    t_sparse_ms=0.0,
                    node_b_dispatch_failed=True,
                )

        try:
            async for token in self._stub.GenerateStream(request_generator()):
                yield token
        except grpc.aio.AioRpcError as e:
            logger.error(f"[{query_id}] Node A stream error: {e.code()} - {e.details()}")
            raise

    async def close(self):
        await self._channel.close()
