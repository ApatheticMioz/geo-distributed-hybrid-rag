# Operational Runbook

How to run the stack and the experiment pipeline in the restructured repo.
Every command below is derived from the actual argparse/CLI code in the
scripts; every path is a real path in this tree. Node access facts (aliases,
IPs, ports) are in `docs/NODE_ACCESS.md` — this runbook does not repeat them.

All commands run from the repo root on Node A (this WSL host) unless stated.

---

## 1. One-time setup pointers

| Task | Where |
|------|-------|
| Node B restructure cutover (`node_B/` → `systems/node_b/`, commit `1d76c8e`) — **do once before the deploy loop works** | `docs/B_MIGRATION.md` |
| Node C bring-up (WireGuard peer `10.8.0.3/24`, venv, Tantivy index transfer, preflight, e2e smoke) | `scripts/node_c_prep/NODE_C_CHECKLIST.md` |

---

## 2. Running the stack

**Prerequisite — vLLM engine (Node A).** The `qwen3.8-27b` engine at
`localhost:18020` is managed out-of-band (`D:\LLM_Ecosystem\scripts\main`) and
must already be running. The stack scripts do **not** start or stop it.

**Orchestrator (Node A):**

```bash
bash scripts/node_a/start_stack.sh   # gRPC :50052 + HTTP :8001; fail-fast, 30s wait
bash scripts/node_a/stop_stack.sh    # SIGTERM then SIGKILL; removes pid file
```

**Health checks:**

```bash
# Node A orchestrator (gRPC :50052, HTTP :8001)
ss -ltn | grep -E ':50052 |:8001 '
curl -s http://127.0.0.1:8001/health

# Node B gateway (:8000) — from Node A over WireGuard, or on B itself
curl -s http://10.8.0.2:8000/health     # expect "role":"hybrid_retrieval_gateway"
```

**Node B gateway lifecycle** (PowerShell on B, from clone root — see
`docs/NODE_ACCESS.md` §2): `schtasks /Run /TN pdc_gateway` /
`schtasks /End /TN pdc_gateway`; Qdrant via
`docker compose -f systems\node_b\docker-compose.yml up -d`.

---

## 3. Latency campaign

`experiments/bench/campaign.py` sweeps `(rtt, loss) × mode × variant × query ×
repeat` against the live Node B gateway `/query/benchmark`, shaping the WAN
path with `scripts/netem/apply.sh`/`clear.sh` per regime.

**Paper final matrix (4 WAN regimes × SPHP on/off, hybrid):**

```bash
python3 experiments/bench/campaign.py \
    --gateway-url http://10.8.0.2:8000 \
    --queries experiments/bench/queries/latency_dev480_seed42.txt \
    --rtt-tiers 0 15 40 80 \
    --loss-tiers 0 \
    --modes hybrid \
    --sphp-axis \
    --query-sample 50 --seed 42 \
    --warmups 5 --repeats 3 \
    --out-dir experiments/bench/campaigns
```

**Plan only** (no netem, no HTTP, no files):

```bash
python3 experiments/bench/campaign.py --dry-run
```

**Key flags** (defaults in parentheses): `--gateway-url`
(`http://10.8.0.2:8000`), `--queries` (one-query-per-line file),
`--rtt-tiers` (`0 15 40 80`), `--loss-tiers` (`0 1`), `--modes`
(`hybrid dense sparse`), `--sphp-axis` (hybrid runs `baseline`+`sphp` variants),
`--query-sample` N (`50`), `--seed` (`42`), `--repeats` R (`3`), `--warmups` W
(`5`), `--top-k` (`10`), `--timeout` (`120`), `--out-dir`
(`benchmarks/campaigns` — pass `experiments/bench/campaigns` to match this tree).

**Outputs** (in `--out-dir`, timestamped `run_id`):

| File | Content |
|------|---------|
| `campaign_<run_id>.jsonl` | one full-timing record per measured request |
| `summary_<run_id>.csv` | per `(rtt, loss, mode, variant)` summary |
| `summary_<run_id>.json` | same summary + run metadata |

**Warmup / repeat semantics.** Each arm gets a *disjoint* slice of a seeded
query pool; the first W of the slice are warmups (executed, **not** recorded).
The remaining N are measured, each served R times back-to-back. There is no
cache-flush endpoint, so **repeat 0 is the cold-prefix reference** and repeats
1..R-1 are warm. Disjoint slices guarantee no arm's repeat-0 prefixes are
pre-cached by an earlier arm.

**Netem discipline (root required).** `scripts/netem/*` need `CAP_NET_ADMIN`
and contain no sudo — run as root:

```bash
wsl.exe -u root -e bash /home/apath/Work/PDC/Project/scripts/netem/apply.sh 15 5
wsl.exe -u root -e bash /home/apath/Work/PDC/Project/scripts/netem/clear.sh
wsl.exe -u root -e bash /home/apath/Work/PDC/Project/scripts/netem/validate.sh 15
```

`campaign.py` calls `apply.sh`/`clear.sh` itself per regime (skipped for the
`rtt=0 loss=0` baseline) and clears before starting and after each regime.
`validate.sh RTT_MS` is a closed-loop check (clear → baseline ping → apply →
shaped ping; PASS if the RTT delta is within ±30% of `RTT_MS`) and always
clears in a trap. Validate shaping **before** a measured window; never leave a
shaped path active behind a crashed run.

---

## 4. Analysis

**Cost model** — `experiments/analysis/cost_model.py`. Fits a
`PlacementCostModel` per cache state (cold = repeat 0, warm = repeat ≥1) from a
campaign JSONL, runs regime-level leave-one-tier-out validation, and writes the
placement-policy crossover tables.

```bash
# newest campaign_*.jsonl under experiments/bench/campaigns (default)
python3 experiments/analysis/cost_model.py
# or a specific campaign file:
python3 experiments/analysis/cost_model.py experiments/bench/campaigns/campaign_<run_id>.jsonl
```

**Outputs** (in `experiments/analysis/results/`):

| File | Content |
|------|---------|
| `cost_model_validation.json` | fitted params + LOTO validation per cache state |
| `crossover_analysis.json` | warm-fitted RTT×BW placement table (primary) |
| `crossover_analysis_cold.json` | cold-fitted table (transient regime) |
| `crossover_analysis.csv` | warm table as CSV |

**Figures** — `experiments/analysis/figures.py` (no CLI args). Reads the newest
campaign, `cost_model_validation.json`, `crossover_analysis{,_cold}.json`, and
`live_retrieval_quality.json`; writes IEEE-styled vector PDFs to
`paper/figures/`:

```bash
python3 experiments/analysis/figures.py
# -> paper/figures/fig1_arch.pdf ... fig8_cost_model_parity.pdf
```

---

## 5. Quality + answer evaluation

**Retrieval-only run** — `experiments/bench/run_eval.py` drives the Node B
`/query/benchmark` endpoint in one mode and writes per-query ranked doc-ids +
timings. `--retrieval-only` skips Node A generation (quality runs).

```bash
python3 experiments/bench/run_eval.py \
    --gateway http://10.8.0.2:8000 \
    --queries /home/apath/Work/PDC/data/msmarco/queries.dev.tsv \
    --qrels   /home/apath/Work/PDC/data/msmarco/qrels.dev.small.tsv \
    --mode hybrid --limit 500 --seed 42 --top-k 100 --rrf-k 60 \
    --retrieval-only \
    --out /tmp/live_hybrid_500.jsonl
```

**Quality report** — `experiments/eval/make_quality_report.py` scores the run
JSONL with the proven scorer (`eval/compute_metrics.py`) and merges provenance
+ retrieval-latency stats into the single generator for
`live_retrieval_quality.json` (consumed by `figures.py::fig7`).

```bash
python3 experiments/eval/make_quality_report.py \
    --run /tmp/live_hybrid_500.jsonl \
    --qrels /home/apath/Work/PDC/data/msmarco/qrels.dev.small.tsv \
    --gateway http://10.8.0.2:8000 \
    --queries-file /home/apath/Work/PDC/data/msmarco/queries.dev.tsv \
    --seed 42 --limit 500 --top-k 100 --rrf-k 60 \
    --out experiments/analysis/results/live_retrieval_quality.json
```

**Answer-level SPHP cost** — `experiments/eval/answer_compare.py` runs each
sampled query twice (baseline `sphp=false` vs `sphp=true`) and compares the two
answers (EM, token-F1, length ratio) over hit/miss subsets. Requires the
gateway to expose `answer_full` on `/query/benchmark`.

```bash
python3 experiments/eval/answer_compare.py \
    --gateway http://10.8.0.2:8000 \
    --queries /home/apath/Work/PDC/data/msmarco/queries.dev.tsv \
    --qrels   /home/apath/Work/PDC/data/msmarco/qrels.dev.small.tsv \
    --limit 200 --seed 42 --top-k 10 \
    --out /tmp/answers_sphp.jsonl \
    --report experiments/analysis/results/answer_comparison.json
```

---

## 6. Live policy demo

`scripts/policy_demo.py` (paper RQ3) demonstrates the live placement policy
end-to-end. It loads **both** cache-state models from
`experiments/analysis/results/cost_model_validation.json` (must exist — run
§4 first), sweeps the fixed regimes `RTT ∈ {0,15,40,80}` ms (loss 0%), selects
the argmin variant per regime from the **warm** fit, and compares realized
TTFT against the state-matched prediction.

```bash
python3 scripts/policy_demo.py \
    --gateway http://10.8.0.2:8000 \
    --queries experiments/bench/queries/latency_dev480_seed42.txt \
    --per-regime 5 --warmups 2 --repeats 3
```

**Flags** (defaults): `--gateway` (`http://10.8.0.2:8000`), `--queries`
(default `benchmarks/queries/latency_dev480_seed42.txt` — a stale path; pass
`experiments/bench/queries/latency_dev480_seed42.txt`), `--per-regime` N (`5`),
`--warmups` W (`2`), `--repeats` R (`3`), `--top-k` (`10`), `--timeout` (`120`),
`--seed` (`42`).

**Repeat semantics** mirror the campaign: each recorded query is served R times
back-to-back; **repeat 0 = cold KV-prefix reference**, repeats ≥1 = warm. The
summary compares warm realized vs the warm prediction and cold realized vs the
cold prediction of the same selected variant.

**Outputs:** `experiments/analysis/results/live_policy_demo.jsonl` (one record
per request) and `live_policy_demo_summary.json` (per-regime, per-state).

**GPU window discipline.** The demo shapes the WAN path and drives the live
engine. **No coworker dispatches (no other campaigns, evals, or demos) may run
during a measured window** — concurrent load corrupts the TTFT measurements.
The script always clears netem in a `finally` block, even on failure.

---

## 7. Node A/B deploy loop

One-liner (Node A → Node B):

```bash
git commit -am "<msg>" && git push && ssh node-b "cd /d D:\FAST\Semester6\NLP\Project_Laptop && git pull"
```

Then, **only if `systems/node_b/` code changed**, restart the gateway on B
(PowerShell): `schtasks /End /TN pdc_gateway` then `schtasks /Run /TN
pdc_gateway`. Qdrant needs no restart for code-only changes.

Full access details, the one-time `docs/B_MIGRATION.md` cutover, and the
PowerShell 5.1 constraints are in `docs/NODE_ACCESS.md`.

---

## Ground-truth sources

- `experiments/bench/campaign.py` — campaign CLI, defaults, outputs, netem + warmup/repeat semantics
- `experiments/analysis/cost_model.py` — cost-model CLI, `cost_model_validation.json` + `crossover_analysis{,_cold}.json`
- `experiments/analysis/figures.py` — figure generation → `paper/figures/*.pdf`
- `experiments/bench/run_eval.py` — retrieval-only eval driver
- `experiments/eval/make_quality_report.py` — `live_retrieval_quality.json` generator
- `experiments/eval/answer_compare.py` — SPHP answer-level cost
- `scripts/policy_demo.py` — live RQ3 policy demo CLI
- `scripts/netem/{apply,clear,validate}.sh` — root requirement, usage
- `scripts/node_a/{start,stop}_stack.sh` — orchestrator lifecycle
- `docs/NODE_ACCESS.md` — node access, deploy loop, B_MIGRATION pointer
