# DEPLOYMENT COMMANDS FOR 3-NODE RAG SYSTEM

## WireGuard Network Configuration
- Node A: 10.8.0.1 (WireGuard)
- Node B: 10.8.0.5 (WireGuard)
- Node C: 10.8.0.x (Running on local machine, connects via WireGuard)

Ensure all nodes are connected via WireGuard before starting.

---

## NODE B - Dense Retrieval Engine

### Terminal 1: Start Node B gRPC Server (Port 50051)

```powershell
cd "c:\Users\Yurnero\Desktop\Uni Work\Semester 6\NLP\Project\Phase 3\node_B\implementation"
$env:QDRANT_HOST = "localhost"
$env:QDRANT_PORT = "6333"
$env:NODE_A_LAN_HOST = "192.168.1.100"
$env:SERVER_PORT = "50051"

# Run the server
C:/Users/Yurnero/AppData/Local/Programs/Python/Python311/python.exe src/server.py
```

**Expected Output:**
```
2026-05-03 ... - root - INFO - Loading BAAI/bge-m3 with FP16 precision...
2026-05-03 ... - root - INFO - Model loaded successfully
2026-05-03 ... - root - INFO - Connecting to Qdrant at localhost:6333...
2026-05-03 ... - root - INFO - Qdrant client connected successfully
2026-05-03 ... - root - INFO - Starting async gRPC server...
2026-05-03 ... - root - INFO - gRPC server listening on 0.0.0.0:50051
```

---

## NODE A - Generation Engine

### Terminal 2: Start Node A Dual Servers (HTTP 8001 + gRPC 50052)

```powershell
cd "c:\Users\Yurnero\Desktop\Uni Work\Semester 6\NLP\Project\Phase 3\node_A\implementation"

# Run the dual-mode server (FastAPI on 8001 + gRPC on 50052)
C:/Users/Yurnero/AppData/Local/Programs/Python/Python311/python.exe -m uvicorn src.main:app --host 0.0.0.0 --port 8001
```

**Expected Output:**
```
2026-05-03 ... - node_a - INFO - Starting Node A...
2026-05-03 ... - node_a - INFO - vLLM engine initialized
2026-05-03 ... - root - INFO - gRPC server listening on 0.0.0.0:50052
2026-05-03 ... - uvicorn - INFO - Uvicorn running on http://0.0.0.0:8001
```

---

## NODE C - Edge Orchestrator & Sparse Retrieval

### Terminal 3: Start Node C (Port 8001)

```powershell
cd "c:\Users\Yurnero\Desktop\Uni Work\Semester 6\NLP\Project\Phase 3\Node C"

# Verify config has correct WireGuard IPs
# Expected:
#   node_b.host: "10.8.0.5"
#   node_a.grpc_host: "10.8.0.1"

C:/Users/Yurnero/AppData/Local/Programs/Python/Python311/python.exe -m uvicorn app:app --host 0.0.0.0 --port 8001
```

**Expected Output:**
```
INFO:     Started server process [XXXX]
INFO:     Waiting for application startup.
2026-05-03 ... | INFO | bm25 | Tantivy BM25 index opened: data\tantivy_index | 10 docs
2026-05-03 ... | INFO | clients | NodeB dispatch channel initialized -> 10.8.0.5:50051
2026-05-03 ... | INFO | clients | NodeA stream channel initialized -> 10.8.0.1:50052
2026-05-03 ... | INFO | app | Node C gateway ready.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8001
```

---

## END-TO-END QUERY TEST

### Terminal 4: Test Node C Query Endpoint

```powershell
# Simple test query
$body = @{query = "machine learning artificial intelligence"} | ConvertTo-Json

# POST to Node C gateway
(Invoke-WebRequest -Uri "http://localhost:8001/query" -Method POST -Body $body -ContentType "application/json" -UseBasicParsing).Content
```

**Expected Flow:**
1. Node C receives query → POST /query
2. Node C executes BM25 → gets 3-10 sparse docs
3. Node C dispatches to Node B → fire-and-forget DenseDispatchRequest
4. Node B receives dispatch → returns ACK immediately
5. Node B computes dense retrieval asynchronously
6. Node C opens bidi stream to Node A → sends SparseContextRequest
7. Node B forwards dense results to Node A → ResultForwarder.ForwardDenseResults()
8. Node A receives sparse + dense results → fuses them via RRF
9. Node A generates tokens via vLLM
10. Node A streams tokens back to Node C → GenerationToken messages
11. Node C streams response back to client (HTTP streaming)

**Output:** Stream of generated text tokens

---

## TROUBLESHOOTING

### Port Conflicts
If 8001 is already in use on Node C, modify config.yaml:
```yaml
gateway:
  host: "0.0.0.0"
  port: 8002  # Change to available port
```

### WireGuard Not Connected
Verify nodes are reachable:
```powershell
# From Node C, ping Node A & B
Test-Connection -ComputerName 10.8.0.1
Test-Connection -ComputerName 10.8.0.5

# Or try telnet
Test-NetConnection -ComputerName 10.8.0.1 -Port 50052
Test-NetConnection -ComputerName 10.8.0.5 -Port 50051
```

### Proto Import Errors
If you see "No module named 'coordination_pb2'", regenerate stubs:

Node A:
```powershell
cd "c:\Users\Yurnero\Desktop\Uni Work\Semester 6\NLP\Project\Phase 3\node_A\implementation"
C:/Users/Yurnero/AppData/Local/Programs/Python/Python311/python.exe -m grpc_tools.protoc -I proto/ --python_out=generated/ --grpc_python_out=generated/ proto/coordination.proto proto/result_forward.proto
```

Node B:
```powershell
cd "c:\Users\Yurnero\Desktop\Uni Work\Semester 6\NLP\Project\Phase 3\node_B\implementation"
C:/Users/Yurnero/AppData/Local/Programs/Python/Python311/python.exe -m grpc_tools.protoc -I proto/ --python_out=generated/ --grpc_python_out=generated/ proto/dispatch.proto proto/coordination.proto proto/result_forward.proto
```

---

## PROTOCOL SUMMARY

### Node C → Node B
- **Protocol**: gRPC fire-and-forget
- **RPC**: DenseDispatcher.Dispatch()
- **Request**: DenseDispatchRequest (query_id, query_text, top_k, node_a_lan_host, node_a_grpc_port)
- **Response**: DenseDispatchAck (immediate)

### Node B → Node A
- **Protocol**: gRPC unary
- **RPC**: ResultForwarder.ForwardDenseResults()
- **Request**: DenseResultForward (query_id, docs[], t_dense_ms)
- **Response**: DenseResultAck

### Node C → Node A
- **Protocol**: gRPC bidirectional stream
- **RPC**: GenerationOrchestrator.GenerateStream()
- **Request Stream**: SparseContextRequest (query_id, query_text, docs[], t_sparse_ms, node_b_dispatch_failed)
- **Response Stream**: GenerationToken (query_id, token, is_final, ttft_ms)

---

## TIMING MEASUREMENTS

- **t_sparse_ms**: Time for Node C to execute BM25 retrieval (measured in app.py)
- **t_dense_ms**: Time for Node B to compute dense retrieval (measured in server.py)
- **ttft_ms**: Time to first token from start of generation in Node A (measured on first token only)
- **t_total_ms**: Total latency from HTTP request to first response token (measured in Node C app.py)
