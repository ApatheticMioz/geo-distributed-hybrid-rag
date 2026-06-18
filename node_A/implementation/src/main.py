import asyncio
import logging
import sys
import time
import uuid
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any, Dict

import grpc
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse
import uvicorn

from . import config
from .db import get_document_texts

# vLLM imports disabled — running in mock mode to avoid GPU OOM
# from vllm.engine.async_llm_engine import AsyncLLMEngine
# from vllm.engine.arg_utils import AsyncEngineArgs

# Import gRPC stubs
sys.path.insert(0, str(Path(__file__).parent.parent / "generated"))
import hybrid_coordination_pb2
import hybrid_coordination_pb2_grpc

logger = logging.getLogger("node_a")
logging.basicConfig(level=logging.INFO)

# Global vLLM engine (None in mock mode)
engine: Any = None

# Background gRPC server task created during FastAPI startup.
grpc_task: asyncio.Task[None] | None = None

# Track which entrypoint owns the gRPC task so we do not double-bind port 50052.
grpc_task_owner: str | None = None


# ============================================================================
# LLM Engine
# ============================================================================

def create_engine():
    """Mock engine initialization — vLLM skipped to avoid GPU OOM."""
    global engine
    engine = None  # No real LLM loaded
    logger.info("MOCK MODE: vLLM engine skipped (GPU OOM prevention)")


# ============================================================================
# FastAPI Lifespan
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan: startup & shutdown."""
    global grpc_task, grpc_task_owner

    logger.info("Starting Node A...")
    create_engine()
    if grpc_task is None:
        grpc_task = asyncio.create_task(run_grpc_server())
        grpc_task_owner = "lifespan"
    yield
    if grpc_task is not None and grpc_task_owner == "lifespan":
        grpc_task.cancel()
        with suppress(asyncio.CancelledError):
            await grpc_task
        grpc_task = None
        grpc_task_owner = None
    logger.info("Shutting down Node A...")


app = FastAPI(title="Node A — Core Generation Engine", lifespan=lifespan)


@app.post("/generate")
async def generate(request: Request):
    """HTTP endpoint for backwards compatibility."""
    payload = await request.json()
    fused_doc_ids = payload.get("fused_doc_ids", [])
    query = payload.get("query")

    if not isinstance(fused_doc_ids, list):
        raise HTTPException(status_code=400, detail="`fused_doc_ids` must be a list")
    if query is None:
        raise HTTPException(status_code=400, detail="`query` is required")

    top_docs = fused_doc_ids[:5]
    context_text = await get_document_texts(top_docs)

    system_instruction = (
        "You are a helpful assistant. Use the provided context to answer the user's question."
    )
    prompt = f"System:\n{system_instruction}\n\nContext:\n{context_text}\n\nUser Query:\n{query}\n\nAssistant:"

    return StreamingResponse(_llm_stream_generator(prompt), media_type="text/plain")


async def _llm_stream_generator(prompt: str):
    """Yield text chunks from the vLLM async engine."""
    if engine is None:
        yield "[error] vLLM engine is not initialized."
        return

    try:
        from vllm import SamplingParams

        sampling_params = SamplingParams(
            temperature=config.TEMPERATURE,
            max_tokens=config.MAX_TOKENS,
        )

        previous_text = ""
        request_id = uuid.uuid4().hex

        async for event in engine.generate(prompt, sampling_params, request_id):
            try:
                current_text = ""
                if hasattr(event, "outputs") and event.outputs:
                    output = event.outputs[0]
                    current_text = getattr(output, "text", str(output))
                else:
                    current_text = str(event)

                if current_text.startswith(previous_text):
                    chunk = current_text[len(previous_text):]
                else:
                    chunk = current_text

                previous_text = current_text

                if chunk:
                    yield chunk
                    await asyncio.sleep(0)
            except Exception:
                yield str(event)
                await asyncio.sleep(0)
    except Exception as exc:
        logger.exception("Error during LLM streaming: %s", exc)
        yield f"[error] {exc}"


# ============================================================================
# gRPC Servicer — receives HybridContextRequest from Node B
# ============================================================================

class GenerationOrchestratorServicer(hybrid_coordination_pb2_grpc.GenerationOrchestratorServicer):
    """
    Implements bidirectional stream for Node B -> Node A coordination.

    Receives pre-fused document list from Node B, hydrates text from SQLite,
    feeds context to vLLM, and streams generated tokens back.
    """

    async def GenerateStream(self, request_iterator, context):
        """Mock generation: hydrates text from SQLite and yields dummy tokens."""
        query_id = None
        tokens_sent = 0

        try:
            async for request in request_iterator:
                query_id = request.query_id
                query_text = request.query_text
                t_sparse_ms = request.t_sparse_ms
                t_dense_ms = request.t_dense_ms

                # Extract pre-fused doc IDs from Node B
                fused_doc_ids = [doc.doc_id for doc in request.fused_docs]

                logger.info(
                    "[%s] fused_docs_received docs=%d t_sparse=%.1fms t_dense=%.1fms",
                    query_id,
                    len(fused_doc_ids),
                    t_sparse_ms,
                    t_dense_ms,
                )

                # Hydrate text from local SQLite (proves database hydration works)
                top_docs = fused_doc_ids[:5]
                context_text = await get_document_texts(top_docs)

                logger.info(
                    "[%s] hydrated %d docs from corpus.sqlite (context_len=%d chars)",
                    query_id, len(top_docs), len(context_text),
                )

                # Build mock response — proves the full pipeline works without GPU
                mock_response = (
                    f"MOCK SUCCESS: Hydrated text for doc IDs: {', '.join(top_docs)}. "
                    f"Context preview: {context_text[:200]}..."
                )

                # Split mock response into "token" chunks and stream them back
                chunk_size = 20
                for i in range(0, len(mock_response), chunk_size):
                    chunk = mock_response[i:i + chunk_size]
                    if chunk:
                        token_msg = hybrid_coordination_pb2.GenerationToken(
                            query_id=query_id,
                            token=chunk,
                            is_final=False,
                            ttft_ms=0.0,
                        )
                        await context.write(token_msg)
                        tokens_sent += 1
                        await asyncio.sleep(0)

                # Send final token
                final_token = hybrid_coordination_pb2.GenerationToken(
                    query_id=query_id,
                    token="",
                    is_final=True,
                    ttft_ms=0.0,
                )
                await context.write(final_token)
                logger.info(
                    "[%s] mock generation complete tokens=%d",
                    query_id,
                    tokens_sent,
                )

                break  # Process only the first request (single query per stream)

        except Exception as e:
            logger.error("[%s] GenerateStream error: %s", query_id, e, exc_info=True)
            raise


# ============================================================================
# gRPC Server
# ============================================================================

async def run_grpc_server():
    """Start async gRPC server."""
    server = grpc.aio.server()
    hybrid_coordination_pb2_grpc.add_GenerationOrchestratorServicer_to_server(
        GenerationOrchestratorServicer(), server
    )

    port = server.add_insecure_port("0.0.0.0:50052")
    if port == 0:
        raise RuntimeError("Failed to bind Node A gRPC server to port 50052")
    await server.start()
    logger.info("gRPC server listening on 0.0.0.0:50052")
    try:
        await server.wait_for_termination()
    finally:
        await server.stop(0)


def run_servers():
    """Run both HTTP and gRPC servers in async context."""
    async def main():
        global grpc_task, grpc_task_owner

        if grpc_task is None:
            grpc_task = asyncio.create_task(run_grpc_server())
            grpc_task_owner = "run_servers"

        config_uvicorn = uvicorn.Config(
            app=app,
            host="0.0.0.0",
            port=8001,
            log_level="info",
        )
        server = uvicorn.Server(config_uvicorn)

        try:
            await server.serve()
        finally:
            if grpc_task is not None and grpc_task_owner == "run_servers":
                grpc_task.cancel()
                with suppress(asyncio.CancelledError):
                    await grpc_task
                grpc_task = None
                grpc_task_owner = None

    asyncio.run(main())


if __name__ == "__main__":
    run_servers()