# Geo-Distributed Hybrid RAG — Stage-Placement Measurement Study

## What this is

A cache-state-aware measurement study of **stage-to-node placement** in a
geo-distributed *hybrid* RAG pipeline — sparse (Tantivy BM25) + dense
(BGE-M3/Qdrant) retrieval with reciprocal-rank fusion, and a vLLM-served
Qwen3.8-27B generator — across WAN network regimes. The system is deployed on
commodity hardware: an **RTX 3090 generation host (Node A)** and a
**GTX 1660 Ti laptop gateway (Node B)** connected over a WireGuard link shaped
by bidirectional Linux `netem` (0/15/40/80 ms). A **3-node variant (Node C:
sparse + user-facing tier)** is reserved but not yet brought up.

The study answers three research questions (see
`proposal/research_proposal.tex`):

- **RQ1 (Placement)** — which placement of each stage minimizes TTFT and
  end-to-end latency under a given network regime, and where does the best
  placement change?
- **RQ2 (Degradation)** — when a node drops out, what is the cost in latency
  and retrieval quality, and how fast can the system adapt?
- **RQ3 (Policy)** — can a simple analytical cost model predict the best
  placement within a stated tolerance and drive live per-regime selection?

Headline findings (see `paper/main.pdf`): TTFT is essentially flat across the
swept RTT range (0.73 ms TTFT / ms RTT); KV-cache state, not the network,
dominates latency variance (cold ≈ 3.5 s vs warm ≈ 0.82 s, 4.4×); and SPHP
(Speculative Progressive Hydration & Prefill) cuts cold TTFT by 17.4% at every
regime (measured hit rate h = 0.84).

## Repository layout

```
experiments/
  bench/       campaign driver, per-mode eval driver, query pools, raw campaign JSONL
  eval/        retrieval-quality scorer, quality-report generator, answer-level SPHP comparison
  analysis/   campaign-driven cost model, publication figures, fitted results
systems/
  node_a/      generation host (vLLM Qwen3.8-27B + local corpus store)
  node_b/      edge gateway (Tantivy + BGE-M3/Qdrant + RRF) — docker-compose for Qdrant
  node_c/      sparse + user-facing tier (reserved; config only)
scripts/
  netem/       bidirectional WAN shaping (apply/clear/show/validate) — root required
  node_a/      orchestrator lifecycle (start/stop_stack.sh)
  node_c_prep/ Node C bring-up checklist, preflight, benchmark
  dev/         GPU / Tantivy / segment dev utilities
  policy_demo.py, transfer_*.sh, forward_grpc.py, make_latency_sample.py
paper/         main.tex + main.pdf + figures/ (fig1–fig8)
proposal/      research_proposal.tex + .pdf (RQ1–RQ3, method)
docs/          NODE_ACCESS, RUNBOOK, TESTBED, B_MIGRATION
start_gateway.bat   Node B gateway launcher (run by the pdc_gateway scheduled task)
```

## Key artifacts

| Artifact | What it is |
|----------|------------|
| `paper/main.pdf` | The paper (ICDCS) — findings, figures, cost model |
| `experiments/analysis/results/cost_model_validation.json` | Campaign-fitted cost model + regime-level LOTO validation (cold/warm) |
| `experiments/analysis/results/crossover_analysis.json` | Warm-fitted RTT×BW placement table (primary policy artifact) |
| `experiments/analysis/results/crossover_analysis_cold.json` | Cold-fitted placement table (transient regime) |
| `experiments/analysis/results/live_policy_demo_summary.json` | RQ3 live policy: realized vs predicted TTFT per regime/state |
| `experiments/analysis/results/live_retrieval_quality.json` | 500-query seeded retrieval quality (MRR/nDCG/Recall) |
| `experiments/analysis/results/answer_comparison.json` | Answer-level SPHP hit-path agreement (F-25) |
| `experiments/bench/campaigns/campaign_*.jsonl` | Raw per-request timing records (the measurement evidence) |

## Quick start

Full detail, flags, and the netem/GPU-window discipline are in
[`docs/RUNBOOK.md`](docs/RUNBOOK.md). All commands run from the repo root on
Node A.

```bash
# 1. Stack up — vLLM engine must already be running on :18020
bash scripts/node_a/start_stack.sh        # orchestrator gRPC :50052 + HTTP :8001
```

```bash
# 2. Health check the Node B gateway
curl -s http://10.8.0.2:8000/health       # expect "role":"hybrid_retrieval_gateway"
```

```bash
# 3. Run the latency campaign (4 regimes × SPHP on/off, hybrid)
python3 experiments/bench/campaign.py --queries experiments/bench/queries/latency_dev480_seed42.txt --rtt-tiers 0 15 40 80 --loss-tiers 0 --modes hybrid --sphp-axis --query-sample 50 --seed 42 --warmups 5 --repeats 3 --out-dir experiments/bench/campaigns
```

```bash
# 4. Refit the cost model + regenerate publication figures
python3 experiments/analysis/cost_model.py && python3 experiments/analysis/figures.py
```

```bash
# 5. Live placement-policy demo (RQ3)
python3 scripts/policy_demo.py --queries experiments/bench/queries/latency_dev480_seed42.txt --per-regime 5 --warmups 2 --repeats 3
```

## Docs

| Doc | Purpose |
|-----|---------|
| [`docs/NODE_ACCESS.md`](docs/NODE_ACCESS.md) | How to reach/operate each node — aliases, IPs, ports, deploy loop |
| [`docs/RUNBOOK.md`](docs/RUNBOOK.md) | Operations — running the stack, campaigns, analysis, eval, policy demo |
| [`docs/TESTBED.md`](docs/TESTBED.md) | Hardware + measurement infrastructure — topology, netem, protocol, corpus |
| [`docs/B_MIGRATION.md`](docs/B_MIGRATION.md) | One-time Node B restructure cutover (`node_B/` → `systems/node_b/`) |
| [`scripts/node_c_prep/NODE_C_CHECKLIST.md`](scripts/node_c_prep/NODE_C_CHECKLIST.md) | Node C bring-up (sparse + user-facing tier) |

## Status

Branch **`two-node-deployment`**. The **2-node milestone is complete** (Node A
generation + Node B gateway measured across all four WAN regimes; cost model
fitted and validated; RQ3 policy demo live). The **3-node variant is pending
Node C bring-up** (2026-09-19) — see `NODE_C_CHECKLIST.md`.
