import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import AsyncGenerator, AsyncIterator
import sys

import grpc
import httpx
import yaml
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from bm25 import TantivyBM25Index
from clients import NodeBDenseClient

_generated_dir = Path(__file__).parent / "generated"
if str(_generated_dir) not in sys.path:
    sys.path.insert(0, str(_generated_dir))

import coordination_pb2  # noqa: E402
import coordination_pb2_grpc  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class LatencyRecord:
    query_id: str
    t_sparse_ms: float
    t_total_ms: float
    ttft_ms: float
    mode: str = "parallel"  # "sequential" or "parallel" for benchmarking
    timestamp: float = field(default_factory=time.time)


class LatencyRecorder:
    def __init__(self, log_path: str = "logs/latency_nodeC.jsonl"):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, **kwargs):
        rec = LatencyRecord(**kwargs)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(rec)) + "\n")
        logger.debug(f"Latency recorded: {asdict(rec)}")


class PipelineOrchestrator:
    """
    Node C orchestration logic — executes sparse and dense retrieval,
    then forwards to Node A via async HTTP POST.
    Includes the dynamic timeout fallback (T_threshold = 150ms).
    """

    def __init__(
        self,
        bm25_index: TantivyBM25Index,
        node_b_client: NodeBDenseClient,
        top_k: int,
        node_a_grpc_host: str,
        node_a_grpc_port: int,
        recorder: LatencyRecorder,
    ):
        self.bm25 = bm25_index
        self.node_b = node_b_client
        self.top_k = top_k
        self.node_a_grpc_host = node_a_grpc_host
        self.node_a_grpc_port = node_a_grpc_port
        self.recorder = recorder

    async def handle_query(
        self,
        query_text: str,
        top_k: int | None = None,
        t_request_start: float | None = None,
        mode: str = "parallel",
        simulate_wan_delay_ms: int = 0,
    ) -> AsyncIterator[str]:
        query_id = str(uuid.uuid4())[:8]
        k = top_k or self.top_k
        normalized_mode = mode.lower().strip()
        delay_seconds = max(simulate_wan_delay_ms, 0) / 1000.0
        dense_task = None

        t0 = t_request_start if t_request_start is not None else time.perf_counter()

        logger.info(f"[{query_id}] Pipeline start ({normalized_mode}) | '{query_text[:60]}'")

        async def dense_retrieve() -> bool:
            if delay_seconds > 0:
                logger.info(
                    f"[{query_id}] Simulating WAN delay before Node B dial: {delay_seconds * 1000:.0f} ms"
                )
                await asyncio.sleep(delay_seconds)
            return await self.node_b.retrieve(query_id=query_id, query_text=query_text, top_k=k)

        if normalized_mode == "sequential":
            sparse_results = await asyncio.to_thread(self.bm25.query, query_text, k)
            t_sparse_ms = (time.perf_counter() - t0) * 1000
            logger.info(f"[{query_id}] BM25 done: {t_sparse_ms:.1f} ms | {len(sparse_results)} docs")

            dense_start = time.perf_counter()
            try:
                node_b_dispatch_failed = not bool(await dense_retrieve())
            except Exception as exc:
                logger.error(f"[{query_id}] Dense retrieval failed: {exc}", exc_info=True)
                node_b_dispatch_failed = True
            dense_ms = (time.perf_counter() - dense_start) * 1000
            logger.info(f"[{query_id}] Node B dispatch complete: {dense_ms:.1f} ms")
        elif normalized_mode == "parallel":
            bm25_task = asyncio.create_task(
                asyncio.to_thread(self.bm25.query, query_text, k),
                name=f"{query_id}_bm25",
            )
            dense_task = asyncio.create_task(dense_retrieve(), name=f"{query_id}_dense")

            sparse_results = await bm25_task
            t_sparse_ms = (time.perf_counter() - t0) * 1000
            logger.info(f"[{query_id}] BM25 done: {t_sparse_ms:.1f} ms | {len(sparse_results)} docs")

            node_b_dispatch_failed = False
            if dense_task.done():
                try:
                    node_b_dispatch_failed = not bool(await dense_task)
                except Exception:
                    node_b_dispatch_failed = True
        else:
            raise ValueError(f"Unsupported query mode: {mode}")

        sparse_docs = [
            coordination_pb2.RetrievedDocument(
                doc_id=str(doc.get("doc_id", "")),
                text=str(doc.get("text", "")),
                score=float(doc.get("score", 0.0)),
                rank=int(doc.get("rank", index + 1)),
            )
            for index, doc in enumerate(sparse_results)
            if doc.get("doc_id")
        ]

        first_token = True
        ttft_ms_recorded = None

        async def request_iterator():
            yield coordination_pb2.SparseContextRequest(
                query_id=query_id,
                query_text=query_text,
                docs=sparse_docs,
                t_sparse_ms=t_sparse_ms,
                node_b_dispatch_failed=node_b_dispatch_failed,
            )

        target = f"{self.node_a_grpc_host}:{self.node_a_grpc_port}"
        async with grpc.aio.insecure_channel(target) as channel:
            stub = coordination_pb2_grpc.GenerationOrchestratorStub(channel)
            try:
                async for token in stub.GenerateStream(request_iterator()):
                    if token.is_final:
                        break

                    if first_token:
                        ttft_ms_recorded = (time.perf_counter() - t0) * 1000
                        first_token = False
                        logger.info(
                            f"[{query_id}] First token received | "
                            f"Edge-TTFT={ttft_ms_recorded:.1f} ms"
                        )

                    if token.token:
                        yield token.token
            except grpc.aio.AioRpcError as exc:
                yield f"[ERROR] Node A gRPC stream failed: {exc.code()} - {exc.details()}"
                return

        if dense_task is not None and not dense_task.done():
            dense_task.cancel()
            with suppress(asyncio.CancelledError):
                await dense_task

        t_total_ms = (time.perf_counter() - t0) * 1000
        logger.info(f"[{query_id}] Pipeline complete: {t_total_ms:.1f} ms total")
        self.recorder.record(
            query_id=query_id,
            t_sparse_ms=t_sparse_ms,
            t_total_ms=t_total_ms,
            ttft_ms=ttft_ms_recorded or 0.0,
            mode=normalized_mode,
        )


_orchestrator: PipelineOrchestrator | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _orchestrator
    with open("config.yaml", "r") as f:
        cfg = yaml.safe_load(f)

    bm25_index = TantivyBM25Index(cfg["corpus"]["tantivy_index_path"])
    
    # As per prompt IP requirements:
    node_b_host = "10.8.0.5"
    node_b_port = 50051
    node_a_lan_host = cfg["node_a"]["lan_host"]
    node_a_grpc_port = cfg["node_a"]["grpc_port"]
    node_b = NodeBDenseClient(node_b_host, node_b_port, node_a_lan_host, node_a_grpc_port)

    node_a_grpc_host = cfg["node_a"]["grpc_host"]

    _orchestrator = PipelineOrchestrator(
        bm25_index=bm25_index,
        node_b_client=node_b,
        top_k=cfg["retrieval"]["top_k"],
        node_a_grpc_host=node_a_grpc_host,
        node_a_grpc_port=node_a_grpc_port,
        recorder=LatencyRecorder(),
    )
    logger.info("Node C gateway ready.")
    yield

    await node_b.close()
    logger.info("Node C gateway shut down.")


app = FastAPI(
    title="Node C - Edge Orchestrator",
    version="3.0.0",
    lifespan=lifespan,
)


class QueryRequest(BaseModel):
    query: str
    top_k: int = 10


@app.post("/query")
async def query_endpoint(
    req: QueryRequest,
    mode: str = Query("parallel"),
    simulate_wan_delay_ms: int | None = Header(default=None, alias="X-Simulate-WAN-Delay"),
):
    if _orchestrator is None:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    normalized_mode = mode.lower().strip()
    if normalized_mode not in {"parallel", "sequential"}:
        raise HTTPException(status_code=400, detail="mode must be 'parallel' or 'sequential'")

    wan_delay_ms = max(simulate_wan_delay_ms or 0, 0)

    t_request_start = time.perf_counter()

    async def token_stream() -> AsyncGenerator[str, None]:
        try:
            async for token in _orchestrator.handle_query(
                req.query,
                req.top_k,
                t_request_start=t_request_start,
                mode=normalized_mode,
                simulate_wan_delay_ms=wan_delay_ms,
            ):
                yield token
        except Exception as e:
            logger.error(f"Streaming error: {e}", exc_info=True)
            yield f"\n[ERROR: {e}]"

    return StreamingResponse(token_stream(), media_type="text/plain")


@app.get("/health")
async def health():
    return {"status": "ok", "node": "C", "role": "edge_orchestrator"}
