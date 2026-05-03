import asyncio
import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse

from . import config
from .db import get_document_texts
from .fusion import reciprocal_rank_fusion

from vllm.engine.async_llm_engine import AsyncLLMEngine
from vllm.engine.arg_utils import AsyncEngineArgs

logger = logging.getLogger("node_a")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Node A — Core Generation Engine & Sync Controller")

engine_args = AsyncEngineArgs(
    model=config.MODEL_PATH,
    quantization=config.QUANTIZATION,
    gpu_memory_utilization=config.GPU_MEMORY_UTILIZATION,
)
engine: Any = AsyncLLMEngine.from_engine_args(engine_args)


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
    except Exception as exc:  # pragma: no cover - runtime dependent
        logger.exception("Error during LLM streaming: %s", exc)
        yield f"[error] {exc}"


@app.post("/generate")
async def generate(request: Request):
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

    # Build prompt (system + context + user query)
    system_instruction = (
        "You are a helpful assistant. Use the provided context to answer the user's question."
    )

    prompt = f"System:\n{system_instruction}\n\nContext:\n{context_text}\n\nUser Query:\n{query}\n\nAssistant:"

    # Stream the LLM output back to the client
    return StreamingResponse(_llm_stream_generator(prompt), media_type="text/plain")

if __name__ == "__main__":
    import uvicorn
    import os
    port = int(os.environ.get("PORT", 8001))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
