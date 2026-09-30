"""
Node C — Sparse Retrieval + User-Facing Gateway (3-Node Topology).

Role (per systems/node_c/config.yaml, decided 2026-09-18):
  * serves the client directly (user-facing FastAPI gateway), and
  * hosts the Tantivy BM25 sparse index, so the sparse leg — and
    therefore the SPHP sparse hint — originates one hop closer to the
    user. The dense leg + RRF fusion stay on Node B (10.8.0.2:8000);
    generation stays on Node A behind Node B's existing P2 path.

This module is the NC-1 skeleton:
  * config loading from systems/node_c/config.yaml,
  * /health readiness gating on the Tantivy index (reports ok ONLY
    after the index loads — same readiness semantics as Node B),
  * /query and /query/benchmark route contracts with request/response
    models field-compatible with Node B's server.py schemas, so the
    campaign harness stays gateway-agnostic.

The four pipeline legs are separate stub functions raising
NotImplementedError with their contracts documented — they are the
NC-2..NC-4 targets and are intentionally NOT implemented here.
"""

import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List

import tantivy
import yaml
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ============================================================================
# Configuration
# ============================================================================

# config.yaml lives one level above implementation/ (systems/node_c/config.yaml)
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config.yaml"

RRF_K = int(os.environ.get("RRF_K", "60"))

# Top-level keys the gateway contract requires (see config.yaml header).
REQUIRED_TOP_LEVEL_KEYS = (
    "wireguard_ip", "node_b", "node_a", "corpus", "retrieval", "gateway",
)
REQUIRED_SECTION_KEYS = {
    "node_b": ("host", "port"),
    "node_a": ("host", "grpc_port"),
    "corpus": ("tantivy_index_path", "raw_corpus_path"),
    "retrieval": ("top_k", "hint_depth"),
    "gateway": ("host", "port"),
}


class ConfigError(ValueError):
    """Raised when config.yaml is missing, malformed, or incomplete."""


def _validate_config(raw: Any, source: str) -> dict:
    if not isinstance(raw, dict):
        raise ConfigError(
            f"{source}: top level must be a mapping, got {type(raw).__name__}"
        )
    missing = [k for k in REQUIRED_TOP_LEVEL_KEYS if k not in raw]
    if missing:
        raise ConfigError(f"{source}: missing top-level key(s): {', '.join(missing)}")
    for section, keys in REQUIRED_SECTION_KEYS.items():
        sec = raw[section]
        if not isinstance(sec, dict):
            raise ConfigError(f"{source}: section '{section}' must be a mapping")
        missing_sub = [k for k in keys if k not in sec]
        if missing_sub:
            raise ConfigError(
                f"{source}: section '{section}' missing key(s): {', '.join(missing_sub)}"
            )
    return raw


def load_config(path: str | Path) -> dict:
    """
    Load and validate the Node C config.yaml.

    Relative paths (corpus.*) are resolved against the config file's
    directory so the gateway works regardless of CWD.
    """
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    cfg = _validate_config(raw, str(path))
    base = path.resolve().parent
    for key in ("tantivy_index_path", "raw_corpus_path"):
        p = cfg["corpus"][key]
        if not Path(p).is_absolute():
            cfg["corpus"][key] = str(base / p)
    return cfg


# ============================================================================
# Pipeline legs — NC-2..NC-4 stubs (contracts only, not implemented yet)
# ============================================================================

# Lucene/Tantivy special characters that must be escaped in a query string
# before it is handed to tantivy's parse_query. An unescaped special (most
# notably the apostrophe, e.g. "paula deen's") makes parse_query raise and the
# /query endpoint return a 500. Escaping each as a literal keeps the query
# parseable while preserving the intended term.
# (Byte-identical to Node B's bm25_retriever._LUCENE_SPECIALS.)
_LUCENE_SPECIALS = set('+-!(){}[]^"~*?:/\\\'')


def _escape_lucene(query_text: str) -> str:
    """
    Escape Lucene special characters so parse_query treats them as literals.

    Each character in ``_LUCENE_SPECIALS`` is prefixed with a backslash, which
    is Lucene's escape character. Normal (non-special) characters pass through
    unchanged, so ordinary queries are unaffected.
    (Byte-identical to Node B's bm25_retriever._escape_lucene.)
    """
    return ''.join('\\' + ch if ch in _LUCENE_SPECIALS else ch for ch in query_text)


def _bm25_query(index: "tantivy.Index", query_text: str, top_k: int = 10) -> List[Dict[str, Any]]:
    """
    Node C's mirror of Node B's BM25Retriever.query.

    Runs a BM25 search against the (lifespan-loaded) Tantivy index and returns
    at most ``top_k`` results. The result dicts carry byte-identical field
    names to Node B's BM25Retriever.query: ``doc_id``, ``text``, ``score``,
    ``rank``. Empty / whitespace-only queries match nothing and yield ``[]``
    (same as Node B — parse_query on an empty string produces a no-match query).
    """
    searcher = index.searcher()
    query = index.parse_query(
        _escape_lucene(query_text), default_field_names=["body"]
    )
    search_result = searcher.search(query, top_k)
    results_list = search_result.hits if hasattr(search_result, "hits") else search_result

    results = []
    for rank, result in enumerate(results_list, start=1):
        score, doc_address = result
        doc = searcher.doc(doc_address)
        results.append({
            "doc_id": doc.get_first("doc_id"),
            "text": doc.get_first("body"),
            "score": float(score),
            "rank": rank,
        })
    return results


def sparse_retrieve(index: "tantivy.Index", query: str, top_k: int, query_id: str) -> tuple[list[str], float]:
    """
    NC-2 — Local Tantivy BM25 sparse leg (the index lives on C).

    Contract:
      * Runs against the disk-backed Tantivy index at
        config['corpus']['tantivy_index_path'] (loaded at startup and reused
        here — the index is opened once in lifespan, not per request).
      * Returns (doc_id_list, elapsed_ms) with at most top_k ids,
        ranked by BM25 score — the same return shape as Node B's
        _sparse_retrieve so the campaign harness is gateway-agnostic.
      * Must be safe to run in a worker thread (asyncio.to_thread).
    """
    if index is None:
        logger.warning("[%s] BM25 index not available; skipping sparse retrieval", query_id)
        return [], 0.0

    started = time.perf_counter()
    results = _bm25_query(index, query, top_k)

    doc_ids = [r["doc_id"] for r in results if r.get("doc_id")]
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    logger.info(
        "[%s] Sparse retrieval: %.1f ms, %d docs",
        query_id, elapsed_ms, len(doc_ids),
    )
    return doc_ids, elapsed_ms


def dense_forward(query: str, top_k: int, query_id: str) -> tuple[list[str], float]:
    """
    NC-3 — Dense-leg forward to Node B's FastAPI gateway.

    Contract:
      * POSTs the query to config['node_b'] (host:port, 10.8.0.2:8000)
        over the WireGuard mesh and returns (doc_id_list, elapsed_ms)
        in Node B's _dense_retrieve shape.
      * Node B's dense leg (BGE-M3 + Qdrant) stays on B; C only
        forwards and times the round trip.
    """
    raise NotImplementedError("NC-3: dense-leg forward to Node B not yet implemented")


async def sphp_hint_dispatch(
    query_id: str,
    query_text: str,
    sparse_doc_ids: list[str],
    t_sparse_ms: float,
    hint_depth: int,
) -> None:
    """
    NC-3 — SPHP sparse-hint dispatch (the hint originates at C).

    Contract:
      * When req.sphp and mode == 'hybrid', dispatch the top
        config['retrieval']['hint_depth'] sparse ids to Node A's
        GenerationOrchestrator gRPC stream (reached transitively via
        Node B's P2 path) immediately at t_sparse, before the dense
        leg completes — mirroring Node B's stream_to_node_a SPHP step 1.
    """
    raise NotImplementedError("NC-3: SPHP hint dispatch not yet implemented")


def fused_reconcile(sparse_ids: list[str], dense_ids: list[str], rrf_k: int) -> list[str]:
    """
    NC-4 — Fused reconciliation (RRF) of the two legs.

    Contract:
      * Fuses the local sparse ranking with the dense ranking forwarded
        from Node B using reciprocal rank fusion (k = rrf_k, default 60),
        producing the final ranking sent to Node A — the 3-node analogue
        of Node B's local RRF.
      * Returns the fused doc-id list in rank order.
    """
    raise NotImplementedError("NC-4: fused reconciliation not yet implemented")


# ============================================================================
# Request / response models — field-compatible with Node B's server.py
# ============================================================================

class QueryRequest(BaseModel):
    query: str
    top_k: int = 10
    # Retrieval mode: 'hybrid' (both legs + RRF), 'sparse' (BM25 only),
    # 'dense' (BGE-M3 only). Default 'hybrid' preserves prior behavior.
    mode: str = "hybrid"
    # RRF constant k, wired into fused_reconcile. Default 60.
    rrf_k: int = RRF_K
    # Retrieval-only fast path: when true, /query/benchmark skips the
    # generation leg entirely and returns per-mode rankings + retrieval
    # timings with zeroed generation metrics. Default false.
    retrieval_only: bool = False
    # SPHP (Speculative Progressive Hydration & Prefill): dispatch sparse
    # hint early while dense leg runs in background. Default false.
    sphp: bool = False


class BenchmarkResponse(BaseModel):
    query_id: str
    query: str
    top_k: int
    mode: str = "hybrid"
    rrf_k: int = RRF_K
    timings: dict[str, Any]
    fused_doc_ids: list[str]
    sparse_doc_ids: list[str] = []
    dense_doc_ids: list[str] = []
    token_count: int
    decode_tps: float
    answer_preview: str
    # Full generated answer (untruncated) for offline answer-level
    # evaluation; answer_preview stays capped at 120 chars for logs/UI.
    answer_full: str = ""
    sphp: bool = False
    sphp_hit: bool | None = None
    sphp_overlap: float | None = None
    wasted_prefill_ms: float | None = None


# ============================================================================
# App factory
# ============================================================================

def _resolve_mode_and_k(req: "QueryRequest") -> tuple[str, int]:
    """
    Normalize the requested retrieval mode and RRF constant (mirrors
    Node B's _resolve_mode_and_k). Invalid mode raises HTTPException(400);
    rrf_k falls back to the module-level RRF_K when unset or non-positive.
    """
    mode = (req.mode or "hybrid").strip().lower()
    if mode not in ("hybrid", "sparse", "dense"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid mode '{req.mode}'. Must be one of: hybrid, sparse, dense.",
        )
    rrf_k = req.rrf_k if (req.rrf_k is not None and req.rrf_k > 0) else RRF_K
    return mode, rrf_k


def load_tantivy_index(index_path: str) -> "tantivy.Index":
    """
    Open the disk-backed Tantivy index and force a searcher load so
    readiness is only reported after the index is actually usable.
    Raises FileNotFoundError if the index directory does not exist.
    """
    p = Path(index_path)
    if not p.is_dir():
        raise FileNotFoundError(f"Tantivy index not found at {p}")
    idx = tantivy.Index.open(str(p))
    _ = idx.searcher()
    return idx


def create_app(config: dict) -> FastAPI:
    """
    Build the Node C FastAPI app from a validated config dict.

    Readiness semantics (mirrors Node B): /health reports
    {"status":"ok","node":"C"} ONLY after the Tantivy index loads at
    startup; until then it returns 503 {"status":"not_ready","node":"C"}.
    """
    _validate_config(config, "config")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("Starting Node C (sparse_user_facing)...")
        app.state.index = None
        app.state.ready = False
        try:
            app.state.index = load_tantivy_index(config["corpus"]["tantivy_index_path"])
            app.state.ready = True
            logger.info("Tantivy index loaded; gateway is ready.")
        # tantivy-py 0.26 raises builtins.ValueError (not a dedicated
        # TantivyError) when Index.open fails; FileNotFoundError covers
        # our own missing-dir check, OSError covers I/O failures.
        except (FileNotFoundError, OSError, ValueError) as exc:
            logger.warning(
                "Tantivy index unavailable (%s). Gateway stays NOT READY "
                "until the index is built (build_index.py).",
                exc,
            )
        yield
        logger.info("Shutting down Node C...")

    app = FastAPI(
        title="Node C - Sparse Retrieval + User-Facing Gateway",
        version="1.0.0",
        lifespan=lifespan,
    )

    @app.exception_handler(NotImplementedError)
    async def _not_implemented_handler(request, exc: NotImplementedError):
        # Pipeline legs are NC-2..NC-4 stubs; surface them as 501 so the
        # campaign harness can distinguish "not built yet" from 4xx/5xx.
        return JSONResponse(status_code=501, content={"detail": str(exc)})

    @app.get("/health")
    async def health():
        if not app.state.ready:
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "node": "C"},
            )
        return {"status": "ok", "node": "C"}

    @app.post("/query")
    async def query_endpoint(
        req: QueryRequest,
        simulate_wan_delay_ms: int | None = Header(default=None, alias="X-Simulate-WAN-Delay"),
    ):
        """
        Main query endpoint (contract mirrors Node B's /query).
        Skeleton: validates the request, then dispatches the pipeline
        legs (NC-2..NC-4), which are not yet implemented.
        """
        query_id = str(uuid.uuid4())[:8]
        k = req.top_k or int(config["retrieval"]["top_k"])
        query_text = req.query.strip()
        mode, rrf_k = _resolve_mode_and_k(req)
        logger.info(
            "[%s] Pipeline start | mode=%s rrf_k=%d sphp=%s query='%s'",
            query_id, mode, rrf_k, req.sphp, query_text[:60],
        )

        # Pipeline (skeleton order; each leg is an NC-2..NC-4 stub):
        sparse_ids, t_sparse_ms = await asyncio.to_thread(
            sparse_retrieve, app.state.index, query_text, k, query_id
        )
        dense_ids, t_dense_ms = await asyncio.to_thread(
            dense_forward, query_text, k, query_id
        )
        fused_ids = await asyncio.to_thread(
            fused_reconcile, sparse_ids, dense_ids, rrf_k
        )
        if req.sphp and mode == "hybrid":
            await sphp_hint_dispatch(
                query_id=query_id,
                query_text=query_text,
                sparse_doc_ids=sparse_ids,
                t_sparse_ms=t_sparse_ms,
                hint_depth=int(config["retrieval"]["hint_depth"]),
            )
        # Token streaming to the client (via Node B's P2 path) is part of
        # NC-3; the skeleton stops here.
        raise NotImplementedError("NC-3: /query token streaming not yet implemented")

    @app.post("/query/benchmark", response_model=BenchmarkResponse)
    async def benchmark_endpoint(
        req: QueryRequest,
        simulate_wan_delay_ms: int | None = Header(default=None, alias="X-Simulate-WAN-Delay"),
    ):
        """
        Synchronous benchmark endpoint (contract mirrors Node B's
        /query/benchmark): complete structured metrics, token counts,
        and decode throughput as JSON for automated evaluation.
        Skeleton: validates the request, then dispatches the pipeline
        legs (NC-2..NC-4), which are not yet implemented.
        """
        query_id = str(uuid.uuid4())[:8]
        k = req.top_k or int(config["retrieval"]["top_k"])
        query_text = req.query.strip()
        mode, rrf_k = _resolve_mode_and_k(req)
        logger.info(
            "[%s] Benchmark query start | mode=%s rrf_k=%d sphp=%s query='%s'",
            query_id, mode, rrf_k, req.sphp, query_text[:60],
        )

        # Pipeline (skeleton order; each leg is an NC-2..NC-4 stub):
        sparse_ids, t_sparse_ms = await asyncio.to_thread(
            sparse_retrieve, app.state.index, query_text, k, query_id
        )
        dense_ids, t_dense_ms = await asyncio.to_thread(
            dense_forward, query_text, k, query_id
        )
        fused_ids = await asyncio.to_thread(
            fused_reconcile, sparse_ids, dense_ids, rrf_k
        )
        if req.sphp and mode == "hybrid":
            await sphp_hint_dispatch(
                query_id=query_id,
                query_text=query_text,
                sparse_doc_ids=sparse_ids,
                t_sparse_ms=t_sparse_ms,
                hint_depth=int(config["retrieval"]["hint_depth"]),
            )
        # Generation leg (Node A via Node B's P2 path) is part of NC-3;
        # the skeleton stops here.
        raise NotImplementedError("NC-3: /query/benchmark generation leg not yet implemented")

    return app


# Module-level app for `uvicorn src.gateway:app` (index loads in lifespan).
app = create_app(load_config(DEFAULT_CONFIG_PATH))


if __name__ == "__main__":
    import uvicorn

    cfg = load_config(DEFAULT_CONFIG_PATH)
    uvicorn.run(
        app,
        host=cfg["gateway"]["host"],
        port=int(cfg["gateway"]["port"]),
    )
