# Artifact Evaluation & Reproducibility Guide

This guide details the deterministic reproduction of all figures, tables, and cost models presented in the paper:
> **"Take the Hint: Speculative Prefill Acceleration for Geo-Distributed Hybrid RAG"**

---

## 1. Quick Reproduction (Precomputed Factorial Campaign)

All raw measurement records, regime sweeps, and confirmatory logs are included in the repository. You can verify and regenerate all publication figures and tables without needing physical multi-GPU hardware.

### 1.1 Environment Setup
```bash
# Recommended Python 3.10+
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 1.2 Run Test Suite
```bash
pytest
# Expect: 199 passed
```

### 1.3 Regenerate All Publication Figures (Figures 1–8)
```bash
python3 experiments/analysis/figures.py
```
This reads the campaign records from `experiments/analysis/results/` and `experiments/bench/campaigns/` and renders vector PDFs directly into `paper/figures/`:
* `fig1_arch.pdf`: Multi-tier systems architecture & SPHP execution dataflow.
* `fig2_topologies.pdf`: Placement topology taxonomy (P0–P3).
* `fig3_sphp_timeline.pdf`: Space-time execution swimlanes comparing baseline P2 vs. SPHP at 40 ms WAN RTT.
* `fig4_stage_breakdown.pdf`: TTFT latency breakdown (retrieval barrier vs. prefill residual) across cache states.
* `fig5_ttft_vs_rtt.pdf`: Measured TTFT vs. WAN RTT (0–80 ms) with 95% bootstrap confidence intervals.
* `fig6_crossover_map.pdf`: Placement crossover analysis across 35 swept (RTT, Bandwidth) cells.
* `fig7_retrieval_quality.pdf`: Live retrieval quality (MRR, nDCG, Recall) across rank depths.
* `fig8_cost_model_parity.pdf`: Analytical cost model parity and held-out regime MAPE.

### 1.4 Validate Analytical Cost Model (Equations 3–7)
```bash
python3 experiments/analysis/cost_model.py
```
Output:
* Reports Leave-One-Tier-Out (LOTO) validation MAPE:
  - Baseline P2: 2.15% (cold) / 4.13% (warm)
  - SPHP: 6.42% (cold) / 4.37% (warm)
* Updates `experiments/analysis/results/cost_model_validation.json` and crossover maps.

---

## 2. Live Factorial Campaign Execution (Physical Multi-Node Testbed)

To reproduce the measurement campaign against active hardware (Node A generation host, Node B gateway, Node C edge ingress):

### 2.1 Start Service Stack
```bash
# On Node A (workstation with RTX 3090, vLLM engine running on :18020):
bash scripts/node_a/start_stack.sh

# On Node B (gateway with GTX 1660 Ti, Qdrant running on :6333):
scripts/launch/start_gateway_node_b.bat

# On Node C (edge tier with Tantivy BM25 on MS MARCO):
scripts/launch/start_gateway_node_c.bat
```

### 2.2 Verify Health & Ingress
```bash
curl -s http://10.8.0.2:8000/health   # Node B
curl -s http://10.8.0.3:8000/health   # Node C
curl -s http://127.0.0.1:8001/health  # Node A
```

### 2.3 Run Factorial Campaign (2,880 Confirmatory Queries)
```bash
python3 experiments/bench/campaign.py \
    --gateway-url http://10.8.0.2:8000 \
    --queries experiments/bench/queries/latency_dev480_seed42.txt \
    --rtt-tiers 0 15 40 80 \
    --loss-tiers 0 \
    --modes hybrid \
    --sphp-axis \
    --query-sample 120 \
    --seed 42 \
    --warmups 5 \
    --repeats 3 \
    --out-dir experiments/bench/campaigns
```

### 2.4 Run Fault Injection & Self-Healing Stress Test (Section VII-C)
```bash
python3 experiments/bench/rq2_driver.py \
    --gateway-url http://10.8.0.2:8000 \
    --mode hybrid \
    --sphp \
    --out-dir experiments/bench/rq2_runs
```

---

## 3. Data Provenance & Key Artifacts

| Paper Reference | Underlying Source Artifact | Description |
|:---|:---|:---|
| **Table II** (P2 Stage Latency) | `experiments/bench/campaigns/summary_v2_confirm_2node_20261013.json` | 2,880-record confirmatory benchmark |
| **Table III** (Argmin Placement Policy) | `experiments/analysis/results/live_policy_demo_summary.json` | Predicted vs. realized warm TTFT |
| **Table IV** (CM-2 / CM-4 / CM-5) | `experiments/bench/campaigns/summary_20261021T135808Z.json` | Multi-topology & multi-leg stress matrices |
| **Figure 5** (TTFT vs RTT) | `experiments/bench/campaigns/campaign_v2_confirm_2node_20261013.jsonl` | Raw per-request timing records |
| **Figure 7** (Retrieval Quality) | `experiments/analysis/results/live_retrieval_quality.json` | 500-query seeded MS MARCO retrieval quality |
| **Figure 8** (Cost Parity) | `experiments/analysis/results/cost_model_validation.json` | LOTO cross-validation metrics |
