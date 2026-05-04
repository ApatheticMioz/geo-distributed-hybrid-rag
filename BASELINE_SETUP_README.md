# Serial vs Parallel Execution Baseline Setup

## What Has Been Done

### 1. Serial Node C Implementation ✅

The `/serial/Node C/` directory now contains a **fully sequential** version of the RAG pipeline:

**Key Changes:**
- **Removed parallelization**: Removed `asyncio.create_task()` for concurrent BM25 and dense retrieval
- **Sequential execution**: 
  1. BM25 runs and completes
  2. Dense retrieval starts AFTER BM25 completes
  3. Both results sent to Node A
  4. Tokens streamed back to client

**Code changes in `serial/Node C/app.py`:**
```python
# ✗ REMOVED (Old Parallel):
# bm25_task = asyncio.create_task(...)
# dense_task = asyncio.create_task(...)
# sparse_results = await bm25_task
# dense_results = await asyncio.wait_for(dense_task, timeout=0.150)

# ✓ ADDED (New Sequential):
# BM25 first
sparse_results = await asyncio.to_thread(self.bm25.query, query_text, k)
t_sparse_ms = (time.perf_counter() - t0) * 1000

# Dense second (no timeout, waits for completion)
dense_results = await self.node_b.retrieve(query_text=query_text, top_k=k)
t_dense_ms = (time.perf_counter() - t_dense_start) * 1000
```

**Enhanced logging to track sequential flow:**
```
[query_id] [SERIAL MODE] Pipeline start
[query_id] Starting BM25 (sparse retrieval)...
[query_id] BM25 done: 125.3 ms | 10 docs
[query_id] Starting Dense retrieval (Node B)...
[query_id] Dense done: 385.2 ms | 10 docs
[query_id] [SEQUENTIAL CHECKPOINT] Sparse=125.3ms, Dense=385.2ms, Total before Node A=510.5ms
[query_id] [SEQUENTIAL] First token received | Edge-TTFT=687.4ms (Sparse=125.3ms + Dense=385.2ms + RPC=176.9ms)
[query_id] [SEQUENTIAL MODE] Pipeline complete: 2543.8 ms total
```

### 2. Configuration Updates ✅

**Serial config** (`serial/Node C/config.yaml`):
- Port: 8002 (for separate benchmarking)
- Points to same Node B (10.8.0.5:50051)
- Points to same Node A (10.8.0.1:50052)

**Parallel config** (`Node C/config.yaml`):
- Port: 8001 (existing)
- Points to same Node B (10.8.0.5:50051)
- Points to same Node A (10.8.0.1:50052)

### 3. Benchmark Script Created ✅

**File**: `benchmark_serial_vs_parallel.py`

**Features:**
- Runs 100 MS MARCO queries against both versions
- Measures:
  - TTFT (Time to First Token)
  - P95 and P99 percentiles
  - Mean, median, min, max latencies
  - Sparse retrieval time breakdown
- Calculates percentage improvement
- Saves results to JSON for analysis

**Key Metrics Tracked:**
```
TTFT (Time to First Token):
  - mean: Average across all queries
  - median: Middle value (50th percentile)
  - p95: 95th percentile (tail latency)
  - p99: 99th percentile (worst-case user experience)
  - min/max: Range of observed latencies
  - stdev: Standard deviation (consistency)

Total Time:
  - Complete end-to-end latency
  
Sparse Time:
  - BM25 only (for breakdown analysis)
```

### 4. Comprehensive Guide Created ✅

**File**: `BENCHMARK_GUIDE.md`

**Contains:**
- Architecture diagrams (serial vs parallel flows)
- Step-by-step instructions to run both versions
- Configuration requirements
- Expected output examples
- Troubleshooting tips
- Performance analysis explanation

## Running the Benchmark

### Quick Start (5 Steps):

```powershell
# Terminal 1: Start Node A
cd node_A\implementation
python -m src.main

# Terminal 2: Start Node B
cd node_B\implementation
python -m src.server

# Terminal 3: Start Serial Gateway
cd serial\Node C
python -m uvicorn app:app --host 0.0.0.0 --port 8002

# Terminal 4: Start Parallel Gateway
cd Node C
python -m uvicorn app:app --host 0.0.0.0 --port 8001

# Terminal 5: Run Benchmark
python benchmark_serial_vs_parallel.py
```

### Expected Results Format:

```json
{
  "timestamp": 1715000000.123,
  "serial": {
    "ttft_ms": {
      "mean": 850.5,
      "median": 820.3,
      "p95": 920.0,
      "p99": 945.0,
      "stdev": 45.3
    },
    "successful_queries": 100,
    "failed_queries": 0
  },
  "parallel": {
    "ttft_ms": {
      "mean": 580.2,
      "median": 560.1,
      "p95": 620.0,
      "p99": 640.0,
      "stdev": 35.1
    },
    "successful_queries": 100,
    "failed_queries": 0
  },
  "improvement_pct": 31.8
}
```

## How Serial vs Parallel Differ

### Sequential (Serial) Flow:
```
Time ────────────────────────────────────
     │ BM25           │ Dense        │ Node A │ Tokens │
     ├─────────────────────────────────────────────────┤
     ├───────┤        (125ms)         (385ms)   (177ms)
                      ├────────────────┤        (66ms)
                                       ├────────────────┤
                                       (Generation + streaming)
                                       
Total TTFT ≈ 125ms + 385ms + 177ms = 687ms
```

### Parallel Flow:
```
Time ────────────────────────────────────
     │ BM25  │ Dense Dispatch │      │ Tokens │
     ├───────┤────────────────┤ Node A ├──────┤
     │ (125ms)  (2ms ACK!)    │(180ms)│(66ms)
     └─────────────────────────┘
                ↓ Async background
            Dense retrieval happens here
            (385ms total, but overlapped)
                
Total TTFT ≈ max(125ms, 2ms) + 180ms + 66ms = 373ms
```

## Key Insights

1. **Parallelization reduces TTFT by overlapping I/O**
   - Serial: t_bm25 + t_dense + t_rpc = 687ms
   - Parallel: max(t_bm25, t_dispatch_ack) + t_rpc = 373ms
   - **Improvement: ~45-50% expected**

2. **Fire-and-Forget Pattern is Critical**
   - Node B returns DenseDispatchAck in 2-3ms (not waiting for full retrieval)
   - Allows Node C to proceed to Node A immediately
   - Dense results arrive at Node A asynchronously

3. **Dense Retrieval Computation Still Happens**
   - It just doesn't block the pipeline
   - Node B computes it on background task
   - Forwards results to Node A when ready

4. **Tail Latencies (P95/P99) Show Real-World Impact**
   - Parallelization most beneficial when components have variable latency
   - If one component is consistently slow, parallelization helps less

## Metrics to Track

### 1. T_sequential (Your Baseline)
From serial benchmark:
- **TTFT mean**: Total time to first token
- **t_sparse_ms**: BM25 execution time
- **Component times**: Breakdown of all stages

### 2. T_parallel (Optimized)
From parallel benchmark:
- **TTFT mean**: Should be significantly lower
- **Overlapped time**: How much of dense retrieval is hidden?

### 3. Improvement Calculation
```
Improvement % = (T_serial - T_parallel) / T_serial * 100
```

**Expected**: 30-40% based on paper claims
**Likely range**: 25-50% depending on network conditions

## Running on Your Actual Infrastructure

The benchmark currently tests on localhost. For WireGuard testing:

```python
# Update benchmark_serial_vs_parallel.py
serial_url = "http://10.8.0.10:8002/query"  # Serial on WireGuard
parallel_url = "http://10.8.0.10:8001/query"  # Parallel on WireGuard
```

This measures cross-device latency impact.

## What's Next?

1. **Run the benchmark** using BENCHMARK_GUIDE.md
2. **Collect 100+ queries** of results from both versions
3. **Calculate percentage improvement** using output JSON
4. **Document findings** in your report with:
   - Mean TTFT comparison chart
   - P95/P99 tail latency analysis
   - Breakdown of where time is spent in each pipeline
   - Discussion of parallelization benefits vs overhead

## Files Overview

```
Phase 3/
├── Node C/                    # Parallel version (gRPC)
│   ├── app.py                # Orchestrates in parallel
│   ├── clients.py            # gRPC dispatch client
│   └── config.yaml           # Port 8001
│
├── serial/
│   └── Node C/               # Serial version (HTTP)
│       ├── app.py            # Sequential execution
│       ├── clients.py        # HTTP client
│       └── config.yaml       # Port 8002
│
├── benchmark_serial_vs_parallel.py    # Runs 100-query benchmark
├── BENCHMARK_GUIDE.md                 # Detailed instructions
└── BENCHMARK_README.md                # This file
```

## Troubleshooting Common Issues

### "Connection refused" on port 8002
→ Make sure serial Node C is started on port 8002

### "TTFT is the same for both versions"
→ Check that nodes are actually using different code paths
→ Look for "[SERIAL MODE]" vs "[PARALLEL]" in logs

### One version is much slower
→ Check CPU/GPU utilization
→ Verify network connectivity (ping 10.8.0.1 and 10.8.0.5)
→ Look for errors in Node A/B logs

### Benchmark won't connect
→ Verify WireGuard is connected
→ Test: `Test-NetConnection -ComputerName 10.8.0.1 -Port 8001`
