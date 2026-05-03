import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import AsyncGenerator, AsyncIterator, Tuple

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from bm25 import TantivyBM25Index
from clients import NodeAStreamClient, NodeBDispatchClient

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
    Node C orchestration logic — dispatch only.

    No timeout logic, no Tthreshold, no speculative prefill decisions.
    Those belong to Node A (the Synchronization Controller).
    """

    def __init__(
        self,
        bm25_index: TantivyBM25Index,
        node_b_client: NodeBDispatchClient,
        node_a_client: NodeAStreamClient,
        top_k: int,
        node_a_lan_host: str,
        node_a_grpc_port: int,
        recorder: LatencyRecorder,
    ):
        self.bm25 = bm25_index
        self.node_b = node_b_client
        self.node_a = node_a_client
        self.top_k = top_k
        self.node_a_lan_host = node_a_lan_host
        self.node_a_grpc_port = node_a_grpc_port
        self.recorder = recorder

    async def handle_query(
        self,
        query_text: str,
        top_k: int | None = None,
        t_request_start: float | None = None,
    ) -> AsyncIterator[Tuple[str, bool, float]]:
        """
        Runs both retrieval branches concurrently.
        Yields (token_text, is_final, ttft_ms) tuples from Node A's stream.

        TTFT is measured on Node C from t_request_start to first token received.
        """
        query_id = str(uuid.uuid4())[:8]
        k = top_k or self.top_k

        t0 = t_request_start if t_request_start is not None else time.perf_counter()

        logger.info(f"[{query_id}] Pipeline start | '{query_text[:60]}'")

        bm25_task = asyncio.create_task(
            asyncio.to_thread(self.bm25.query, query_text, k),
            name=f"{query_id}_bm25",
        )
        dispatch_task = asyncio.create_task(
            self.node_b.dispatch(
                query_id=query_id,
                query_text=query_text,
                top_k=k,
                node_a_lan_host=self.node_a_lan_host,
                node_a_grpc_port=self.node_a_grpc_port,
            ),
            name=f"{query_id}_dispatch",
        )

        sparse_results = await bm25_task
        t_sparse_ms = (time.perf_counter() - t0) * 1000
        logger.info(f"[{query_id}] BM25 done: {t_sparse_ms:.1f} ms | {len(sparse_results)} docs")

        ttft_ms_recorded = None
        first_token = True

        async for token_msg in self.node_a.generate_stream(
            query_id=query_id,
            query_text=query_text,
            sparse_results=sparse_results,
            t_sparse_ms=t_sparse_ms,
            dispatch_task=dispatch_task,
        ):
            if first_token:
                ttft_ms_recorded = (time.perf_counter() - t0) * 1000
                first_token = False
                logger.info(
                    f"[{query_id}] First token received | "
                    f"Edge-TTFT={ttft_ms_recorded:.1f} ms"
                )

            yield token_msg.token, token_msg.is_final, ttft_ms_recorded or 0.0

        t_total_ms = (time.perf_counter() - t0) * 1000
        logger.info(f"[{query_id}] Pipeline complete: {t_total_ms:.1f} ms total")
        self.recorder.record(
            query_id=query_id,
            t_sparse_ms=t_sparse_ms,
            t_total_ms=t_total_ms,
            ttft_ms=ttft_ms_recorded or 0.0,
        )


_orchestrator: PipelineOrchestrator | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _orchestrator
    with open("config.yaml", "r") as f:
        cfg = yaml.safe_load(f)

    bm25_index = TantivyBM25Index(cfg["corpus"]["tantivy_index_path"])
    node_b = NodeBDispatchClient(cfg["node_b"]["host"], cfg["node_b"]["port"])
    node_a = NodeAStreamClient(cfg["node_a"]["grpc_host"], cfg["node_a"]["grpc_port"])

    _orchestrator = PipelineOrchestrator(
        bm25_index=bm25_index,
        node_b_client=node_b,
        node_a_client=node_a,
        top_k=cfg["retrieval"]["top_k"],
        node_a_lan_host=cfg["node_a"]["lan_host"],
        node_a_grpc_port=cfg["node_a"]["grpc_port"],
        recorder=LatencyRecorder(),
    )
    logger.info("Node C gateway ready.")
    yield

    await node_b.close()
    await node_a.close()
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
async def query_endpoint(req: QueryRequest):
    if _orchestrator is None:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    t_request_start = time.perf_counter()

    async def token_stream() -> AsyncGenerator[str, None]:
        try:
            async for token, is_final, _ttft_ms in _orchestrator.handle_query(
                req.query, req.top_k, t_request_start=t_request_start
            ):
                yield token
                if is_final:
                    break
        except Exception as e:
            logger.error(f"Streaming error: {e}", exc_info=True)
            yield f"\n[ERROR: {e}]"

    return StreamingResponse(token_stream(), media_type="text/plain")


@app.get("/health")
async def health():
    return {"status": "ok", "node": "C", "role": "edge_orchestrator"}
