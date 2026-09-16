import os
os.environ["FLASHINFER_DISABLE_VERSION_CHECK"] = "1"
os.environ["VLLM_USE_V1"] = "0"
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

# vLLM imports are guarded so the server can start even if vLLM is unavailable.
try:
    from vllm.engine.async_llm_engine import AsyncLLMEngine
    from vllm.engine.arg_utils import AsyncEngineArgs
    VLLM_AVAILABLE = True
except ImportError:
    VLLM_AVAILABLE = False

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
    """Initialize the vLLM engine only for the "vllm" backend (or skip in mock mode).

    The "external" backend never initializes vLLM and never touches the GPU.
    """
    global engine
    if config.MOCK_MODE:
        logger.warning("MOCK MODE: Skipping vLLM/AWQ model loading. Responses will be simulated.")
        engine = None
        return
    if config.GENERATION_BACKEND != "vllm":
        logger.info(
            "Generation backend is '%s'; skipping vLLM engine init (no GPU).",
            config.GENERATION_BACKEND,
        )
        engine = None
        return
    if not VLLM_AVAILABLE:
        logger.warning("vLLM not available. Running in mock mode.")
        engine = None
        return
    logger.info(f"Loading vLLM engine from {config.MODEL_PATH}...")
    
    engine_args = AsyncEngineArgs(
        model=config.MODEL_PATH,
        quantization="awq",
        enforce_eager=True,  # Conserves VRAM as per your paper
        gpu_memory_utilization=0.90,
        max_model_len=config.MAX_MODEL_LEN
    )
    engine = AsyncLLMEngine.from_engine_args(engine_args)
    logger.info("vLLM engine loaded successfully.")

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

    return StreamingResponse(_llm_stream_generator(prompt, context_text, query), media_type="text/plain")


async def _external_stream_generator(context_text: str, query: str):
    """Stream text chunks from an external OpenAI-compatible endpoint.

    Uses the `openai` python package against `config.EXTERNAL_ENDPOINT` with
    `stream=True`. Messages mirror the existing prompt semantics:
      - system: answer strictly from the provided context
      - user:   the query plus the numbered, hydrated passages

    Sampling reuses the existing config (temperature / max_tokens).

    Fail-fast: connection/HTTP errors propagate as exceptions. We never
    synthesize tokens, and there are no retries, fallbacks, or masking.
    """
    from openai import AsyncOpenAI

    client = AsyncOpenAI(base_url=config.EXTERNAL_ENDPOINT, api_key="not-needed")

    # Number the hydrated passages so the model can cite them.
    passages = [p.strip() for p in context_text.split("\n\n") if p.strip()]
    numbered = "\n\n".join(f"[{i}] {p}" for i, p in enumerate(passages, 1))

    system_msg = (
        "You are a helpful assistant. Answer strictly from the provided context. "
        "Do not use any knowledge outside the context. If the answer is not in the "
        "context, say so."
    )
    user_msg = f"Query: {query}\n\nContext:\n{numbered}"

    stream = await client.chat.completions.create(
        model=config.EXTERNAL_MODEL,
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        temperature=config.TEMPERATURE,
        max_tokens=config.MAX_TOKENS,
        stream=True,
    )

    async for event in stream:
        if event.choices:
            delta = event.choices[0].delta
            if delta and delta.content:
                yield delta.content
                await asyncio.sleep(0)


async def _llm_stream_generator(prompt: str, context_text: str = "", query: str = ""):
    """Yield text chunks from the active generation backend (or mock response).

    Backends:
      - "external": OpenAI-compatible endpoint via the `openai` package.
      - "vllm":     in-process vLLM async engine.
    """
    if config.MOCK_MODE:
        logger.info("MOCK MODE: Returning simulated response.")
        mock_response = (
            "[MOCK RESPONSE] This is a simulated response from Node A. "
            "The AWQ model was not loaded (mock mode is active). "
            "In production, this would contain the LLM-generated answer based on the provided context.\n"
            f"Query processed at: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Context length: {len(prompt)} characters"
        )
        for chunk in mock_response:
            yield chunk
            if chunk == '\n':
                continue
            await asyncio.sleep(0.01)
        return

    if config.GENERATION_BACKEND == "external":
        # External OpenAI-compatible endpoint. Fail-fast: any connection/HTTP
        # error propagates to the caller; we never synthesize tokens.
        async for chunk in _external_stream_generator(context_text, query):
            yield chunk
            await asyncio.sleep(0)
        return

    # vLLM in-process engine branch.
    if engine is None:
        logger.info("vLLM engine unavailable; returning simulated response.")
        mock_response = (
            "[MOCK RESPONSE] This is a simulated response from Node A. "
            "The AWQ model was not loaded (vLLM unavailable). "
            "In production, this would contain the LLM-generated answer based on the provided context.\n"
            f"Query processed at: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Context length: {len(prompt)} characters"
        )
        for chunk in mock_response:
            yield chunk
            if chunk == '\n':
                continue
            await asyncio.sleep(0.01)
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

    Receives pre-fused document list from Node B (or provisional sparse hint in SPHP mode),
    hydrates text from SQLite, feeds context to LLM, and streams generated tokens back.
    """

    async def GenerateStream(self, request_iterator, context):
        """Streams LLM tokens, supporting SPHP progressive prefill and reconciliation."""
        query_id = None
        tokens_sent = 0
        spec_task = None
        spec_queue = None
        spec_start = 0.0
        provisional_docs = []
        sphp_hit = False
        sphp_overlap = 0.0
        wasted_prefill_ms = 0.0

        try:
            async for request in request_iterator:
                query_id = request.query_id
                query_text = request.query_text
                t_sparse_ms = request.t_sparse_ms
                t_dense_ms = request.t_dense_ms

                if request.is_sparse_hint:
                    # Message 1: SPHP Sparse Hint
                    provisional_docs = [doc.doc_id for doc in request.fused_docs]
                    spec_start = time.perf_counter()
                    logger.info("[%s] SPHP sparse_hint received: %d docs (t_sparse=%.1fms)",
                                query_id, len(provisional_docs), t_sparse_ms)

                    # Hydrate provisional context
                    top_sparse = provisional_docs[:5]
                    sparse_context = await get_document_texts(top_sparse)
                    system_instruction = "You are a helpful assistant. Use the provided context to answer the user's question."
                    sparse_prompt = f"System:\n{system_instruction}\n\nContext:\n{sparse_context}\n\nUser Query:\n{query_text}\n\nAssistant:"

                    spec_queue = asyncio.Queue()

                    async def _spec_worker(p, c, q):
                        try:
                            async for chunk in _llm_stream_generator(p, c, q):
                                await spec_queue.put(("chunk", chunk))
                        except asyncio.CancelledError:
                            raise
                        except Exception as exc:
                            await spec_queue.put(("error", exc))
                        finally:
                            await spec_queue.put(("done", None))

                    spec_task = asyncio.create_task(_spec_worker(sparse_prompt, sparse_context, query_text))
                    continue

                # Message 2 (or single message): Final fused context
                fused_doc_ids = [doc.doc_id for doc in request.fused_docs]
                top_docs = fused_doc_ids[:5]

                if spec_task is not None and not spec_task.done():
                    # Reconcile SPHP speculative prefill with final fused docs
                    intersection = set(provisional_docs[:5]) & set(top_docs)
                    sphp_overlap = len(intersection) / 5.0

                    if sphp_overlap >= 0.5:
                        sphp_hit = True
                        logger.info("[%s] SPHP HIT! overlap=%.2f (matched %d/5 docs)",
                                    query_id, sphp_overlap, len(intersection))
                    else:
                        sphp_hit = False
                        wasted_prefill_ms = (time.perf_counter() - spec_start) * 1000.0
                        spec_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await spec_task
                        spec_task = None
                        spec_queue = None
                        logger.info("[%s] SPHP MISS! overlap=%.2f. Aborted prefill after %.1fms",
                                    query_id, sphp_overlap, wasted_prefill_ms)
                elif spec_task is not None and spec_task.done():
                    intersection = set(provisional_docs[:5]) & set(top_docs)
                    sphp_overlap = len(intersection) / 5.0
                    if sphp_overlap >= 0.5:
                        sphp_hit = True
                    else:
                        sphp_hit = False
                        wasted_prefill_ms = (time.perf_counter() - spec_start) * 1000.0
                        spec_task = None
                        spec_queue = None

                start_time = time.perf_counter()
                first_token = True
                ttft_ms = 0.0

                if sphp_hit and spec_queue is not None:
                    # Stream tokens out of speculative queue
                    while True:
                        msg_type, val = await spec_queue.get()
                        if msg_type == "done":
                            break
                        if msg_type == "error":
                            logger.error("[%s] Error in speculative LLM stream: %s", query_id, val)
                            break
                        if first_token:
                            ttft_ms = (time.perf_counter() - start_time) * 1000.0
                            first_token = False
                        else:
                            ttft_ms = 0.0

                        token_msg = hybrid_coordination_pb2.GenerationToken(
                            query_id=query_id,
                            token=val,
                            is_final=False,
                            ttft_ms=ttft_ms,
                            sphp_hit=True,
                            sphp_overlap=sphp_overlap,
                            wasted_prefill_ms=0.0,
                        )
                        await context.write(token_msg)
                        tokens_sent += 1
                else:
                    # Direct generation with final fused context
                    t_hyd_start = time.perf_counter()
                    context_text = await get_document_texts(top_docs)
                    t_hydration_ms = (time.perf_counter() - t_hyd_start) * 1000.0

                    system_instruction = "You are a helpful assistant. Use the provided context to answer the user's question."
                    prompt = f"System:\n{system_instruction}\n\nContext:\n{context_text}\n\nUser Query:\n{query_text}\n\nAssistant:"

                    async for chunk in _llm_stream_generator(prompt, context_text, query_text):
                        if first_token:
                            ttft_ms = (time.perf_counter() - start_time) * 1000.0
                            first_token = False
                        else:
                            ttft_ms = 0.0

                        token_msg = hybrid_coordination_pb2.GenerationToken(
                            query_id=query_id,
                            token=chunk,
                            is_final=False,
                            ttft_ms=ttft_ms,
                            sphp_hit=sphp_hit,
                            sphp_overlap=sphp_overlap,
                            wasted_prefill_ms=wasted_prefill_ms,
                        )
                        await context.write(token_msg)
                        tokens_sent += 1

                total_gen_ms = (time.perf_counter() - start_time) * 1000.0
                decode_ms = max(total_gen_ms - ttft_ms, 0.001)
                tps = (tokens_sent - 1) / (decode_ms / 1000.0) if tokens_sent > 1 else 0.0

                # Send final sentinel token
                final_token = hybrid_coordination_pb2.GenerationToken(
                    query_id=query_id,
                    token="",
                    is_final=True,
                    ttft_ms=0.0,
                    sphp_hit=sphp_hit,
                    sphp_overlap=sphp_overlap,
                    wasted_prefill_ms=wasted_prefill_ms,
                )
                await context.write(final_token)

                logger.info(
                    "[%s] live generation complete tokens=%d ttft=%.1fms decode=%.1fms throughput=%.1f tps sphp_hit=%s overlap=%.2f",
                    query_id, tokens_sent, ttft_ms, decode_ms, tps, sphp_hit, sphp_overlap,
                )
                break  # Process only the first request sequence

        except Exception as e:
            logger.error("[%s] GenerateStream error: %s", query_id, e, exc_info=True)
            if spec_task and not spec_task.done():
                spec_task.cancel()
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