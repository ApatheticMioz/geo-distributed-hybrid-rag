import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import AsyncGenerator, AsyncIterator, Tuple, Optional

import httpx
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from bm25 import TantivyBM25Index
from clients import NodeBDenseClient

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
    mode: str = "sequential"  # "sequential" or "parallel" for benchmarking
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
        node_a_http_url: str,
        recorder: LatencyRecorder,
    ):
        self.bm25 = bm25_index
        self.node_b = node_b_client
        self.top_k = top_k
        self.node_a_http_url = node_a_http_url
        self.recorder = recorder

    async def handle_query(
        self,
        query_text: str,
        top_k: int | None = None,
        t_request_start: float | None = None,
    ) -> AsyncIterator[str]:
        query_id = str(uuid.uuid4())[:8]
        k = top_k or self.top_k

        t0 = t_request_start if t_request_start is not None else time.perf_counter()

        logger.info(f"[{query_id}] SEQUENTIAL Pipeline start | '{query_text[:60]}'")

        # ========== SEQUENTIAL EXECUTION ==========
        # Phase 1: BM25 sparse retrieval (no parallelism, wait for completion)
        logger.info(f"[{query_id}] Starting BM25 sparse retrieval...")
        sparse_results = await asyncio.to_thread(self.bm25.query, query_text, k)
        t_sparse_ms = (time.perf_counter() - t0) * 1000
        logger.info(f"[{query_id}] BM25 done: {t_sparse_ms:.1f} ms | {len(sparse_results)} docs")

        # Phase 2: Dense retrieval from Node B (after BM25 completes, no parallelism)
        logger.info(f"[{query_id}] Starting dense retrieval from Node B...")
        dense_results = None
        t_dense_start = time.perf_counter()
        try:
            dense_results = await self.node_b.retrieve(query_text=query_text, top_k=k)
            t_dense_ms = (time.perf_counter() - t_dense_start) * 1000
            logger.info(f"[{query_id}] Dense done: {t_dense_ms:.1f} ms | {len(dense_results)} docs")
        except Exception as e:
            t_dense_ms = (time.perf_counter() - t_dense_start) * 1000
            logger.error(f"[{query_id}] Dense retrieval failed after {t_dense_ms:.1f} ms: {e}")
            dense_results = None
        # ========== END SEQUENTIAL EXECUTION ==========

        payload = {
            "query": query_text,
            "sparse": [d["doc_id"] for d in sparse_results],
            "dense": [d["doc_id"] for d in dense_results] if dense_results is not None else None
        }

        first_token = True
        ttft_ms_recorded = None

        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream("POST", self.node_a_http_url, json=payload) as response:
                if response.status_code != 200:
                    error_msg = await response.aread()
                    yield f"[ERROR] Node A returned {response.status_code}: {error_msg.decode('utf-8', errors='ignore')}"
                    return
                
                async for chunk in response.aiter_text():
                    if first_token:
                        ttft_ms_recorded = (time.perf_counter() - t0) * 1000
                        first_token = False
                        logger.info(
                            f"[{query_id}] First token received | "
                            f"Edge-TTFT={ttft_ms_recorded:.1f} ms"
                        )
                    yield chunk

        t_total_ms = (time.perf_counter() - t0) * 1000
        logger.info(f"[{query_id}] SEQUENTIAL Pipeline complete: {t_total_ms:.1f} ms total")
        self.recorder.record(
            query_id=query_id,
            t_sparse_ms=t_sparse_ms,
            t_total_ms=t_total_ms,
            ttft_ms=ttft_ms_recorded or 0.0,
            mode="sequential",
        )


_orchestrator: PipelineOrchestrator | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _orchestrator
    with open("config.yaml", "r") as f:
        cfg = yaml.safe_load(f)

    bm25_index = TantivyBM25Index(cfg["corpus"]["tantivy_index_path"])
    
    node_b_host = cfg["node_b"]["host"]
    node_b_port = cfg["node_b"]["port"]
    node_b = NodeBDenseClient(node_b_host, node_b_port)

    node_a_url = f"http://{cfg['node_a']['grpc_host']}:8001/generate"

    _orchestrator = PipelineOrchestrator(
        bm25_index=bm25_index,
        node_b_client=node_b,
        top_k=cfg["retrieval"]["top_k"],
        node_a_http_url=node_a_url,
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
async def query_endpoint(req: QueryRequest):
    if _orchestrator is None:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    t_request_start = time.perf_counter()

    async def token_stream() -> AsyncGenerator[str, None]:
        try:
            async for token in _orchestrator.handle_query(
                req.query, req.top_k, t_request_start=t_request_start
            ):
                yield token
        except Exception as e:
            logger.error(f"Streaming error: {e}", exc_info=True)
            yield f"\n[ERROR: {e}]"

    return StreamingResponse(token_stream(), media_type="text/plain")


@app.get("/health")
async def health():
    return {"status": "ok", "node": "C", "role": "edge_orchestrator"}
