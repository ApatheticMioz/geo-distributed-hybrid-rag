# Serial vs Parallel Benchmark Setup

## Overview

This benchmark compares sequential (serial) vs parallel execution of the RAG pipeline on Node C (Edge Orchestrator). The key difference is:

- **Serial Baseline**: Executes BM25 → wait → Dense Retrieval → wait → Node A
- **Parallel Optimized**: Executes BM25 and Dense Retrieval in parallel, overlapping I/O

## Architecture

```
Serial Flow:
  BM25 (wait)
    ↓
  Dense Retrieval (wait)
    ↓
  Node A gRPC (wait)
    ↓
  Tokens
  
Parallel Flow:
  ┌─ BM25
  └─ Dense Dispatch (fire-and-forget)
       ↓
    Node A gRPC (waits for both in flight)
       ↓
    Tokens
```

## Running the Benchmark

### Step 1: Ensure Backend Services are Running

**Node A (Generation Engine)** - Terminal 1:
```powershell
cd "c:\Users\Yurnero\Desktop\Uni Work\Semester 6\NLP\Project\Phase 3\node_A\implementation"
python -m src.main
```

**Node B (Dense Retrieval)** - Terminal 2:
```powershell
cd "c:\Users\Yurnero\Desktop\Uni Work\Semester 6\NLP\Project\Phase 3\node_B\implementation"
python -m src.server
```

### Step 2: Start Serial Gateway (Port 8002)

**Serial Node C** - Terminal 3:
```powershell
cd "c:\Users\Yurnero\Desktop\Uni Work\Semester 6\NLP\Project\Phase 3\serial\Node C"

# Update config.yaml to use port 8002
# gateway:
#   host: "0.0.0.0"
#   port: 8002

python -m uvicorn app:app --host 0.0.0.0 --port 8002
```

### Step 3: Start Parallel Gateway (Port 8001)

**Parallel Node C** - Terminal 4:
```powershell
cd "c:\Users\Yurnero\Desktop\Uni Work\Semester 6\NLP\Project\Phase 3\Node C"
python -m uvicorn app:app --host 0.0.0.0 --port 8001
```

### Step 4: Run Benchmark (Terminal 5)

```powershell
cd "c:\Users\Yurnero\Desktop\Uni Work\Semester 6\NLP\Project\Phase 3"
python benchmark_serial_vs_parallel.py
```

## Expected Results

The benchmark will:
1. Send 100 MS MARCO queries to the serial version (port 8002)
2. Measure TTFT and total latency for each query
3. Send the same 100 queries to the parallel version (port 8001)
4. Calculate the percentage improvement

### Example Output

```
SERIAL RESULTS:
{
  "ttft_ms": {
    "mean": 850.5,
    "median": 820.3,
    "min": 750.2,
    "max": 950.1,
    "stdev": 45.3,
    "p95": 920.0,
    "p99": 945.0
  },
  "total_ms": {...},
  "successful_queries": 100,
  "failed_queries": 0
}

PARALLEL RESULTS:
{
  "ttft_ms": {
    "mean": 580.2,
    "median": 560.1,
    "min": 500.0,
    "max": 650.3,
    "stdev": 35.1,
    "p95": 620.0,
    "p99": 640.0
  },
  "total_ms": {...},
  "successful_queries": 100,
  "failed_queries": 0
}

PERFORMANCE IMPROVEMENT
===
Serial TTFT (mean):        850.50ms
Parallel TTFT (mean):      580.20ms
Absolute improvement:      270.30ms
Percentage improvement:    31.8%

✓ Parallel is 31.8% faster than Serial baseline
```

## Configuration Changes

### Serial Node C Config

Update `serial/Node C/config.yaml`:
```yaml
gateway:
  host: "0.0.0.0"
  port: 8002  # Different port from parallel
```

### Parallel Node C Config

Keep `Node C/config.yaml`:
```yaml
gateway:
  host: "0.0.0.0"
  port: 8001  # Standard port
```

## Key Metrics

- **TTFT (Time to First Token)**: Measures from HTTP request to first token received
  - Serial: Includes sequential BM25 + Dense + RPC overhead
  - Parallel: Includes max(BM25, Dense dispatch) + RPC + generation

- **Sparse Retrieval Time (t_sparse_ms)**: BM25 execution time
  - Serial: Measured in sequential flow
  - Parallel: Measured but overlapped with dense dispatch

- **P95 / P99**: Tail latencies important for user experience
  - Shows impact of parallelization on worst-case scenarios

## Troubleshooting

### Port Already in Use
If port 8002 is in use:
```yaml
# Edit serial/Node C/config.yaml
gateway:
  host: "0.0.0.0"
  port: 8003  # Use different port
```

Then update `benchmark_serial_vs_parallel.py`:
```python
serial_url = "http://localhost:8003/query"
```

### Node B / Node A Connection Issues

Check WireGuard connectivity:
```powershell
Test-NetConnection -ComputerName 10.8.0.5 -Port 50051
Test-NetConnection -ComputerName 10.8.0.1 -Port 50052
```

### Benchmark Timeouts

Increase timeout in benchmark script:
```python
async with httpx.AsyncClient(timeout=180.0) as client:  # 3 minutes
```

## Performance Analysis

### Why is Parallel Faster?

1. **Overlapped I/O**: While Node B computes dense results on background task, Node C can:
   - Initiate gRPC stream to Node A immediately
   - Begin streaming generation while dense results are being computed

2. **Reduced Total Wait Time**:
   - Serial: t_bm25 + t_dense + t_rpc_to_a
   - Parallel: max(t_bm25, t_dense_dispatch) + t_rpc_to_a
   - Fire-and-forget ACK returns immediately (~1-2ms vs 100-500ms for full retrieval)

3. **Batch Efficiency**: Node B can queue multiple dispatch requests, amortizing model initialization overhead

## Collecting Multiple Runs

To track variability across multiple runs:

```powershell
# Run benchmark multiple times
for ($i=1; $i -le 5; $i++) {
    Write-Host "Run $i of 5..."
    python benchmark_serial_vs_parallel.py
    Start-Sleep -Seconds 30
}
```

Results will accumulate in `benchmark_results.json` with timestamps.

## Expected Timeline

- **Benchmark startup**: ~5 seconds
- **Serial 100 queries**: ~2-3 minutes (includes client-side 200ms delays between queries)
- **Parallel 100 queries**: ~2-3 minutes (same delay for fair comparison)
- **Total runtime**: ~5-7 minutes

## Reference: Paper Claims

- Original paper claims: 30-40% reduction in TTFT with parallelization
- Factors affecting improvement:
  - Network latency (WireGuard VPN adds ~5-10ms per hop)
  - Model size (larger models = longer BM25 window is visible)
  - Query complexity (simple vs complex affects dense retrieval time)
