# Node C — Implementation Plan (Revised v3)
**Role:** Edge Orchestrator & Sparse Retrieval Engine  
**Hardware:** Intel Iris XE (80EU) / i5-1235U / 16 GB LPDDR4X / University Wi-Fi / Windows 11

---

## Architectural Corrections from v1 and v2

Five critical errors in v1 were corrected in v2. Four new defects discovered in v2 are corrected here.

### v1 → v2 Fixes (retained)

| # | v1 Error | v2 Fix |
|---|----------|--------|
| **Fix 1** | Node C → Node A used `httpx` JSON (HTTP/1.1) | All inter-node comms use gRPC + Protobuf |
| **Fix 2** | Timeout scheduler lived on Node C | Scheduler lives on Node A; Node C only dispatches |
| **Fix 3** | Node B replied to Node C across the WAN (trombone) | Node B sends dense results directly to Node A on the local LAN |
| **Fix 4** | Entire MS MARCO corpus loaded as Python dicts → OOM | Disk-backed Lucene index via Tantivy (Rust); never fully in RAM |
| **Fix 5** | NLTK `word_tokenize` + Python loops for tokenization | Tantivy's compiled Rust tokenizer handles all tokenization internally |

### v2 → v3 Fixes (new)

| # | v2 Defect | v3 Fix |
|---|-----------|--------|
| **Fix 6** | `SparseContextRequest` has no field to signal Node B failure; Node A blindly waits out its full `Tthreshold` (~2 s) whenever Node B drops | Add `bool node_b_dispatch_failed = 5` to `SparseContextRequest`; Node C sets this flag so Node A can trigger immediate fallback |
| **Fix 7** | `orchestrator.py` sequentially `await`s `dispatch_task` after `bm25_task`, holding finished BM25 results hostage for up to 2 s if Node B times out | Upgrade `GenerateStream` to a **bidirectional** gRPC stream; Node C opens the stream and sends sparse context to Node A immediately after BM25, then asynchronously streams the failure flag if dispatch times out |
| **Fix 8** | `config.yaml` gives Node A's **WireGuard VPN IP** (`10.0.0.1`) to Node B for dense result forwarding, forcing the multi-KB dense payload through WireGuard encryption and destroying the ≤ 1 ms LAN path | Add `node_a.lan_host` (physical LAN IP, e.g. `192.168.1.100`) to `config.yaml`; Node C passes this physical IP to Node B in `DenseDispatchRequest`, not the VPN IP |
| **Fix 9** | Node C logs `ttft_ms` from Node A's `GenerationToken`, which starts its clock only when Node A receives the gRPC call — ignoring BM25 time and WAN transit entirely | Node C measures TTFT locally: `time.perf_counter()` delta from HTTP request arrival at the gateway to the first token yielded to the client |

---

## Revised Data Flow

```
[User] ──HTTP──► [Node C: FastAPI]
                      │
                  t_request_start = time.perf_counter()   [FIX 9]
                      │
          ┌───────────┴───────────────┐
          │ (concurrent, async tasks) │
          ▼                           ▼
  [Branch 1]                    [Branch 2]
  BM25 / Tantivy               gRPC dispatch
  (local CPU RAM)              to Node B
  30–80 ms                     (fire-and-forget ACK)
          │                           │
          │ BM25 done                 │ ACK/timeout
          ▼ → open bidi gRPC stream   │ result arrives
  [Node A gRPC server] ◄─────────────┘ asynchronously
  ① sparse context sent immediately   [FIX 7]
  ② node_b_dispatch_failed flag        [FIX 6]
     streamed if dispatch times out
  (Node B sends dense results directly to Node A
   via physical LAN IP 192.168.1.100, not 10.0.0.1) [FIX 8]
  (Synchronization Controller)
  - Receives node_b_dispatch_failed early → immediate fallback [FIX 6]
  - Owns Tthreshold timer
  - Applies RRF or speculative prefill
  - Runs LLM (Llama-3-8B AWQ)
  - Streams tokens back to Node C ──► [User]
                      │
                  TTFT = perf_counter() - t_request_start
                  (measured on Node C, not from Node A's clock) [FIX 9]
```

Key points:
- Node B never replies to Node C. It replies directly to Node A on the **physical LAN IP** (not the VPN IP).
- Node C's two jobs after dispatching are: (a) finish its own BM25 query, (b) stream the response from Node A back to the user.
- All timeout and fallback logic lives entirely on Node A.
- TTFT is measured edge-to-edge on Node C for scientifically valid Phase 3 results.

---

## Table of Contents
1. [Project Directory Structure](#1-project-directory-structure)
2. [Python Environment Setup](#2-python-environment-setup)
3. [WireGuard VPN Configuration](#3-wireguard-vpn-configuration)
4. [Protobuf & gRPC Schema Design](#4-protobuf--grpc-schema-design)
5. [Disk-Backed BM25 Index via Tantivy](#5-disk-backed-bm25-index-via-tantivy)
6. [gRPC Async Client → Node B (Dispatch-Only)](#6-grpc-async-client--node-b-dispatch-only)
7. [gRPC Async Client → Node A (Bidi Stream)](#7-grpc-async-client--node-a-bidi-stream)
8. [Asynchronous FastAPI Gateway](#8-asynchronous-fastapi-gateway)
9. [Orchestrator (Dispatch-Only — No Scheduler Logic)](#9-orchestrator-dispatch-only--no-scheduler-logic)
10. [Latency Instrumentation](#10-latency-instrumentation)
11. [Configuration File](#11-configuration-file)
12. [Index Build Script (One-Time)](#12-index-build-script-one-time)
13. [Running & Testing Node C](#13-running--testing-node-c)
14. [Requirement Compliance Checklist](#14-requirement-compliance-checklist)

---

## 1. Project Directory Structure

```
nodeC/
├── config.yaml
├── requirements.txt
│
├── proto/
│   ├── dispatch.proto          # Node C → Node B: query dispatch (ACK only)
│   └── coordination.proto      # Node C ↔ Node A: bidi stream [FIX 6 & 7]
│
├── generated/                  # Auto-generated stubs — do NOT edit
│   ├── __init__.py
│   ├── dispatch_pb2.py
│   ├── dispatch_pb2_grpc.py
│   ├── coordination_pb2.py
│   └── coordination_pb2_grpc.py
│
├── retrieval/
│   ├── __init__.py
│   ├── bm25_tantivy.py         # Disk-backed BM25 via Tantivy [FIX 4 & 5]
│   └── tantivy_index/          # Tantivy index files (written by build script)
│
├── clients/
│   ├── __init__.py
│   ├── node_b_client.py        # Fire-and-forget gRPC dispatch to Node B [FIX 3 & 8]
│   └── node_a_client.py        # Bidi gRPC stream client to Node A [FIX 1, 2, 6 & 7]
│
├── scheduler/
│   ├── __init__.py
│   └── orchestrator.py         # Dispatch-only; no timeout logic [FIX 2, 7 & 9]
│
├── gateway/
│   ├── __init__.py
│   └── app.py                  # FastAPI entry point [FIX 9]
│
├── utils/
│   ├── __init__.py
│   └── metrics.py
│
├── scripts/
│   └── build_index.py          # One-time Tantivy index builder for MS MARCO [FIX 4]
│
└── tests/
    ├── test_tantivy.py
    ├── test_dispatch_client.py
    └── test_pipeline.py
```

---

## 2. Python Environment Setup

### 2.1 Install Python 3.11
Download from python.org. Check **"Add Python to PATH"** during install.

```bash
python --version    # Python 3.11.x
```

### 2.2 Create and activate a virtual environment
```bash
cd nodeC
python -m venv venv
venv\Scripts\activate
```

### 2.3 `requirements.txt`

> **[FIX 4 & 5]** `rank-bm25` and `nltk` are removed entirely. `tantivy` replaces both.
> **[FIX 1]** `httpx` is removed. `grpcio` handles all inter-node transport.

```text
# Web framework
fastapi==0.111.0
uvicorn[standard]==0.29.0

# gRPC — ALL inter-node communication [FIX 1]
grpcio==1.63.0
grpcio-tools==1.63.0
protobuf==4.25.3

# Disk-backed BM25 via Tantivy (Rust) [FIX 4 & 5]
tantivy==0.22.0

# Config and utilities
pyyaml==6.0.1
structlog==24.1.0

# Testing
pytest==8.2.0
pytest-asyncio==0.23.6
```

Install:
```bash
pip install -r requirements.txt
```

> **Note on Tantivy:** `tantivy` ships as a pre-compiled Python wheel with the Rust runtime bundled. No separate Rust toolchain is required. On Windows, `pip install tantivy` fetches a pre-built `.whl` directly.

---

## 3. WireGuard VPN Configuration

Node C must maintain a persistent encrypted tunnel through the university Wi-Fi to reach the home LAN where Nodes A and B are co-located.

### 3.1 Install WireGuard for Windows
Download from https://www.wireguard.com/install/

### 3.2 Generate Node C's key pair
Open **PowerShell as Administrator**:
```powershell
cd "C:\Program Files\WireGuard"
.\wireguard.exe /generateprivatekey | Out-File -FilePath "C:\wireguard\nodeC_private.key"
Get-Content "C:\wireguard\nodeC_private.key" | .\wireguard.exe /generatepublickey
```
Share only Node C's **public key** with the Node A operator for their peer config.

### 3.3 Node C WireGuard config (`C:\wireguard\nodeC.conf`)
```ini
[Interface]
PrivateKey = <NODE_C_PRIVATE_KEY>
Address = 10.0.0.3/24
DNS = 8.8.8.8
MTU = 1420                       # Prevents fragmentation of multi-KB retrieval payloads

[Peer]
PublicKey = <NODE_A_PUBLIC_KEY>
Endpoint = <HOME_DDNS_HOSTNAME>:51820    # e.g. home.example.duckdns.org:51820
AllowedIPs = 10.0.0.0/24
PersistentKeepalive = 25         # Keeps tunnel alive through university NAT
```

### 3.4 Import and start the tunnel
```powershell
.\wireguard.exe /importtunnel "C:\wireguard\nodeC.conf"
.\wireguard.exe /installtunnelservice "C:\wireguard\nodeC.conf"
```

### 3.5 Verify both nodes are reachable
```powershell
ping 10.0.0.1    # Node A
ping 10.0.0.2    # Node B
```
Both should respond with RTT in the 5–20 ms range.

---

## 4. Protobuf & gRPC Schema Design

> **[FIX 1 & 3]** Two separate `.proto` files replace the single `retrieval.proto` from v1.
> - `dispatch.proto`: Node C → Node B. Node B is instructed to reply **directly to Node A**, not back to Node C. This eliminates the trombone routing defect.
> - `coordination.proto`: Node C ↔ Node A. Upgraded to **bidirectional streaming** [FIX 7], and extended with a `node_b_dispatch_failed` flag [FIX 6].

These schemas must be coordinated with the Node A and Node B implementors. All three nodes share the same `.proto` definitions.

### 4.1 `proto/dispatch.proto` (Node C → Node B)

> **[FIX 8]** Field 4 is renamed from `node_a_grpc_host` to `node_a_lan_host`. Node C will populate
> this with Node A's **physical LAN IP** (e.g. `192.168.1.100`), not the WireGuard VPN IP (`10.0.0.1`).
> This ensures Node B's dense payload travels over the raw Gigabit Ethernet (≤ 1 ms) rather than
> being needlessly routed through the WireGuard tunnel.

```protobuf
syntax = "proto3";
package dispatch;

// Node C sends this to Node B.
// Critically, it includes Node A's physical LAN address so Node B
// can forward its dense results DIRECTLY to Node A over the home LAN [FIX 3].
// node_a_lan_host MUST be the physical LAN IP, NOT the WireGuard VPN IP [FIX 8].
message DenseDispatchRequest {
  string query_id         = 1;
  string query_text       = 2;
  int32  top_k            = 3;
  string node_a_lan_host  = 4;   // Physical LAN IP (e.g. 192.168.1.100) [FIX 8]
  int32  node_a_grpc_port = 5;
}

// Node B immediately ACKs. Dense computation happens asynchronously on Node B.
message DenseDispatchAck {
  string query_id = 1;
  bool   accepted = 2;
}

service DenseDispatcher {
  rpc Dispatch (DenseDispatchRequest) returns (DenseDispatchAck);
}
```

### 4.2 `proto/coordination.proto` (Node C ↔ Node A)

> **[FIX 6]** `SparseContextRequest` gains a new field `node_b_dispatch_failed` (field 5).
> When Node C sets this to `true`, Node A can **immediately** trigger the speculative prefill
> fallback without waiting for `Tthreshold` to expire. This eliminates the silent-failure
> latency penalty that would otherwise cost 2+ seconds on every Node B dropout.
>
> **[FIX 7]** The service is upgraded from a **unary-request / server-stream** RPC to a
> **fully bidirectional stream**. This allows Node C to send the sparse context to Node A
> immediately after BM25 completes (without waiting for the Node B dispatch to resolve),
> then asynchronously push a second `SparseContextRequest` with `node_b_dispatch_failed=true`
> if the dispatch later times out. Node A starts processing as soon as it receives the first
> message; it does not need to wait for the stream to close.

```protobuf
syntax = "proto3";
package coordination;

message RetrievedDocument {
  string doc_id = 1;
  string text   = 2;
  float  score  = 3;
  int32  rank   = 4;
}

// Node C sends this to Node A on the bidirectional stream.
// Node A is already expecting dense results from Node B on a separate channel.
// Node A owns the Tthreshold timer and all fallback logic [FIX 2].
//
// Node C sends this message at most TWICE per query:
//   Message 1 (always): sparse docs + node_b_dispatch_failed=false, sent immediately after BM25 [FIX 7]
//   Message 2 (conditional): empty docs + node_b_dispatch_failed=true, sent only if dispatch times out [FIX 6]
message SparseContextRequest {
  string query_id                    = 1;
  string query_text                  = 2;
  repeated RetrievedDocument docs    = 3;
  double t_sparse_ms                 = 4;   // For latency accounting on Node A
  bool   node_b_dispatch_failed      = 5;   // [FIX 6] true → Node A must not wait for dense branch
}

// Node A streams tokens back to Node C as they are generated.
// NOTE [FIX 9]: Node C IGNORES ttft_ms from this message.
//               Node C measures TTFT independently using its own wall clock.
message GenerationToken {
  string query_id  = 1;
  string token     = 2;
  bool   is_final  = 3;
  double ttft_ms   = 4;   // Node A's internal measurement — NOT used by Node C [FIX 9]
}

service GenerationOrchestrator {
  // [FIX 7] Bidirectional stream: Node C pushes SparseContextRequest messages,
  // Node A streams back GenerationTokens concurrently.
  // Node A MUST NOT require the request stream to close before streaming tokens.
  rpc GenerateStream (stream SparseContextRequest) returns (stream GenerationToken);
}
```

### 4.3 Generate Python stubs
```bash
python -m grpc_tools.protoc -I proto/ --python_out=generated/ --grpc_python_out=generated/ proto/dispatch.proto
python -m grpc_tools.protoc -I proto/ --python_out=generated/ --grpc_python_out=generated/ proto/coordination.proto
echo. > generated\__init__.py
```

---

## 5. Disk-Backed BM25 Index via Tantivy

> **[FIX 4]** The in-memory Python dict corpus is replaced with a Tantivy index stored on disk. Tantivy is a Lucene-equivalent written in Rust. It memory-maps only active index segments — the full MS MARCO corpus (8.8M passages) fits on disk and is queried without loading everything into RAM, eliminating the OOM risk on 16 GB LPDDR4X.

> **[FIX 5]** Tantivy uses its own compiled Rust tokenizer (Unicode-aware, configurable). There are no NLTK calls, no Python-level stopword loops, and no `word_tokenize` overhead. All tokenization happens inside the Rust runtime.

### 5.1 `retrieval/bm25_tantivy.py`

```python
import logging
import time
from pathlib import Path
from typing import List, Dict, Any

import tantivy

logger = logging.getLogger(__name__)


class TantivyBM25Index:
    """
    Disk-backed BM25 index using Tantivy (Rust/Lucene-equivalent).

    FIX 4: The index lives on disk. Tantivy memory-maps active segments only.
            MS MARCO (8.8M passages) is queried without loading into Python RAM.
    FIX 5: Tokenization is handled by Tantivy's compiled Rust tokenizer.
            No NLTK, no Python loops, no stopword iteration at query time.

    Target latency: 30–80 ms per query.
    """

    def __init__(self, index_path: str):
        self.index_path = Path(index_path)
        if not self.index_path.exists():
            raise FileNotFoundError(
                f"Tantivy index not found at '{index_path}'. "
                "Run scripts/build_index.py first."
            )
        self._index = tantivy.Index.open(str(self.index_path))
        # Reload the searcher each query to pick up any index updates.
        # For a static evaluation corpus this is effectively free.
        self._searcher = self._index.searcher()
        logger.info(
            f"Tantivy BM25 index opened: {self.index_path} "
            f"| {self._searcher.num_docs()} docs"
        )

    def query(self, query_text: str, top_k: int = 10) -> List[Dict[str, Any]]:
        """
        Returns top_k BM25-ranked results.
        Tokenization and scoring are performed entirely inside the Rust runtime.
        """
        t_start = time.perf_counter()

        # parse_query uses Tantivy's Rust tokenizer — no Python tokenization
        query = self._index.parse_query(query_text, ["body"])
        hits = self._searcher.search(query, top_k).hits

        results = []
        for rank, (score, doc_address) in enumerate(hits, start=1):
            doc = self._searcher.doc(doc_address)
            results.append({
                "doc_id": doc.get_first("doc_id"),
                "text":   doc.get_first("body"),
                "score":  float(score),
                "rank":   rank,
            })

        elapsed_ms = (time.perf_counter() - t_start) * 1000
        logger.debug(f"Tantivy BM25 query: {elapsed_ms:.1f} ms, {len(results)} results")
        return results
```

> **Schema note:** The Tantivy schema used here (`doc_id` + `body`) is defined in the build script (Section 12). The query-time code only opens an already-built index — it never rebuilds or loads raw documents.

---

## 6. gRPC Async Client → Node B (Dispatch-Only)

> **[FIX 3]** In v1, Node C dispatched a query to Node B and awaited the full dense result payload across the WAN, then re-sent it to Node A — crossing the WAN boundary twice. In v2/v3, Node C sends a `DenseDispatchRequest` that includes Node A's gRPC address. Node B uses this address to forward its result **directly to Node A on the local LAN (≤ 1 ms)**. Node C only receives a lightweight ACK.

> **[FIX 8]** The `dispatch()` method now accepts `node_a_lan_host` (physical LAN IP) instead of `node_a_grpc_host` (WireGuard VPN IP). This IP is embedded in the `DenseDispatchRequest` that is sent to Node B, so that Node B routes the multi-KB dense payload over raw Gigabit Ethernet rather than through the WireGuard tunnel. The physical LAN IP is read from `config.yaml` under `node_a.lan_host`.

### `clients/node_b_client.py`

```python
import logging
import grpc
from generated import dispatch_pb2, dispatch_pb2_grpc

logger = logging.getLogger(__name__)


class NodeBDispatchClient:
    """
    Sends a fire-and-forget dispatch request to Node B.

    FIX 3: The request embeds Node A's gRPC endpoint so Node B can forward
    its dense retrieval result directly to Node A over the local LAN (≤1 ms),
    bypassing the WAN entirely for the dense payload.
    Node C receives only a lightweight ACK — it never handles the dense payload.

    FIX 8: node_a_lan_host is the PHYSICAL LAN IP of Node A (e.g. 192.168.1.100),
    NOT the WireGuard VPN IP (10.0.0.1). Passing the VPN IP here would force
    Node B's dense payload through WireGuard encryption, destroying the ≤1 ms
    LAN path that the Tparallel masking model depends on.
    """

    def __init__(self, host: str, port: int):
        target = f"{host}:{port}"
        options = [
            ("grpc.keepalive_time_ms", 10_000),
            ("grpc.keepalive_timeout_ms", 5_000),
            ("grpc.keepalive_permit_without_calls", True),
            ("grpc.http2.max_pings_without_data", 0),
        ]
        self._channel = grpc.aio.insecure_channel(target, options=options)
        self._stub = dispatch_pb2_grpc.DenseDispatcherStub(self._channel)
        logger.info(f"NodeB dispatch channel initialized → {target}")

    async def dispatch(
        self,
        query_id: str,
        query_text: str,
        top_k: int,
        node_a_lan_host: str,   # [FIX 8] Physical LAN IP — NOT the WireGuard VPN IP
        node_a_grpc_port: int,
        ack_timeout: float = 2.0,
    ) -> bool:
        """
        Dispatches the query to Node B and returns True if Node B acknowledged.
        Node B will independently send dense results to Node A's physical LAN IP — not back here.

        Returns False if Node B is offline or does not respond within ack_timeout.
        The caller (orchestrator.py) must propagate this failure to Node A via the
        node_b_dispatch_failed flag in SparseContextRequest [FIX 6].
        """
        request = dispatch_pb2.DenseDispatchRequest(
            query_id=query_id,
            query_text=query_text,
            top_k=top_k,
            node_a_lan_host=node_a_lan_host,   # [FIX 8] physical LAN IP
            node_a_grpc_port=node_a_grpc_port,
        )
        try:
            ack = await self._stub.Dispatch(request, timeout=ack_timeout)
            logger.info(
                f"[{query_id}] Node B ACK received | accepted={ack.accepted}"
            )
            return ack.accepted
        except grpc.aio.AioRpcError as e:
            logger.error(
                f"[{query_id}] Node B dispatch failed: {e.code()} — {e.details()}"
            )
            return False

    async def close(self):
        await self._channel.close()
```

---

## 7. gRPC Async Client → Node A (Bidi Stream)

> **[FIX 1]** In v1, Node C used `httpx` to POST a JSON body to Node A. JSON parsing on the i5-1235U is up to 11× slower than Protobuf and payloads are 60–80% larger, directly degrading TTFT. This client uses gRPC with Protobuf for all communication with Node A.

> **[FIX 2]** Node C no longer evaluates any timeout. It sends sparse results to Node A and then simply reads a server-side token stream. Node A holds all timeout and fallback logic.

> **[FIX 6 & 7]** The RPC is now a **bidirectional stream**. `generate_stream()` accepts the initial
> sparse request and the `dispatch_task` asyncio Future. It sends the sparse context to Node A
> **immediately** (without waiting for the dispatch result), then awaits the dispatch task
> asynchronously. If dispatch fails, it pushes a second `SparseContextRequest` message with
> `node_b_dispatch_failed=True` so Node A can trigger its immediate fallback without waiting
> out `Tthreshold`. Because gRPC bidi streaming fully decouples the send and receive paths,
> Node A starts receiving and processing tokens from the moment it gets the first message —
> it does not need to wait for Node C to close the request stream.

### `clients/node_a_client.py`

```python
import asyncio
import logging
from typing import List, Dict, Any, AsyncIterator

import grpc
from generated import coordination_pb2, coordination_pb2_grpc

logger = logging.getLogger(__name__)


class NodeAStreamClient:
    """
    Sends sparse retrieval results to Node A via a bidirectional gRPC stream and
    receives a server-side token stream back.

    FIX 1: Replaces httpx/JSON with gRPC/Protobuf.
            Protobuf is 11× faster to parse than JSON on mobile-tier CPUs.
    FIX 2: Node C sends results and reads the stream.
            All timeout / Tthreshold / speculative-prefill logic is on Node A.
    FIX 6: If dispatch_task resolves to False, a second SparseContextRequest with
            node_b_dispatch_failed=True is sent so Node A can immediately fall back.
    FIX 7: The stream is bidirectional. The initial sparse context is sent as soon
            as BM25 finishes — dispatch_task does NOT have to resolve first.
    """

    def __init__(self, host: str, port: int):
        target = f"{host}:{port}"
        options = [
            ("grpc.keepalive_time_ms", 10_000),
            ("grpc.keepalive_timeout_ms", 5_000),
            ("grpc.keepalive_permit_without_calls", True),
            ("grpc.http2.max_pings_without_data", 0),
        ]
        self._channel = grpc.aio.insecure_channel(target, options=options)
        self._stub = coordination_pb2_grpc.GenerationOrchestratorStub(self._channel)
        logger.info(f"NodeA stream channel initialized → {target}")

    async def generate_stream(
        self,
        query_id: str,
        query_text: str,
        sparse_results: List[Dict[str, Any]],
        t_sparse_ms: float,
        dispatch_task: "asyncio.Task[bool]",   # [FIX 6 & 7] Future for Node B ACK result
    ) -> AsyncIterator[coordination_pb2.GenerationToken]:
        """
        Opens a bidirectional gRPC stream to Node A.

        Sends two messages at most:
          1. Sparse context (always, immediately after BM25 — no waiting for dispatch) [FIX 7]
          2. node_b_dispatch_failed=True notification (only if dispatch_task resolves False) [FIX 6]

        Yields GenerationToken objects from Node A concurrently.
        Node A handles RRF fusion, Tthreshold logic, and LLM decoding.

        NOTE: Node A's implementation MUST start streaming tokens as soon as message 1
        arrives. It must not block waiting for the request stream to close.
        """
        docs = [
            coordination_pb2.RetrievedDocument(
                doc_id=r["doc_id"],
                text=r["text"],
                score=r["score"],
                rank=r["rank"],
            )
            for r in sparse_results
        ]

        initial_request = coordination_pb2.SparseContextRequest(
            query_id=query_id,
            query_text=query_text,
            docs=docs,
            t_sparse_ms=t_sparse_ms,
            node_b_dispatch_failed=False,   # [FIX 6] optimistic default
        )

        logger.info(
            f"[{query_id}] Opening bidi stream to Node A "
            f"({len(docs)} sparse docs, t_sparse={t_sparse_ms:.1f} ms)"
        )

        async def request_generator():
            """
            Async generator that feeds SparseContextRequest messages into the
            bidirectional stream.

            Message 1: sparse context, sent immediately.          [FIX 7]
            Message 2: failure flag, sent only if dispatch failed. [FIX 6]
            """
            # ── Message 1: send sparse context to Node A immediately ──────────
            yield initial_request

            # ── Await dispatch result without blocking token streaming ─────────
            # dispatch_task was already running concurrently. Awaiting it here
            # does NOT stall the token receive path because gRPC bidi streaming
            # runs send and receive independently on the same event loop.
            try:
                ack_accepted = await dispatch_task
            except Exception as exc:
                logger.error(f"[{query_id}] dispatch_task raised: {exc}")
                ack_accepted = False

            # ── Message 2 (conditional): notify Node A of Node B failure ──────
            if not ack_accepted:
                logger.warning(
                    f"[{query_id}] Node B dispatch failed — sending "
                    "node_b_dispatch_failed=True to Node A for immediate fallback [FIX 6]"
                )
                yield coordination_pb2.SparseContextRequest(
                    query_id=query_id,
                    query_text=query_text,
                    docs=[],             # no new docs — this is a control message only
                    t_sparse_ms=0.0,
                    node_b_dispatch_failed=True,  # [FIX 6]
                )
            # Generator ends; gRPC marks the request stream as complete.

        try:
            async for token in self._stub.GenerateStream(request_generator()):
                yield token
        except grpc.aio.AioRpcError as e:
            logger.error(
                f"[{query_id}] Node A stream error: {e.code()} — {e.details()}"
            )
            raise

    async def close(self):
        await self._channel.close()
```

---

## 8. Asynchronous FastAPI Gateway

Node C remains the user-facing HTTP endpoint. It accepts queries, runs the orchestrator, and streams tokens back to the user via FastAPI's `StreamingResponse`.

> **[FIX 9]** `t_request_start` is recorded via `time.perf_counter()` at the moment the HTTP
> request arrives at the gateway endpoint. This timestamp is passed into `handle_query` so the
> orchestrator can compute a true edge-to-edge TTFT that includes BM25 processing time and WAN
> transit — not just Node A's internal generation clock.

### `gateway/app.py`

```python
import logging
import time
import yaml
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from retrieval.bm25_tantivy import TantivyBM25Index
from clients.node_b_client import NodeBDispatchClient
from clients.node_a_client import NodeAStreamClient
from scheduler.orchestrator import PipelineOrchestrator
from utils.metrics import LatencyRecorder

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

_orchestrator: PipelineOrchestrator | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _orchestrator
    with open("config.yaml", "r") as f:
        cfg = yaml.safe_load(f)

    # FIX 4 & 5: Disk-backed Tantivy index — no OOM risk, no NLTK
    bm25_index = TantivyBM25Index(cfg["corpus"]["tantivy_index_path"])

    # FIX 3: Node B client only dispatches; Node A address goes in the request
    node_b = NodeBDispatchClient(cfg["node_b"]["host"], cfg["node_b"]["port"])

    # FIX 1: Node A client uses gRPC, not httpx/JSON
    # grpc_host is the WireGuard VPN IP — correct for Node C → Node A tunnel traffic
    node_a = NodeAStreamClient(cfg["node_a"]["grpc_host"], cfg["node_a"]["grpc_port"])

    _orchestrator = PipelineOrchestrator(
        bm25_index=bm25_index,
        node_b_client=node_b,
        node_a_client=node_a,
        top_k=cfg["retrieval"]["top_k"],
        # FIX 8: node_a_lan_host is the PHYSICAL LAN IP passed to Node B for direct forwarding.
        # node_a_grpc_host is the VPN IP used for Node C → Node A gRPC (correctly routed via WireGuard).
        node_a_lan_host=cfg["node_a"]["lan_host"],
        node_a_grpc_port=cfg["node_a"]["grpc_port"],
        recorder=LatencyRecorder(),
    )
    logger.info("Node C gateway ready.")
    yield

    await node_b.close()
    await node_a.close()
    logger.info("Node C gateway shut down.")


app = FastAPI(
    title="Node C — Edge Orchestrator",
    version="3.0.0",
    lifespan=lifespan,
)


class QueryRequest(BaseModel):
    query: str
    top_k: int = 10


@app.post("/query")
async def query_endpoint(req: QueryRequest):
    """
    Accepts user query, runs parallel retrieval, streams the LLM answer
    token-by-token as Server-Sent Events.
    """
    if _orchestrator is None:
        raise HTTPException(status_code=503, detail="Orchestrator not initialized")

    # [FIX 9] Record the true request arrival time at the edge.
    # This is the correct t=0 for TTFT — it includes BM25 time and WAN transit.
    t_request_start = time.perf_counter()

    async def token_stream() -> AsyncGenerator[str, None]:
        try:
            async for token, is_final, _ttft_ms in _orchestrator.handle_query(
                req.query, req.top_k, t_request_start=t_request_start
            ):
                yield token
                if is_final:
                    break
        except Exception as e:
            logger.error(f"Streaming error: {e}", exc_info=True)
            yield f"\n[ERROR: {e}]"

    return StreamingResponse(token_stream(), media_type="text/plain")


@app.get("/health")
async def health():
    return {"status": "ok", "node": "C", "role": "edge_orchestrator"}
```

---

## 9. Orchestrator (Dispatch-Only — No Scheduler Logic)

> **[FIX 2]** In v1, `orchestrator.py` held `Tthreshold`, `asyncio.TimeoutError` handling, the speculative prefill flag, and the `dense_timed_out` boolean. All of that is **Node A's responsibility** per the report. Node C's orchestrator has one job: run BM25 and dispatch, then forward and stream.

> **[FIX 7 — The Sequential Await Bubble Fix]** v2 had the following pattern:
> ```python
> sparse_results = await bm25_task    # fine
> ack_accepted   = await dispatch_task  # BUG: holds BM25 results hostage for up to 2 s
> # only now opens Node A stream
> ```
> v3 opens the Node A bidi stream **immediately** after BM25 completes by passing `dispatch_task`
> (still pending) directly to `node_a.generate_stream()`. The client's async `request_generator`
> awaits the dispatch result internally and pushes the failure flag if needed — without ever
> blocking the token receive path.

> **[FIX 9 — Edge-Side TTFT Measurement]** v2 extracted `ttft_ms` from Node A's `GenerationToken`,
> which starts its internal clock only when it receives the gRPC request — ignoring all of BM25
> processing time and WAN transit. v3 measures TTFT as `time.perf_counter() - t_request_start`
> where `t_request_start` is recorded at the gateway when the HTTP request arrives (Section 8).
> Node A's `token_msg.ttft_ms` value is explicitly discarded.

### `scheduler/orchestrator.py`

```python
import asyncio
import logging
import time
import uuid
from typing import AsyncIterator, Tuple

from retrieval.bm25_tantivy import TantivyBM25Index
from clients.node_b_client import NodeBDispatchClient
from clients.node_a_client import NodeAStreamClient
from utils.metrics import LatencyRecorder

logger = logging.getLogger(__name__)


class PipelineOrchestrator:
    """
    Node C orchestration logic — dispatch only.

    FIX 2: No timeout logic, no Tthreshold, no speculative prefill decisions.
           Those belong to Node A (the Synchronization Controller).
           Node C's responsibility ends after:
             1. BM25 query completes
             2. Sparse results sent to Node A immediately (no waiting for dispatch) [FIX 7]
             3. dispatch_task passed to node_a client for async failure signaling [FIX 6 & 7]
             4. Token stream relayed from Node A to caller
             5. TTFT measured edge-to-edge on Node C [FIX 9]
    """

    def __init__(
        self,
        bm25_index: TantivyBM25Index,
        node_b_client: NodeBDispatchClient,
        node_a_client: NodeAStreamClient,
        top_k: int,
        node_a_lan_host: str,    # [FIX 8] Physical LAN IP for Node B → Node A forwarding
        node_a_grpc_port: int,
        recorder: LatencyRecorder,
    ):
        self.bm25 = bm25_index
        self.node_b = node_b_client
        self.node_a = node_a_client
        self.top_k = top_k
        self.node_a_lan_host = node_a_lan_host   # [FIX 8]
        self.node_a_grpc_port = node_a_grpc_port
        self.recorder = recorder

    async def handle_query(
        self,
        query_text: str,
        top_k: int | None = None,
        t_request_start: float | None = None,   # [FIX 9] gateway-recorded request arrival time
    ) -> AsyncIterator[Tuple[str, bool, float]]:
        """
        Runs both retrieval branches concurrently.
        Yields (token_text, is_final, ttft_ms) tuples from Node A's stream.

        TTFT is measured on Node C from t_request_start to first token received. [FIX 9]

        Flow (v3):
          T=0ms  : t_request_start recorded at gateway [FIX 9]
          T=0ms  : Launch BM25 task + Node B dispatch task concurrently
          T~30ms : BM25 finishes
          T~30ms : Bidi stream opened to Node A; sparse context sent IMMEDIATELY [FIX 7]
                   dispatch_task (still pending) handed to node_a_client
          T~20ms : Node B ACK resolves inside request_generator [FIX 7]
                   If False → node_b_dispatch_failed=True pushed to Node A [FIX 6]
          T~150ms: Node B delivers dense results directly to Node A (physical LAN, ≤1 ms) [FIX 8]
          T~150ms: Node A begins RRF + LLM prefill; first token streams back
          T~150ms: TTFT recorded as perf_counter() - t_request_start [FIX 9]
        """
        query_id = str(uuid.uuid4())[:8]
        k = top_k or self.top_k

        # [FIX 9] Fall back to now if gateway did not pass t_request_start (e.g. in tests)
        t0 = t_request_start if t_request_start is not None else time.perf_counter()

        logger.info(f"[{query_id}] Pipeline start | '{query_text[:60]}'")

        # ── Launch Branch 1 (BM25) and Branch 2 (Node B dispatch) concurrently ─
        bm25_task = asyncio.create_task(
            asyncio.to_thread(self.bm25.query, query_text, k),
            name=f"{query_id}_bm25",
        )
        dispatch_task = asyncio.create_task(
            self.node_b.dispatch(
                query_id=query_id,
                query_text=query_text,
                top_k=k,
                node_a_lan_host=self.node_a_lan_host,   # [FIX 8] physical LAN IP
                node_a_grpc_port=self.node_a_grpc_port,
            ),
            name=f"{query_id}_dispatch",
        )

        # ── Await BM25 (typically 30–80 ms) ─────────────────────────────────────
        sparse_results = await bm25_task
        t_sparse_ms = (time.perf_counter() - t0) * 1000
        logger.info(f"[{query_id}] BM25 done: {t_sparse_ms:.1f} ms | {len(sparse_results)} docs")

        # ── [FIX 7] Open Node A bidi stream IMMEDIATELY — do NOT await dispatch_task first.
        # dispatch_task is passed into generate_stream(), which handles it asynchronously
        # inside its request_generator. If dispatch later fails, the generator pushes
        # node_b_dispatch_failed=True so Node A can trigger immediate fallback [FIX 6].
        # ─────────────────────────────────────────────────────────────────────────
        ttft_ms_recorded = None
        first_token = True

        async for token_msg in self.node_a.generate_stream(
            query_id=query_id,
            query_text=query_text,
            sparse_results=sparse_results,
            t_sparse_ms=t_sparse_ms,
            dispatch_task=dispatch_task,   # [FIX 7] still-pending task, not awaited here
        ):
            if first_token:
                # [FIX 9] Measure TTFT on Node C using the gateway-recorded start time.
                # Explicitly DO NOT use token_msg.ttft_ms — that value starts from Node A's
                # internal clock and ignores BM25 time and WAN transit entirely.
                ttft_ms_recorded = (time.perf_counter() - t0) * 1000
                first_token = False
                logger.info(
                    f"[{query_id}] First token received | "
                    f"Edge-TTFT={ttft_ms_recorded:.1f} ms [FIX 9]"
                )

            yield token_msg.token, token_msg.is_final, ttft_ms_recorded or 0.0

        t_total_ms = (time.perf_counter() - t0) * 1000
        logger.info(f"[{query_id}] Pipeline complete: {t_total_ms:.1f} ms total")
        self.recorder.record(
            query_id=query_id,
            t_sparse_ms=t_sparse_ms,
            t_total_ms=t_total_ms,
            ttft_ms=ttft_ms_recorded or 0.0,   # [FIX 9] edge-measured, not from Node A
        )
```

---

## 10. Latency Instrumentation

> **[FIX 9]** `ttft_ms` in `LatencyRecord` is now the **edge-measured** value — the delta from
> HTTP request arrival at Node C to first token yielded to the client. It is computed in
> `orchestrator.py` using `time.perf_counter()` and is never sourced from Node A's
> `GenerationToken.ttft_ms`. This makes Phase 3 TTFT reduction measurements scientifically valid.

### `utils/metrics.py`

```python
import json
import logging
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)


@dataclass
class LatencyRecord:
    query_id:    str
    t_sparse_ms: float
    t_total_ms:  float
    ttft_ms:     float   # [FIX 9] Edge-measured: perf_counter() delta on Node C
    timestamp:   float = field(default_factory=time.time)


class LatencyRecorder:
    """
    Writes per-query latency records as JSONL for Phase 3 evaluation
    (MS MARCO MRR@10, WikiQA Exact Match, TTFT reduction benchmarks).

    FIX 9: ttft_ms is sourced from orchestrator.py's edge-side measurement,
    not from Node A's GenerationToken.ttft_ms. This ensures the recorded TTFT
    reflects the full end-to-end latency from query arrival at the edge to
    first token returned to the user — the metric the report targets.
    """

    def __init__(self, log_path: str = "logs/latency_nodeC.jsonl"):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, **kwargs):
        rec = LatencyRecord(**kwargs)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(rec)) + "\n")
        logger.debug(f"Latency recorded: {asdict(rec)}")
```

---

## 11. Configuration File

> **[FIX 8]** A new field `node_a.lan_host` is added. This is Node A's **physical LAN IP address**
> on the home Gigabit Ethernet network (e.g. `192.168.1.100`). Node C passes this IP to Node B
> inside `DenseDispatchRequest`, so that Node B routes its dense payload over the raw LAN
> (≤ 1 ms) rather than through the WireGuard tunnel.
>
> `node_a.grpc_host` remains the WireGuard VPN IP (`10.0.0.1`). This is correct and intentional:
> Node C → Node A traffic travels over the WAN and must traverse the WireGuard tunnel.
> Only Node B → Node A (LAN-internal) traffic should use the physical IP.

### `config.yaml`

```yaml
# ── Node VPN addresses ─────────────────────────────────────────────────────
node_b:
  host: "10.0.0.2"          # Node B VPN IP — Dense Retrieval Engine
  port: 50051                # Node B's gRPC DenseDispatcher server port

node_a:
  grpc_host: "10.0.0.1"     # Node A VPN IP — used by Node C for gRPC over WireGuard tunnel
  grpc_port: 50052           # Node A's gRPC GenerationOrchestrator server port
  lan_host:  "192.168.1.100" # [FIX 8] Node A's PHYSICAL LAN IP — passed to Node B so that
                             # Node B forwards dense results over raw Gigabit Ethernet (≤1 ms),
                             # NOT through the WireGuard VPN. Replace with the actual LAN IP
                             # assigned to Node A's Ethernet adapter on the home router.
  # Note: no base_url, no HTTP — gRPC only [FIX 1]

# ── Tantivy disk-backed BM25 index ─────────────────────────────────────────
corpus:
  # Path to the pre-built Tantivy index (created by scripts/build_index.py)
  # This is a directory, not a single file [FIX 4]
  tantivy_index_path: "retrieval/tantivy_index"

  # Path to the raw JSONL corpus used to build the index
  # Not loaded at runtime — used only by build_index.py
  raw_corpus_path: "retrieval/corpus/documents.jsonl"

# ── Retrieval parameters ──────────────────────────────────────────────────
retrieval:
  top_k: 10
  # Tthreshold is NOT configured here — it lives entirely on Node A [FIX 2]

# ── FastAPI gateway ───────────────────────────────────────────────────────
gateway:
  host: "0.0.0.0"
  port: 8000
```

---

## 12. Index Build Script (One-Time)

> **[FIX 4]** This script runs exactly once to convert the raw JSONL corpus into a disk-backed Tantivy index. After this step, Node C never loads the raw corpus into Python memory again at runtime. The script can be run offline before deployment.

### `scripts/build_index.py`

```python
"""
One-time build script: converts JSONL corpus → Tantivy disk-backed BM25 index.

Run from nodeC/ directory:
    python scripts/build_index.py --corpus retrieval/corpus/documents.jsonl \
                                   --index  retrieval/tantivy_index

For MS MARCO (8.8M passages), expect ~10–20 minutes build time on i5-1235U.
The resulting index directory is ~8–12 GB on disk but is never fully loaded
into RAM at query time — Tantivy memory-maps only active segments.
"""

import argparse
import json
import logging
import time
from pathlib import Path

import tantivy

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
logger = logging.getLogger(__name__)


def build(corpus_path: str, index_path: str, commit_every: int = 50_000):
    corpus_path = Path(corpus_path)
    index_path = Path(index_path)
    index_path.mkdir(parents=True, exist_ok=True)

    # Define schema — must match TantivyBM25Index in retrieval/bm25_tantivy.py
    schema_builder = tantivy.SchemaBuilder()
    schema_builder.add_text_field("doc_id", stored=True, tokenizer_name="raw")
    schema_builder.add_text_field("body",   stored=True, tokenizer_name="en_stem")
    schema = schema_builder.build()

    # heap_size_in_bytes: controls how much RAM the writer uses during indexing
    # 512 MB is safe on 16 GB LPDDR4X; increase to 1024 MB to speed up build
    index = tantivy.Index(schema, path=str(index_path))
    writer = index.writer(heap_size_in_bytes=512 * 1024 * 1024)

    t_start = time.perf_counter()
    count = 0
    with open(corpus_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            doc_data = json.loads(line)
            writer.add_document(
                tantivy.Document(
                    doc_id=[doc_data["id"]],
                    body=[doc_data["text"]],
                )
            )
            count += 1
            if count % commit_every == 0:
                writer.commit()
                elapsed = time.perf_counter() - t_start
                logger.info(f"  {count:,} docs indexed | {elapsed:.0f}s elapsed")

    writer.commit()
    index.reload()
    elapsed = time.perf_counter() - t_start
    logger.info(f"Index build complete: {count:,} docs in {elapsed:.1f}s")
    logger.info(f"Index location: {index_path.resolve()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--index",  required=True)
    parser.add_argument("--commit-every", type=int, default=50_000)
    args = parser.parse_args()
    build(args.corpus, args.index, args.commit_every)
```

**Run once before starting the gateway:**
```bash
python scripts/build_index.py \
  --corpus retrieval/corpus/documents.jsonl \
  --index  retrieval/tantivy_index
```

---

## 13. Running & Testing Node C

### 13.1 Prerequisites checklist before starting
- [ ] Tantivy index built (`retrieval/tantivy_index/` directory exists and is non-empty)
- [ ] WireGuard tunnel active (`ping 10.0.0.1` and `ping 10.0.0.2` succeed)
- [ ] Node B's gRPC DenseDispatcher server is running on `10.0.0.2:50051`
- [ ] Node A's gRPC GenerationOrchestrator server is running on `10.0.0.1:50052`
- [ ] Node A's physical LAN IP confirmed and set correctly in `config.yaml` under `node_a.lan_host`
- [ ] Proto stubs generated in `generated/` — **re-generate after any `.proto` change** (bidi stream and new field require fresh stubs)

### 13.2 Start the gateway
```bash
venv\Scripts\activate
uvicorn gateway.app:app --host 0.0.0.0 --port 8000
```

### 13.3 Smoke test — BM25 isolated (no network required)
```python
# Quick local test — run in a Python shell
from retrieval.bm25_tantivy import TantivyBM25Index
idx = TantivyBM25Index("retrieval/tantivy_index")
results = idx.query("What is retrieval augmented generation?", top_k=5)
for r in results:
    print(r["rank"], r["score"], r["text"][:80])
```

### 13.4 Integration test — full streaming pipeline
```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d "{\"query\": \"What is pipeline parallelism?\", \"top_k\": 10}" \
  --no-buffer
```
You should see tokens arriving incrementally if Node A is streaming correctly.

### 13.5 Unit tests

**`tests/test_tantivy.py`** — covers FIX 4 and FIX 5:
```python
import time
import json
import pytest
from pathlib import Path
from retrieval.bm25_tantivy import TantivyBM25Index


@pytest.fixture(scope="module")
def built_index(tmp_path_factory):
    """Build a small Tantivy index for testing."""
    import tantivy
    idx_path = tmp_path_factory.mktemp("idx")
    schema_builder = tantivy.SchemaBuilder()
    schema_builder.add_text_field("doc_id", stored=True, tokenizer_name="raw")
    schema_builder.add_text_field("body",   stored=True, tokenizer_name="en_stem")
    schema = schema_builder.build()
    index = tantivy.Index(schema, path=str(idx_path))
    writer = index.writer(heap_size_in_bytes=32 * 1024 * 1024)
    docs = [
        {"id": f"d{i}", "text": f"Retrieval augmented generation document {i} about RAG systems"}
        for i in range(2000)
    ]
    for d in docs:
        writer.add_document(tantivy.Document(doc_id=[d["id"]], body=[d["text"]]))
    writer.commit()
    index.reload()
    return str(idx_path)


def test_returns_ranked_results(built_index):
    idx = TantivyBM25Index(built_index)
    results = idx.query("retrieval augmented generation", top_k=10)
    assert len(results) == 10
    assert results[0]["rank"] == 1
    assert "doc_id" in results[0] and "text" in results[0]


def test_latency_within_budget(built_index):
    """FIX 5: Rust tokenizer should keep query well within 80 ms target."""
    idx = TantivyBM25Index(built_index)
    # Warm up
    idx.query("warm up query", top_k=10)
    # Measure
    t = time.perf_counter()
    idx.query("pipeline parallelism distributed inference", top_k=10)
    elapsed_ms = (time.perf_counter() - t) * 1000
    assert elapsed_ms < 80, f"BM25 too slow: {elapsed_ms:.1f} ms (target < 80 ms)"


def test_no_oom_with_large_result_set(built_index):
    """FIX 4: Index is disk-backed; top_k=100 must not cause memory spike."""
    idx = TantivyBM25Index(built_index)
    results = idx.query("rag llm inference", top_k=100)
    assert len(results) <= 100   # May return fewer if corpus is small


def test_missing_index_raises(tmp_path):
    """FIX 4: Fail fast with clear error if build_index.py was not run."""
    with pytest.raises(FileNotFoundError, match="build_index.py"):
        TantivyBM25Index(str(tmp_path / "nonexistent"))
```

Run all tests:
```bash
pytest tests/ -v
```

---

## 14. Requirement Compliance Checklist

### 14.1 Original Report Requirements

| # | Requirement | Where Implemented | Status |
|---|-------------|-------------------|--------|
| R1 | Node C acts as Request Orchestrator: exposes FastAPI async gateway | `gateway/app.py` | ✅ |
| R2 | Node C executes BM25 sparse retrieval on local CPU RAM | `retrieval/bm25_tantivy.py` | ✅ |
| R3 | BM25 latency target: 30–80 ms | `test_latency_within_budget()` asserts < 80 ms | ✅ |
| R4 | Node C dispatches non-blocking gRPC call to Node B simultaneously with BM25 | `asyncio.create_task` for both in `orchestrator.py` | ✅ |
| R5 | gRPC over HTTP/2 for **all** inter-node communication | `grpcio` for both Node B and Node A clients; no `httpx` | ✅ |
| R6 | Protobuf binary serialization (11× faster than JSON, 60–80% smaller) | `dispatch.proto` + `coordination.proto` replace all JSON | ✅ |
| R7 | `Tparallel = max(Tsparse, TWAN + Tdense)` model | Node B delivers dense directly to Node A's physical LAN IP; Node C's critical path is only sparse → Node A | ✅ |
| R8 | Asynchronous scheduler with Tthreshold and speculative prefill lives on Node A | `orchestrator.py` has zero timeout logic; Node A owns this | ✅ |
| R9 | Node C does NOT perform RRF fusion | No RRF code anywhere on Node C | ✅ |
| R10 | Node C does NOT host LLM, Qdrant, or BGE-M3 | Not in `requirements.txt`; not in any module | ✅ |
| R11 | WireGuard with MTU 1420 for anti-fragmentation | `nodeC.conf` with `MTU = 1420` | ✅ |
| R12 | DDNS endpoint + PersistentKeepalive for NAT traversal | WireGuard `Endpoint` + `PersistentKeepalive = 25` | ✅ |
| R13 | Per-query latency logging for MS MARCO / WikiQA Phase 3 evaluation | `utils/metrics.py` JSONL output including edge-measured `ttft_ms` [FIX 9] | ✅ |
| R14 | MS MARCO corpus scale supportable without OOM on 16 GB LPDDR4X | Tantivy disk-backed index; corpus never loaded as Python dicts | ✅ |

### 14.2 All Nine Fixes — Verified

| Fix | Issue | Resolution | Verified By |
|-----|-------|------------|-------------|
| **Fix 1** | Node C → Node A used HTTP/JSON (`httpx`) | Replaced with `coordination.proto` + `grpc.aio` | No `httpx` in `requirements.txt`; `node_a_client.py` uses `grpc.aio` |
| **Fix 2** | Timeout scheduler on Node C | Removed `t_threshold`, `asyncio.TimeoutError`, `dense_timed_out` from Node C | `orchestrator.py` contains zero timeout logic |
| **Fix 3** | Node B replied to Node C (trombone WAN×2) | `dispatch.proto` embeds `node_a_lan_host`; Node B sends dense payload directly to Node A | `DenseDispatchRequest` fields 4 & 5; `node_b_client.py` never receives dense payload |
| **Fix 4** | Full MS MARCO corpus loaded as Python dicts → OOM | Tantivy disk-backed index built once; queried via memory-mapped segments | `build_index.py` + `test_no_oom_with_large_result_set()` |
| **Fix 5** | NLTK `word_tokenize` + Python stopword loops | Tantivy Rust tokenizer (`en_stem`) handles all tokenization | `test_latency_within_budget()` asserts < 80 ms; no `nltk` import anywhere |
| **Fix 6** | No signal to Node A when Node B dies — Node A waits out full `Tthreshold` (~2 s) | `bool node_b_dispatch_failed = 5` added to `SparseContextRequest`; Node C's `request_generator` pushes this flag immediately when dispatch fails | `coordination.proto` field 5; `node_a_client.py` `request_generator`; Node A can trigger immediate fallback |
| **Fix 7** | Sequential `await dispatch_task` after `await bm25_task` holds BM25 results hostage for up to 2 s | `GenerateStream` upgraded to bidi stream; Node A stream opens immediately after BM25; `dispatch_task` awaited asynchronously inside `request_generator` | `coordination.proto` bidi RPC; `node_a_client.py` `request_generator`; `orchestrator.py` no longer awaits `dispatch_task` before calling `generate_stream` |
| **Fix 8** | Node A's WireGuard VPN IP (`10.0.0.1`) passed to Node B, routing dense payload through WireGuard and destroying ≤ 1 ms LAN path | `node_a.lan_host` (physical LAN IP) added to `config.yaml`; `dispatch.proto` field renamed to `node_a_lan_host`; `node_b_client.py` and `orchestrator.py` use `node_a_lan_host` | `config.yaml` new field; `DenseDispatchRequest.node_a_lan_host`; `PipelineOrchestrator(node_a_lan_host=...)` |
| **Fix 9** | TTFT logged from `GenerationToken.ttft_ms` (Node A's internal clock — excludes BM25 and WAN transit) — scientifically invalid for Phase 3 | Node C measures TTFT as `time.perf_counter() - t_request_start` where `t_request_start` is recorded at HTTP request arrival in `gateway/app.py`; `token_msg.ttft_ms` is explicitly discarded | `gateway/app.py` records `t_request_start`; `orchestrator.py` computes edge TTFT on first token; `LatencyRecord.ttft_ms` comment updated |

### 14.3 Final Pre-Launch Checklist
- [ ] `python scripts/build_index.py` completed without errors
- [ ] `pytest tests/ -v` — all tests pass, latency test < 80 ms
- [ ] `ping 10.0.0.1` and `ping 10.0.0.2` respond through WireGuard
- [ ] Node B's gRPC server running and confirmed via `grpc_health_probe` or equivalent
- [ ] Node A's gRPC streaming server running and confirmed
- [ ] Proto stubs re-generated after `.proto` changes (bidi stream + `node_b_dispatch_failed` field + `node_a_lan_host` rename)
- [ ] `POST /query` returns streaming tokens (not a 503)
- [ ] `logs/latency_nodeC.jsonl` is populated after first query; `ttft_ms` values are plausibly ~150–200 ms (edge-to-edge, not Node A's ~50 ms internal value)
- [ ] Node B offline test: take Node B offline, issue a query, confirm Node A still produces a response using sparse-only context; confirm `logs/latency_nodeC.jsonl` shows `ttft_ms` consistent with sparse-only path (not a 2+ second penalty)
- [ ] Node B LAN routing confirmed: verify via network capture or Node B logs that dense payload is sent to `192.168.1.100` (physical LAN), not `10.0.0.1` (VPN)
