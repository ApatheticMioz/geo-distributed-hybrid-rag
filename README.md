# Take the Hint: Speculative Prefill Acceleration for Geo-Distributed Hybrid RAG

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Artifacts: Reproducible](https://img.shields.io/badge/Artifacts-Reproducible%20(199%20tests)-brightgreen.svg)](REPRODUCIBILITY.md)
[![Status: Confirmatory Evaluated](https://img.shields.io/badge/Benchmark-2%2C880%20Queries%20(N%3D0%20failures)-success.svg)](experiments/bench/campaigns/)

This repository hosts the official implementation, measurement harness, analytical cost model, raw confirmatory records, and figure generation suite for:
> **"Take the Hint: Speculative Prefill Acceleration for Geo-Distributed Hybrid RAG"**

---

## 💡 Executive Summary & Core Systems Insights

Deploying hybrid Retrieval-Augmented Generation (RAG)—combining lexical sparse search (Tantivy BM25), dense neural embeddings (BGE-M3 + Qdrant HNSW), reciprocal-rank fusion (RRF), SQLite passage hydration, and LLM generation (vLLM Qwen3.8-27B)—across geographically distributed edge–cloud fleets introduces fundamental stage placement trade-offs. 

Through a **2,880-record confirmatory benchmark** swept across four WAN regimes (0 to 80 ms RTT) under bidirectional Linux `netem` shaping, our measurements reveal:

1. **TTFT is Flat in WAN Latency ($\approx$0.95 ms/ms RTT)**: Identifier-sized boundary payloads (144 bytes for top-10 IDs) decouple network transit delay from payload bulk.
2. **KV-Cache State Dominates Latency Variance**: Cold-prefix TTFT ($\approx$1.7 s) reaches **$2.8$–$3.2\times$** warm-state latency ($\approx$0.55 s), overshadowing network variations across all WAN regimes.
3. **SPHP Eliminates the Prefill Bottleneck**: By streaming an early provisional sparse hint ($\mathcal{H}$, 104 bytes) to the generation host immediately upon sparse index completion, **SPHP** (Speculative Progressive Hydration & Prefill) overlaps masked cloud prefill with in-flight dense retrieval and fusion.
   * **Measured Hit Rate**: $h = 0.900$ (95% CI $[0.883, 0.914]$) with mean candidate set overlap of $0.636$.
   * **Cold TTFT Reduction**: Cuts cold TTFT by **$23.7\%$–$27.6\%$** across all regimes (Holm-FWER $p \le 0.0008$, Cohen's $d = -1.29$ to $-1.57$).
   * **Answer Quality**: Bounded within decode sampling stochasticity (token-F1 $0.422$ vs. baseline repeat $0.586$).
4. **Analytical Placement Cost Model**: A closed-form analytical model achieves a leave-one-tier-out (LOTO) validation MAPE of **2.15% (cold) / 4.13% (warm)** for baseline P2 and **6.42% (cold) / 4.37% (warm)** for SPHP. Crossover sweeps across 35 (RTT, bandwidth) cells prove that SPHP split placement is globally optimal.
5. **Generalization across Hostile Topologies**:
   * **Physical 3-Node Tiering (CM-2)**: 16.9%–26.7% cold TTFT drop; warm TTFT sensitivity to Link B RTT drops to $0.153$ ms/ms.
   * **Live Public WAN Routes (CM-4)**: 24.4% (2-node) and 23.9% (3-node) cold TTFT reductions under authentic internet jitter.
   * **Multi-Leg Loss Stress Matrix (CM-5)**: Evaluated across 27 cells (54 arms, 810 queries, up to 2% loss per link); SPHP maintains a 24.7% mean speedup and $86.9\%$ hit rate.
   * **Transient Edge Fault Recovery**: Edge dropouts self-heal in 64–86 s with zero post-recovery errors and latency settling within 1.4% of baseline.

---

## 🏛️ System Architecture & Dataflow

```
   ┌────────────────────────┐      ┌─────────────────────────┐      ┌─────────────────────────┐
   │  Node C (Edge Ingress) │      │  Node B (Neural Gateway)│      │  Node A (Cloud Host)    │
   │  Tantivy BM25 (8.84M)  │      │  BGE-M3 + Qdrant HNSW   │      │  vLLM Qwen3.8-27B W4A16 │
   └──────────┬─────────────┘      └────────────┬────────────┘      └────────────┬────────────┘
              │                                 │                                │
    Query In  │                                 │                                │
    ─────────►│                                 │                                │
              │── (1) Forward Dense Query ─────►│                                │
              │                                 │── (3) Dense Encoding + HNSW    │
              │── (2) Stream Sparse Hint (H) ───┼───────────────────────────────►│
              │       (104 B provisional IDs)   │                                │── (5) Speculative
              │                                 │                                │       Masked Prefill
              │                                 │── (4) RRF Fusion Reciprocal    │       & SQLite Hydrate
              │                                 │── (4) Verified Context (F) ───►│
              │                                 │       (144 B top-k IDs)        │── (5) Overlap Gate
              │                                 │                                │       Commit (h=0.900)
              │◄────────────────────────────────┴────────────────────────────────┼── (6) SSE Token Stream
    Client    │                                                                  │       (35.3 tok/s)
```

---

## 📊 Placement Topology Taxonomy

| Topology | Paradigm | Client Tier | Edge Gateway (Node B) | Cloud Host (Node A) |
|:---|:---|:---|:---|:---|
| **P0** | Colocated Monolithic | Ingress | --- | All pipeline stages colocated |
| **P1** | Edge Gateway Proxy | Ingress | Gateway reverse proxy | All pipeline stages in cloud |
| **P2** | Split Baseline | Ingress | Sparse, Dense, RRF Fusion | SQLite Hydrate, Prefill, Decode |
| **P2-SPHP** | **Speculative Split** | Ingress | Sparse, Dense, RRF Fusion | SQLite Hydrate, **Speculative Prefill**, Decode |
| **P3** | Edge Hydrate | Ingress | Sparse, Dense, Fusion, Hydrate | Prefill, Decode |

---

## 📂 Repository Structure

```text
├── LICENSE                             # Apache 2.0 Open Source License
├── README.md                           # This document
├── REPRODUCIBILITY.md                  # Comprehensive artifact evaluation & reproduction runbook
├── pyproject.toml                      # Standard Python packaging & pytest config
├── proto/                              # Centralized gRPC & Protocol Buffer schemas
│   ├── coordination.proto
│   ├── hybrid_coordination.proto
│   ├── result_forward.proto
│   └── dispatch.proto
├── paper/                              # Camera-ready publication manuscript
│   ├── main.tex                        # Complete IEEE conference LaTeX source
│   ├── main.pdf                        # Compiled paper
│   ├── references.bib                  # Sourced bibliography
│   └── figures/                        # Publication vector figures (fig1–fig8)
├── systems/                            # Heterogeneous multi-tier implementation
│   ├── node_a/                         # Tier 1: Cloud Generation Host (vLLM + SQLite hydration)
│   ├── node_b/                         # Tier 2: Regional Neural Gateway (BGE-M3 + Qdrant + RRF)
│   └── node_c/                         # Tier 3: Edge Ingress Tier (Tantivy BM25 + SPHP hint)
├── experiments/                        # Factorial measurement & analysis suite
│   ├── bench/                          # Automated benchmark drivers & query pools
│   │   ├── campaign.py                 # Factorial WAN measurement campaign driver
│   │   ├── rq2_driver.py               # Fault injection & edge dropout driver
│   │   ├── campaigns/                  # Canonical confirmatory raw records (2,880 queries)
│   │   └── queries/                    # Seeded MS MARCO test query partitions
│   ├── eval/                           # Retrieval quality & answer divergence scoring
│   └── analysis/                       # Cost model fitting & deterministic figure generation
│       ├── cost_model.py               # Closed-form cost model & LOTO cross-validation
│       ├── figures.py                  # Generates Figures 1-8 to paper/figures/
│       └── results/                    # Validated results, crossover sweeps, and parity logs
├── scripts/                            # Operational & network control tooling
│   ├── compile_protos.sh               # One-step Protocol Buffer compiler
│   ├── netem/                          # Bidirectional Linux netem shaping (apply, clear, validate)
│   ├── launch/                         # Portable service launch scripts
│   └── node_a/                         # Cloud stack orchestration (start_stack.sh, stop_stack.sh)
└── docs/                               # Systems testbed specifications & runbooks
    ├── TESTBED.md                      # Physical hardware & testbed infrastructure
    ├── RUNBOOK.md                      # Operational runbook for live campaigns
    └── NODE_ACCESS.md                  # Inter-node access matrix & network layout
```

---

## 🚀 One-Click Reproducibility

### 1. Install Dependencies
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Verify Test Suite (199 Tests Passing)
```bash
pytest
```

### 3. Regenerate Publication Figures (Figures 1–8)
```bash
python3 experiments/analysis/figures.py
```
Outputs vector PDFs into `paper/figures/`:
* `fig1_arch.pdf`: Multi-tier system architecture and execution dataflow.
* `fig2_topologies.pdf`: Topology component-placement matrix.
* `fig3_sphp_timeline.pdf`: Space-time execution swimlanes (baseline P2 vs. SPHP).
* `fig4_stage_breakdown.pdf`: TTFT stage composition across network regimes.
* `fig5_ttft_vs_rtt.pdf`: Measured TTFT vs. WAN RTT (0–80 ms) with bootstrap 95% CIs.
* `fig6_crossover_map.pdf`: Placement crossover curves over (RTT, Bandwidth).
* `fig7_retrieval_quality.pdf`: Seeded 500-query MS MARCO retrieval quality.
* `fig8_cost_model_parity.pdf`: Regime-level cost model parity and LOTO MAPE annotations.

### 4. Validate Analytical Cost Model
```bash
python3 experiments/analysis/cost_model.py
```

---

## 📑 Key Experimental Artifacts

| Paper Reference | Artifact File | Description |
|:---|:---|:---|
| **Table II** (Latency Decomposition) | [`experiments/bench/campaigns/summary_v2_confirm_2node_20261013.json`](experiments/bench/campaigns/summary_v2_confirm_2node_20261013.json) | 2,880-query confirmatory measurement baseline |
| **Table III** (Argmin Policy Verification)| [`experiments/analysis/results/live_policy_demo_summary.json`](experiments/analysis/results/live_policy_demo_summary.json) | Realized vs. predicted TTFT (pooled error 5.57%) |
| **Table IV** (CM-2, CM-4, CM-5) | [`experiments/bench/campaigns/summary_20261021T135808Z.json`](experiments/bench/campaigns/summary_20261021T135808Z.json) | Multi-topology, public internet, and 27-cell stress records |
| **Section V-C** (Answer Drift) | [`experiments/analysis/results/answer_comparison.json`](experiments/analysis/results/answer_comparison.json) | Token-F1 and exact match agreement over 200 seeded queries |
| **Section VI-B** (Cost Parity) | [`experiments/analysis/results/cost_model_validation.json`](experiments/analysis/results/cost_model_validation.json) | Leave-one-tier-out regime validation logs |

---

## 📜 Citation

If you use this codebase, cost models, or empirical findings in your research, please cite:

```bibtex
@article{ali2026takethehint,
  title     = {Take the Hint: Speculative Prefill Acceleration for Geo-Distributed Hybrid RAG},
  author    = {Ali, Muhammad Abdullah and Aamir, Muhammad Abdullah and Majeed, Adil},
  year      = {2026}
}
```

---

## 📄 License

This project is licensed under the [Apache License, Version 2.0](LICENSE).
