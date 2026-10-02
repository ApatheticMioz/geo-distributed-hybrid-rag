# Node Access Sheet (F-1)

How to reach, operate, and develop across each node in the 3-node geo-distributed hybrid-RAG
topology. Every alias, IP, and path below is verified against active configurations.

---

## 0. Topology & SSH Control Matrix

Development can be conducted on **any node** (Node A, Node B, or Node C). Coordination and
branch synchronization occur through the remote Git repository (`origin`).

| From \ To | Node A (`pc`) | Node B (`laptop`) | Node C (`friend-laptop`) | Remote (`origin`) |
|:---|:---|:---|:---|:---|
| **Node A (PC)** | Local (WSL2) | `ssh laptop` / `ssh laptop-wg` | `ssh friend-laptop` / `ssh friend-wg` | Full push / pull |
| **Node B (Laptop)** | `ssh pc` / `ssh pc-wg` | Local (PowerShell 7) | `ssh friend-laptop` / `ssh friend-wg` | Full push / pull |
| **Node C (Friend's Laptop)** | *No SSH access* | *No SSH access* | Local (PowerShell 7 / Git Bash) | Full push / pull |

> **Key Access Rules:**
> - **Nodes A & B** have mutual SSH control and can both SSH into Node C.
> - **Node C** does **not** have inbound SSH access into Node A or Node B.
> - The human developer on Node C works locally in their clone root, commits and pushes to `origin`, pulls updates from `origin`, and manages local Node C services.

### Node Endpoints at a Glance

| Node | Role | WireGuard IP | Tailscale IP / Alias | Clone Root | Primary Service | Port |
|:---|:---|:---|:---|:---|:---|:---|
| **A** | Generation host (vLLM Qwen3.8-27B) | `10.8.0.1` | `100.92.1.107` (`pc`) | `/home/apath/Work/PDC/Project` (WSL) | gRPC `GenerationOrchestrator` | `50052` |
| **B** | Dense + fusion gateway (BGE-M3/Qdrant + RRF) | `10.8.0.2` | `100.99.79.108` (`laptop`) | `D:\Work\Semester6\NLP\Project_Laptop` | HTTP FastAPI gateway | `8000` |
| **C** | Sparse retrieval (Tantivy) + user-facing tier | `10.8.0.3` | `100.101.11.23` (`friend-laptop`) | `C:\Users\Yurnero\Desktop\Uni Work\Semester 6\NLP\Project\Phase 3` | FastAPI gateway + local Tantivy | `8000` |

---

## 1. Node A (`10.8.0.1` / PC)

The generation host (RTX 3090 + vLLM). Runs in WSL2 Ubuntu on PC.

| Item | Value |
|:---|:---|
| Clone root | `/home/apath/Work/PDC/Project` (WSL2) |
| WireGuard IP | `10.8.0.1` (iface `eth0`, mirrored networking) |
| Tailscale IP | `100.92.1.107` (SSH alias: `pc`) |
| vLLM engine | `qwen3.8-27b` at `localhost:18020` (managed out-of-band; do not invoke GPU for non-campaign tasks) |
| Orchestrator gRPC | `:50052` (`GenerationOrchestrator`) |
| Orchestrator HTTP | `:8001` (FastAPI) |
| Orchestrator PID / Log | `/tmp/pdc_node_a_orchestrator.pid` / `/tmp/pdc_node_a_orchestrator.log` |

**Lifecycle** (orchestrator only):
* Start: `bash scripts/node_a/start_stack.sh`
* Stop: `bash scripts/node_a/stop_stack.sh`

---

## 2. Node B (`10.8.0.2` / Laptop)

The dense-retrieval + RRF-fusion gateway (GTX 1660 Ti). Runs native Windows + PowerShell 7.

| Item | Value |
|:---|:---|
| Clone root | `D:\Work\Semester6\NLP\Project_Laptop` |
| WireGuard IP | `10.8.0.2` (SSH alias: `laptop-wg`, legacy `node-b`) |
| Tailscale IP | `100.99.79.108` (SSH alias: `laptop`) |
| LAN IP | `192.168.0.142` (SSH alias: `laptop-lan`) |
| Remote shell | **PowerShell 7.6.6 (`pwsh.exe`)** |
| Gateway | HTTP FastAPI `src.server:app` on `0.0.0.0:8000` |
| Qdrant container | `qdrant_node_b` (HTTP `:6333`, gRPC `:6334`), external volume `pdc_qdrant_storage` |
| Gateway Python venv | `systems\node_b\implementation\.venv` (torch `2.7.1+cu118`, transformers `>=4.40.0`) |

**Gateway Lifecycle** (run from clone root in PowerShell):
* Start Gateway: `schtasks /Run /TN pdc_gateway`
* Stop Gateway: `schtasks /End /TN pdc_gateway`
* Start Qdrant: `docker compose -f systems\node_b\docker-compose.yml up -d`
* Stop Qdrant: `docker stop qdrant_node_b`
* Health Check: `curl.exe -s http://127.0.0.1:8000/health` → JSON with `"role":"hybrid_retrieval_gateway"`

*(Note: Gateway scheduled task executes `start_gateway.bat` at clone root).*

---

## 3. Node C (`10.8.0.3` / Friend's Laptop)

The sparse-retrieval (Tantivy BM25) + user-facing gateway tier. Clients send requests to Node C.

| Item | Value |
|:---|:---|
| Clone root | `C:\Users\Yurnero\Desktop\Uni Work\Semester 6\NLP\Project\Phase 3` |
| WireGuard IP | `10.8.0.3` (SSH alias from A/B: `friend-wg`, legacy `node-c`) |
| Tailscale IP | `100.101.11.23` (SSH alias from A/B: `friend-laptop`, `friend`, `yurnero`) |
| Remote shell | **PowerShell 7.6.6 (`pwsh.exe`)** |
| Outbound SSH | **None** (cannot SSH into Node A or Node B) |
| Gateway | FastAPI on `0.0.0.0:8000` (per `systems/node_c/config.yaml`) |
| Sparse index | Tantivy BM25 local on C (`systems/node_c/data/tantivy_index`) |
| Dense leg | Forwarded to Node B `10.8.0.2:8000` |
| Generation | Transitive via Node B → Node A `10.8.0.1:50052` |
| Python venv | `systems\node_c\.venv` (Python 3.11) |

**Gateway Lifecycle**:
* Launch: `start_gateway_c.bat` (or `schtasks /Run /TN pdc_gateway_c`)
* Verification: `curl.exe -s http://127.0.0.1:8000/health`
* **Remote Ops Pattern (from A or B)**: Always use single-line commands when issuing commands over SSH into Node C.

---

## 4. Cross-Node Deploy & Development Flow

Because development can happen on any machine:

1. **Commit and Push**:
   * Commit changes on the active development node (A, B, or C).
   * Push to remote: `git push origin <branch>` (active branch: `feat/three-node-study`).

2. **Sync Other Nodes**:
   * **From Node A or B**:
     ```bash
     # To sync Node B (from A):
     ssh laptop "git -C 'D:\Work\Semester6\NLP\Project_Laptop' pull origin <branch>"

     # To sync Node A (from B):
     ssh pc "wsl.exe -d Ubuntu -e bash -c 'cd /home/apath/Work/PDC/Project && git pull origin <branch>'"

     # To sync Node C (from A or B):
     ssh friend-laptop "git -C 'C:\Users\Yurnero\Desktop\Uni Work\Semester 6\NLP\Project\Phase 3' pull origin <branch>"
     ```
   * **From Node C**:
     The human developer on Node C runs `git pull origin <branch>` locally.

3. **Restart Services (Only if relevant node code changed)**:
   * **Node B changed**: `schtasks /End /TN pdc_gateway` then `schtasks /Run /TN pdc_gateway`.
   * **Node C changed**: Restart Node C gateway (`start_gateway_c.bat` or `pdc_gateway_c` task).
   * **Node A changed**: `bash scripts/node_a/stop_stack.sh && bash scripts/node_a/start_stack.sh`.

---

## 5. Netem WAN Emulation (Node A — Root Required)

`scripts/netem/*` shapes the `eth0` ↔ `10.8.0.2` (and `10.8.0.3`) path bidirectionally
(half-per-direction: egress on `eth0` root qdisc, ingress via `ifb0`). Run as root:

| Script | Purpose |
|:---|:---|
| `apply.sh RTT_MS LOSS_PCT [RATE]` | Apply bidirectional shaping |
| `clear.sh` | Remove all netem state (idempotent) |
| `show.sh` | Inspect current qdisc state (read-only) |
| `validate.sh` | Closed-loop RTT check (clear → baseline → apply → shaped) |

Run from Windows on PC as:
```cmd
wsl.exe -u root -e bash /home/apath/Work/PDC/Project/scripts/netem/apply.sh 15 5
```

---

## Ground-Truth Sources

- `~/.ssh/config` (on Node A & Node B) — `laptop` (100.99.79.108), `pc` (100.92.1.107), `friend-laptop` (100.101.11.23)
- `scripts/node_a/start_stack.sh`, `stop_stack.sh` — orchestrator lifecycle, ports 50052/8001
- `start_gateway.bat`, `start_gateway_c.bat` — Node B & Node C gateway launch contracts
- `systems/node_b/docker-compose.yml` — Qdrant `qdrant_node_b`, named volume `pdc_qdrant_storage`
- `systems/node_c/config.yaml` — Node C role, `:8000`, B/A endpoints, `dense_timeout_ms`
- `scripts/node_c_prep/NODE_C_CHECKLIST.md` — Node C bring-up and verification checklist
- `scripts/netem/apply.sh` — root netem shaping contract on `eth0` / `ifb0`
