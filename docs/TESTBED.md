# Testbed — Hardware & Measurement Infrastructure

Hardware and measurement-infrastructure description of the 2-node testbed.
Every number below is sourced from the ground-truth files listed at the
bottom. Operations live in `docs/RUNBOOK.md`; endpoints/aliases in
`docs/NODE_ACCESS.md`.

---

## 1. Topology

```
Client
  │  HTTP query
  ▼
Node B  10.8.0.2   (edge gateway — laptop: Intel i7-10750H, GTX 1660 Ti)
  │  • Tantivy BM25 sparse index (full 8.84M passages)
  │  • BGE-M3 dense encoder → 1024-dim HNSW in Qdrant
  │  • local Reciprocal Rank Fusion (RRF)
  │  gRPC stream over WireGuard (fused top-k context, or SPHP sparse hint)
  ▼
Node A  10.8.0.1   (generation — RTX 3090 workstation, 24 GB VRAM)
  │  • local SQLite store: full 8,841,823-passage MS MARCO corpus (O(1) hydration)
  │  • vLLM serving Qwen3.8-27B, W4A16-quantized (AWQ)
  │  • per-request think suppression (chat-template kwargs) so TTFT
  │    measures the answer stream, not chain-of-thought tokens
  │  gRPC stream over WireGuard (decoded token stream)
  ▼
Client  (progressive token stream)
```

The two nodes are connected over WireGuard (Node A `10.8.0.1`, Node B
`10.8.0.2`) with gRPC streaming for the query, the fused context, and the
returned token stream. The deployed placement is **P2**: sparse, dense, and
fusion on Node B; hydration, prefill, and decode on Node A — only the top-k
document identifiers cross the link, not the corpus text.

**Generation backend (Node A, `systems/node_a/implementation/src/config.py`):**
external OpenAI-compatible endpoint `http://127.0.0.1:18020/v1`, model
`qwen3.8-27b`, AWQ quantization, `GPU_MEMORY_UTILIZATION=0.90`,
`MAX_MODEL_LEN=4096`, `ENFORCE_EAGER=True`, `MAX_TOKENS=512`,
`TEMPERATURE=0.2`, `RRF_K=60`, `T_THRESHOLD_MS=160`.

---

## 2. Network control

The A–B link is shaped with Linux `netem` **bidirectionally**
(`scripts/netem/apply.sh`):

- **Egress** — a delaying qdisc on Node A's egress interface (`eth4`) applies
  `rtt/2` of propagation to outbound packets.
- **Ingress** — an `ifb` redirect (`ifb0`) applies `rtt/2` to inbound packets.

Because both directions are shaped, an end-to-end ping RTT increases by exactly
the configured value, and every crossing of the A–B boundary (query forward,
context return, hint forward, token stream back) experiences the configured
delay. This is the contrast with an **application-level sleep** injected at one
endpoint, which inflates only the metric it touches and reproduces no queuing
or serialization behavior.

| Property | Value |
|----------|-------|
| Regimes swept | 0, 15, 40, 80 ms RTT (LAN / near-edge / wide-area) |
| Link | nominal 100 Mbps WireGuard |
| Validation | ping probe before every measured window, **±30% gate** (e.g. the 15 ms regime measured 14.3 ms) |

`scripts/netem/validate.sh RTT_MS` runs the closed-loop check (clear → baseline
ping → apply → shaped ping; PASS if the RTT delta is within ±30% of `RTT_MS`)
and always clears in a trap.

---

## 3. Measurement protocol

All reported numbers come from a single scripted campaign
(`experiments/bench/campaign.py`):

| Parameter | Value |
|-----------|-------|
| Regimes | 4 (0 / 15 / 40 / 80 ms) |
| Arms | 2 (P2 baseline, P2-SPHP) |
| Measured queries per arm | 50, sampled with **seed 42** from the 480-query latency pool (`experiments/bench/queries/latency_dev480_seed42.txt`) |
| Query slices | **disjoint per arm** — no query is seen by both arms, so cross-arm cache warm-up cannot contaminate comparisons |
| Warmups per arm | 5, executed but **discarded** |
| Recorded repeats per query | 3 |
| Total measured records | 1,200 (zero failures) |
| top-k | 10 |

**Cache-state semantics.** The engine has no flush endpoint, so **repeat 0 of
each arm slice is the cold-prefix reference** (reported explicitly rather than
pretending order-independence); repeats 1–2 are warm. Statistics reported
throughout: means, medians, p95, and bootstrap 95% CIs (2,000 resamples,
seed 42). Retrieval quality is evaluated separately on 500 seeded dev queries
against qrels; answer-level SPHP effects on 200 seeded queries.

**Fitted model (from `experiments/analysis/results/cost_model_validation.json`,
1,200 records; 400 cold / 800 warm; top-k 10; regimes 0/15/40/80 ms):**

| Component | Cold (repeat 0) | Warm (repeats 1–2) |
|-----------|-----------------|--------------------|
| t_sparse (ms) | 1711.29 | 74.80 |
| t_dense (ms) | 2680.85 | 190.80 |
| t_fusion (ms) | 0.0175 | 0.0192 |
| t_prefill (ms) | 850.71 | 633.27 |
| decode (tok/s) | 35.56 | 35.18 |
| SPHP hit rate (measured) | 0.84 | 0.84 |
| SPHP miss penalty (ms) | 2697.8 | 193.6 |

Measured warm RTT sensitivity: slope **0.729 ms TTFT / ms RTT** (the
analytical `rtt/2`-per-crossing WAN term is an upper bound on this response).

---

## 4. Corpus

- **8,841,823** MS MARCO passages.
- **Dense:** BGE-M3 encoder, **1024-dimensional HNSW** index in Qdrant (Node B).
- **Sparse:** Tantivy BM25 full index over the same 8.84M passages (Node B).
- **Hydration:** full corpus in a local SQLite store on Node A for O(1)
  passage lookup.

---

## Ground-truth sources

- `paper/main.tex` §III — Testbed, Network Control, Measurement Protocol
- `systems/node_b/docker-compose.yml` — Qdrant `qdrant_node_b` (6333/6334)
- `systems/node_a/implementation/src/config.py` — generation backend, RRF_K, T_THRESHOLD
- `scripts/netem/apply.sh` (header) — bidirectional half-RTT-per-direction contract
- `experiments/bench/campaign.py` (docstring) — arm/query/repeat semantics
- `experiments/analysis/results/cost_model_validation.json` — fitted params, regimes, decode, RTT sensitivity
