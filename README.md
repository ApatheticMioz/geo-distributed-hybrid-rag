# Geo-Distributed RAG (Node C, Node B, Node A)

This project implements a three-node Retrieval-Augmented Generation (RAG) pipeline designed to reduce time-to-first-token by overlapping sparse and dense retrieval. Node C (edge) orchestrates the request, Node B performs dense retrieval on Qdrant, and Node A fuses context and generates the final answer with vLLM.

## Architecture (high level)

- Node C (edge orchestrator): FastAPI gateway, Tantivy BM25, dispatches dense retrieval to Node B, streams generation tokens from Node A.
- Node B (dense retrieval): BGE-M3 embeddings, Qdrant vector search, forwards dense results to Node A.
- Node A (generation): receives sparse and dense results, fuses with RRF, loads context from SQLite, generates with vLLM.

## Repository layout

- Node C/ - edge gateway, BM25, gRPC client for Node B and stream to Node A.
- node_A/implementation/ - generation service, fusion logic, SQLite corpus reader.
- node_B/implementation/ - dense retrieval server and Qdrant indexing/sync scripts.

## Prerequisites

- Python 3.10+ on all nodes
- Qdrant running for Node B
- GPU capable of running vLLM on Node A
- SQLite corpus database for Node A
- Tantivy index for Node C
- Network connectivity between nodes (WireGuard or LAN)

## Data preparation

### 1) Build the SQLite corpus (Node A)

From node_A/implementation:

```powershell
python build_db.py
```

This expects a local `collection.tsv` with MS MARCO passages. It creates `corpus.sqlite` in the same folder.

### 2) Build the Tantivy index (Node C)

From Node C/:

```powershell
python build_index.py --corpus data/documents.jsonl --index data/tantivy_index
```

The JSONL file must contain `{ "id": ..., "text": ... }` entries.

### 3) Index or sync to Qdrant (Node B)

From node_B/implementation:

```powershell
python index_qdrant.py
```

Or use the streaming sync script (supports MS MARCO + WikiQA):

```powershell
python sync_qdrant.py
```

Configure Qdrant host/port and dataset options via environment variables in `sync_qdrant.py`.

## Install dependencies

```powershell
# Node C
pip install -r "Node C\requirements.txt"

# Node A
pip install -r "node_A\implementation\requirements.txt"

# Node B
pip install -r "node_B\implementation\requirements.txt"
```

## Configuration

### Node C
Edit `Node C/config.yaml`:

- `node_b.host` and `node_b.port`
- `node_a.grpc_host` and `node_a.grpc_port`
- `node_a.lan_host` (used by Node B to forward dense results)
- `gateway.port`

### Node B
Environment variables used by `node_B/implementation/src/server.py`:

- `QDRANT_HOST`, `QDRANT_PORT`, `QDRANT_GRPC_PORT`
- `QDRANT_COLLECTION` (default: `msmarco_passages`)
- `NODE_B_GRPC_PORT` (default: 50051)
- `NODE_A_GRPC_HOST` (default: 10.8.0.1)

### Node A
Model and DB paths are set in `node_A/implementation/src/config.py`:

- `MODEL_PATH` (default: `../llama3-awq`)
- `DB_PATH` (default: `corpus.sqlite`)

## Run the system

### 1) Start Node A (generation + gRPC)

```powershell
cd node_A\implementation
python -m src.main
```

### 2) Start Node B (dense retrieval)

```powershell
cd node_B\implementation
python -m src.server
```

### 3) Start Node C (edge orchestrator)

```powershell
cd "Node C"
python -m uvicorn app:app --host 0.0.0.0 --port 8000
```

### 4) Query the edge gateway

```powershell
$body = @{ query = "machine learning"; top_k = 10 } | ConvertTo-Json
Invoke-WebRequest -Uri "http://localhost:8000/query" -Method POST -Body $body -ContentType "application/json"
```

## APIs

- Node C: `POST /query` (JSON `{ query, top_k }`)
  - Query param `mode=parallel|sequential` (default: `parallel`)
  - Optional header `X-Simulate-WAN-Delay` (milliseconds)
- Node C: `GET /health`
- Node A: `POST /generate` (legacy, sparse + dense payload)

## gRPC contracts

- Node C -> Node B: `DenseDispatcher.Dispatch` (dispatch.proto)
- Node B -> Node A: `ResultForwarder.ForwardDenseResults` (result_forward.proto)
- Node C -> Node A: `GenerationOrchestrator.GenerateStream` (coordination.proto)

Proto files live under `Node C/proto` and `node_A/implementation/proto` / `node_B/implementation/proto`.

## Notes

- Large artifacts (models, indexes, databases) are not tracked in git. Place them locally and adjust paths if needed.
- Qdrant collections expected: `msmarco_passages` (and `wikiqa_passages` if enabled).
