#!/usr/bin/env python3
"""Smoke test for the pluggable external generation backend.

Streams ONE test completion against config.EXTERNAL_ENDPOINT using the
`openai` python package (OpenAI-compatible chat.completions, stream=True),
mirroring the message semantics used by node_A's external branch, and prints
ttft_ms, n_tokens, and tok/s.

Fail-fast: any connection/HTTP error propagates (no retries, no fallback,
no masking). Run with the node_A venv:

    .venv/bin/python scripts/smoke_backend.py
"""
import asyncio
import sys
import time
from pathlib import Path

# Make `src` importable regardless of CWD.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config  # noqa: E402  (safe: config.py has no vLLM import)


async def main() -> None:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(base_url=config.EXTERNAL_ENDPOINT, api_key="not-needed")

    # Mirror the external branch's message semantics:
    #   system: answer strictly from provided context
    #   user:   query + numbered hydrated passages
    passages = [
        "The 3090 GPU is occupied by the external serving endpoint.",
        "Node A streams generated tokens back over gRPC.",
    ]
    numbered = "\n\n".join(f"[{i}] {p}" for i, p in enumerate(passages, 1))
    system_msg = (
        "You are a helpful assistant. Answer strictly from the provided context. "
        "Do not use any knowledge outside the context. If the answer is not in the "
        "context, say so."
    )
    user_msg = "Query: What is occupying the 3090 GPU?\n\nContext:\n" + numbered

    t0 = time.perf_counter()
    ttft_ms = 0.0
    n_tokens = 0
    first = True

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
        if not event.choices:
            continue
        delta = event.choices[0].delta
        if not (delta and delta.content):
            continue
        if first:
            ttft_ms = (time.perf_counter() - t0) * 1000.0
            first = False
        n_tokens += 1

    total_ms = (time.perf_counter() - t0) * 1000.0
    decode_ms = max(total_ms - ttft_ms, 0.001)
    tps = (n_tokens - 1) / (decode_ms / 1000.0) if n_tokens > 1 else 0.0

    print(f"backend={config.GENERATION_BACKEND}")
    print(f"endpoint={config.EXTERNAL_ENDPOINT}")
    print(f"model={config.EXTERNAL_MODEL}")
    print(f"ttft_ms={ttft_ms:.1f}")
    print(f"n_tokens={n_tokens}")
    print(f"tok/s={tps:.1f}")
    print(f"total_ms={total_ms:.1f}")


if __name__ == "__main__":
    asyncio.run(main())
