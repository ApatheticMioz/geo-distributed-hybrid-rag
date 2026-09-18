# Node Access Sheet (F-1)

How to reach and operate each node in the 3-node geo-distributed hybrid-RAG
topology. Every alias, IP, and port below is sourced from the ground-truth
files listed at the bottom.

## 0. Topology at a glance

| Node | Role | WireGuard IP | SSH alias (from WSL) | Service | Port |
|------|------|--------------|----------------------|---------|------|
| A | Generation host (vLLM Qwen3.8-27B) | `10.8.0.1` | — (this WSL host) | gRPC `GenerationOrchestrator` | `50052` |
| B | Dense + fusion gateway (BGE-M3/Qdrant + RRF) | `10.8.0.2` | `node-b` / `node-b-lan` | HTTP FastAPI gateway | `8000` |
| C | Sparse retrieval (Tantivy) + user-facing tier | `10.8.0.3` | `node-c` | FastAPI gateway + local Tantivy | `8000` |

---

## 1. Node A — this WSL host (`10.8.0.1`)

The WSL2 VM where this repo lives and where you do all editing. It is the
generation host.

| Item | Value |
|------|-------|
| WireGuard IP | `10.8.0.1` (iface `eth4`, the WireGuard-path interface) |
| vLLM engine | `qwen3.8-27b` at `localhost:18020` — managed **separately** (`D:\LLM_Ecosystem\scripts\main`), must already be running; **not** touched by the stack scripts |
| Orchestrator gRPC | `:50052` (`GenerationOrchestrator`) |
| Orchestrator HTTP | `:8001` (FastAPI) |
| Orchestrator PID file | `/tmp/pdc_node_a_orchestrator.pid` |
| Orchestrator log | `/tmp/pdc_node_a_orchestrator.log` |

**Lifecycle** (orchestrator only — the vLLM engine is managed out-of-band):

| Action | Command |
|--------|---------|
| Start | `bash scripts/node_a/start_stack.sh` |
| Stop | `bash scripts/node_a/stop_stack.sh` |

`start_stack.sh` is fail-fast (no retries): it refuses to start if `:50052` is
already bound by something else, records the PID, and waits up to 30 s for the
gRPC listener. `stop_stack.sh` SIGTERMs then SIGKILLs the recorded PID and
removes the pid file.

---

## 2. Node B (`10.8.0.2`)

The dense-retrieval + RRF-fusion gateway. Reached over SSH from this WSL host.

| Item | Value |
|------|-------|
| WireGuard IP | `10.8.0.2` |
| SSH alias (WireGuard) | `node-b` → `10.8.0.2` |
| SSH alias (LAN) | `node-b-lan` → `192.168.0.142` |
| SSH user | `apath` (key `~/.ssh/id_ed25519`) |
| Remote shell | **Windows PowerShell 5.1** — no `&&`, no `head`/`grep`; use `;` and PowerShell cmdlets |
| Gateway | HTTP FastAPI `src.server:app` on `0.0.0.0:8000` |
| Qdrant (docker) | container `qdrant_node_b`; ports `6333` (HTTP) / `6334` (gRPC) |
| Clone root | `D:\FAST\Semester6\NLP\Project_Laptop` |

**Gateway lifecycle** — driven by a Windows scheduled task, not a shell:

| Action | Command (PowerShell, from clone root) |
|--------|----------------------------------------|
| Start | `schtasks /Run /TN pdc_gateway` |
| Stop | `schtasks /End /TN pdc_gateway` |
| Qdrant up | `docker compose -f systems\node_b\docker-compose.yml up -d` |
| Qdrant stop | `docker stop qdrant_node_b` |

The `pdc_gateway` task runs `start_gateway.bat` (repo root), which `cd`s to
`D:\FAST\Semester6\NLP\Project_Laptop\systems\node_b\implementation` and starts
uvicorn on `:8000`. The task runs the `.bat` by its fixed path, so a repo
restructure does not require a `schtasks` change. (Verified 2026-09-19: the
task action on B was found still pointing at the pre-restructure
`node_B\scripts\gateway_task.bat` and was repointed to
`D:\FAST\Semester6\NLP\Project_Laptop\start_gateway.bat` — `schtasks /Run`
had been failing silently with `Last Result=1`. If `/Run` appears to do
nothing, check the task's *Task To Run* with `schtasks /Query /TN
pdc_gateway /V`.)

**Health check:** `curl.exe -s http://127.0.0.1:8000/health` → JSON with
`"role":"hybrid_retrieval_gateway"`.

---

## 3. Node C (`10.8.0.3`)

Sparse-retrieval (Tantivy BM25) + user-facing tier. Clients talk to C, not B.
**Bring-up is pending** — do not assume it is up.

| Item | Value |
|------|-------|
| WireGuard IP | `10.8.0.3` |
| SSH alias | `node-c` → `10.8.0.3` (user `apath`) |
| Gateway | FastAPI on `0.0.0.0:8000` (per `systems/node_c/config.yaml`) |
| Sparse index | Tantivy BM25, local at C (`data/tantivy_index`) |
| Dense leg | forwarded to Node B `10.8.0.2:8000` |
| Generation | transitive: B → A `10.8.0.1:50052` |

**Bring-up:** follow `scripts/node_c_prep/NODE_C_CHECKLIST.md` (WireGuard peer
`10.8.0.3/24`, repo sync, venv, Tantivy index transfer, preflight, e2e smoke).
The legacy gRPC `DenseDispatcher` (`10.8.0.5:50051`) is retired — Node B's
retrieval surface is its FastAPI gateway.

---

## 4. Deploy loop

Normal change flow (Node B is the only remote node currently in service):

1. **Edit here** (Node A / this WSL host).
2. **Commit + push** from this host.
3. **`ssh node-b`** → `git pull` in the clone root
   (`D:\FAST\Semester6\NLP\Project_Laptop`).
4. **Restart the gateway only if `systems/node_b/` code changed:**
   `schtasks /End /TN pdc_gateway` then `schtasks /Run /TN pdc_gateway`.
   (Qdrant needs no restart for code-only changes.)

> **ONE-TIME pending cutover:** the `node_B/` → `systems/node_b/` restructure
> (commit `1d76c8e`) must be applied on Node B **before** the normal loop
> works, because Node B's clone carries untracked state in the old directory.
> Follow `docs/B_MIGRATION.md` in order (stop services → pull → move untracked
> state → restart from the new tree → verify). Do this once; afterwards the
> normal loop above applies.

---

## 5. Netem (WAN emulation) — root required

`scripts/netem/*` shape the `eth4` ↔ `10.8.0.2` path bidirectionally
(half-per-direction: egress on `eth4` root qdisc, ingress via `ifb0`). They
need `CAP_NET_ADMIN` and contain **no sudo** — run as root:

| Script | Purpose |
|--------|---------|
| `apply.sh RTT_MS LOSS_PCT [RATE]` | Apply bidirectional shaping |
| `clear.sh` | Remove all netem state (idempotent) |
| `show.sh` | Inspect current qdisc state (read-only) |
| `validate.sh` | Closed-loop RTT check (clear → baseline → apply → shaped) |

Run from Windows as:

```
wsl.exe -u root -e bash /home/apath/Work/PDC/Project/scripts/netem/apply.sh 15 5
```

---

## Ground-truth sources

- `~/.ssh/config` — `node-b` (10.8.0.2), `node-c` (10.8.0.3), `node-b-lan` (192.168.0.142)
- `scripts/node_a/start_stack.sh`, `scripts/node_a/stop_stack.sh` — orchestrator lifecycle, ports 50052/8001, vLLM 18020
- `start_gateway.bat` — Node B gateway `:8000`, clone root path
- `systems/node_b/docker-compose.yml` — Qdrant `qdrant_node_b`, ports 6333/6334
- `systems/node_c/config.yaml` — Node C role, `10.8.0.3`, gateway `:8000`, B/A endpoints
- `scripts/node_c_prep/NODE_C_CHECKLIST.md` — Node C bring-up, topology table
- `scripts/netem/apply.sh` (+ `clear.sh`, `show.sh`, `validate.sh`) — root requirement, `eth4`/`ifb0`
- `docs/B_MIGRATION.md` — one-time restructure cutover, `pdc_gateway` task, clone root
