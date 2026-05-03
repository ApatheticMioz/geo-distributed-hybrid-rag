import asyncio
import logging
import sys
import time
import uuid
from typing import Any, Dict, List
from pathlib import Path
from contextlib import asynccontextmanager

import grpc
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse
import uvicorn

from . import config
from .db import get_document_texts
from .fusion import reciprocal_rank_fusion

from vllm.engine.async_llm_engine import AsyncLLMEngine
from vllm.engine.arg_utils import AsyncEngineArgs

# Import gRPC stubs
sys.path.insert(0, str(Path(__file__).parent.parent / "generated"))
import coordination_pb2
import coordination_pb2_grpc

logger = logging.getLogger("node_a")
logging.basicConfig(level=logging.INFO)

# Global vLLM engine
engine: Any = None

# Query state management: query_id -> {sparse_docs, dense_docs, event}
query_state: Dict[str, Dict[str, Any]] = {}


def create_engine():
    """Initialize vLLM engine."""
    global engine
    engine_args = AsyncEngineArgs(
        model=config.MODEL_PATH,
        quantization=config.QUANTIZATION,
        gpu_memory_utilization=config.GPU_MEMORY_UTILIZATION,
    )
    engine = AsyncLLMEngine.from_engine_args(engine_args)
    logger.info("vLLM engine initialized")


async def _llm_stream_generator(prompt: str):
    """Yield text chunks from the vLLM async engine."""
    if engine is None:
        yield "[error] vLLM engine is not initialized."
        return

    try:
        from vllm import SamplingParams  # type: ignore

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
                    chunk = current_text[len(previous_text) :]
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan: startup & shutdown."""
    logger.info("Starting Node A...")
    create_engine()
    yield
    logger.info("Shutting down Node A...")


app = FastAPI(title="Node A — Core Generation Engine & Sync Controller", lifespan=lifespan)


@app.post("/generate")
async def generate(request: Request):
    """HTTP endpoint for backwards compatibility."""
    payload = await request.json()
    sparse = payload.get("sparse", []) or []
    dense = payload.get("dense", None)
    query = payload.get("query")

    if not isinstance(sparse, list):
        raise HTTPException(status_code=400, detail="`sparse` must be a list of doc_ids")

    if query is None:
        raise HTTPException(status_code=400, detail="`query` is required")

    # Synchronization logic: fallback if dense missing/empty
    if not dense:
        logger.warning("`dense` missing or empty — falling back to sparse-only flow")
        fused = list(dict.fromkeys(sparse))  # preserve order, dedupe
    else:
        fused = reciprocal_rank_fusion(sparse, dense, config.RRF_K)

    top_docs = fused[:5]
    context_text = await get_document_texts(top_docs)

    # Build prompt
    system_instruction = (
        "You are a helpful assistant. Use the provided context to answer the user's question."
    )

    prompt = f"System:\n{system_instruction}\n\nContext:\n{context_text}\n\nUser Query:\n{query}\n\nAssistant:"

    # Stream the LLM output back to the client
    return StreamingResponse(_llm_stream_generator(prompt), media_type="text/plain")


# gRPC Servicers

class GenerationOrchestratorServicer(coordination_pb2_grpc.GenerationOrchestratorServicer):
    """Implements bidi stream for Node C -> Node A coordination."""
    
    async def GenerateStream(self, request_iterator, context):
        """
        Bidirectional stream: receive SparseContextRequest, send GenerationToken.
        """
        query_id = None
        query_text = None
        t_sparse_ms = 0
        t_ttft_ms = 0
        t_gen_start = 0
        sparse_docs = []
        dense_docs = []
        node_b_failed = False
        tokens_sent = 0
        
        try:
            # Phase 1: Receive sparse docs from Node C
            logger.info("Waiting for SparseContextRequest from Node C...")
            async for request in request_iterator:
                query_id = request.query_id
                query_text = request.query_text
                t_sparse_ms = request.t_sparse_ms
                node_b_failed = request.node_b_dispatch_failed
                
                sparse_docs = [
                    doc.doc_id for doc in request.docs
                ]
                
                logger.info(
                    f"[{query_id}] Received sparse context: {len(sparse_docs)} docs, "
                    f"node_b_failed={node_b_failed}"
                )
                
                # Merge with any dense docs received from Node B
                dense_doc_ids = [doc for doc in dense_docs]
                fused = reciprocal_rank_fusion(
                    sparse_docs, dense_doc_ids if dense_doc_ids else None, config.RRF_K
                )
                
                top_docs = fused[:5]
                context_text = await get_document_texts(top_docs)
                
                # Build prompt
                system_instruction = (
                    "You are a helpful assistant. Use the provided context to answer the user's question."
                )
                prompt = f"System:\n{system_instruction}\n\nContext:\n{context_text}\n\nUser Query:\n{query_text}\n\nAssistant:"
                
                # Start token generation
                t_gen_start = time.perf_counter()
                logger.info(f"[{query_id}] Starting token generation...")
                
                try:
                    from vllm import SamplingParams
                    
                    sampling_params = SamplingParams(
                        temperature=config.TEMPERATURE,
                        max_tokens=config.MAX_TOKENS,
                    )
                    request_id = uuid.uuid4().hex
                    previous_text = ""
                    
                    async for event in engine.generate(prompt, sampling_params, request_id):
                        try:
                            current_text = ""
                            if hasattr(event, "outputs") and event.outputs:
                                output = event.outputs[0]
                                current_text = getattr(output, "text", str(output))
                            
                            if current_text.startswith(previous_text):
                                chunk = current_text[len(previous_text):]
                            else:
                                chunk = current_text
                            
                            previous_text = current_text
                            
                            if chunk:
                                # Measure TTFT on first token
                                if tokens_sent == 0:
                                    t_ttft_ms = (time.perf_counter() - t_gen_start) * 1000
                                
                                token_msg = coordination_pb2.GenerationToken(
                                    query_id=query_id,
                                    token=chunk,
                                    is_final=False,
                                    ttft_ms=t_ttft_ms if tokens_sent == 0 else 0,
                                )
                                await context.write(token_msg)
                                tokens_sent += 1
                                await asyncio.sleep(0)
                        except Exception as e:
                            logger.error(f"[{query_id}] Error in token generation: {e}")
                    
                    # Send final token
                    final_token = coordination_pb2.GenerationToken(
                        query_id=query_id,
                        token="",
                        is_final=True,
                        ttft_ms=0,
                    )
                    await context.write(final_token)
                    logger.info(f"[{query_id}] Generation complete: {tokens_sent} tokens, TTFT={t_ttft_ms:.1f}ms")
                    
                except Exception as e:
                    logger.error(f"[{query_id}] Error in generation stream: {e}", exc_info=True)
                    raise
                
                break  # Process only the first request (single query per stream)
        
        except Exception as e:
            logger.error(f"[{query_id}] GenerateStream error: {e}", exc_info=True)
            raise


class ResultForwarderServicer(coordination_pb2_grpc.ResultForwarderServicer):
    """Receives dense results from Node B."""
    
    async def ForwardDenseResults(self, request: coordination_pb2.DenseResultForward, context):
        """
        Receive dense results from Node B and store them for later fusion.
        """
        query_id = request.query_id
        t_dense_ms = request.t_dense_ms
        
        logger.info(
            f"[{query_id}] Received {len(request.docs)} dense results from Node B ({t_dense_ms:.1f}ms)"
        )
        
        # Extract dense doc IDs (in order by rank)
        dense_doc_ids = [doc.doc_id for doc in request.docs]
        
        # Store in query state for later use
        if query_id not in query_state:
            query_state[query_id] = {}
        query_state[query_id]["dense_docs"] = dense_doc_ids
        query_state[query_id]["t_dense_ms"] = t_dense_ms
        
        ack = coordination_pb2.DenseResultAck(query_id=query_id, accepted=True)
        return ack


async def run_grpc_server():
    """Start async gRPC server."""
    server = grpc.aio.server()
    coordination_pb2_grpc.add_GenerationOrchestratorServicer_to_server(
        GenerationOrchestratorServicer(), server
    )
    coordination_pb2_grpc.add_ResultForwarderServicer_to_server(
        ResultForwarderServicer(), server
    )
    
    server.add_insecure_port(f"0.0.0.0:50052")
    await server.start()
    logger.info("gRPC server listening on 0.0.0.0:50052")
    await server.wait_for_termination()


def run_servers():
    """Run both HTTP and gRPC servers in async context."""
    async def main():
        # Start gRPC server in background
        grpc_task = asyncio.create_task(run_grpc_server())
        
        # Run FastAPI with uvicorn
        config_uvicorn = uvicorn.Config(
            app=app,
            host="0.0.0.0",
            port=8001,
            log_level="info",
        )
        server = uvicorn.Server(config_uvicorn)
        
        try:
            await server.serve()
        except KeyboardInterrupt:
            logger.info("Shutting down...")
            grpc_task.cancel()
    
    asyncio.run(main())


if __name__ == "__main__":
    run_servers()

