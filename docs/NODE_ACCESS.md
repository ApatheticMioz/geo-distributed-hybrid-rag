# Node Access Sheet (F-1)

How to reach, operate, and develop across each node in the 3-node geo-distributed hybrid-RAG
topology. Every alias, IP, and path below is verified against active configurations.

---

## 0. Topology & SSH Control Matrix

Development can be conducted on **any node** (Node A, Node B, or Node C). Coordination and
branch synchronization occur through the remote Git repository (`origin`).

| From \ To | Node A (`pc`) | Node B (`laptop`) | Node C (`node-c`) | Remote (`origin`) |
|:---|:---|:---|:---|:---|
| **Node A (PC)** | Local (WSL2) | `ssh laptop` / `ssh laptop-wg` | `ssh node-c` / `ssh friend-wg` | Full push / pull |
| **Node B (Laptop)** | `ssh pc` / `ssh pc-wg` ⚠️ (see §1) | Local (PowerShell 7) | `ssh node-c` / `ssh friend-wg` | Full push / pull |
| **Node C (Friend's Laptop)** | *No SSH access* | *No SSH access* | Local (PowerShell 7 / Git Bash) | Full push / pull |

> **Key Access Rules:**
> - **Nodes A & B** have mutual SSH control and can both SSH into Node C.
> - **Node C** does **not** have inbound SSH access into Node A or Node B.
> - The human developer on Node C works locally in their clone root, commits and pushes to `origin`, pulls updates from `origin`, and manages local Node C services.

### Node Endpoints at a Glance

| Node | Role | WireGuard IP | Tailscale IP / Alias | Clone Root | Primary Service | Port |
|:---|:---|:---|:---|:---|:---|:---|
| **A** | Generation host (vLLM Qwen3.8-27B) | `10.8.0.1` | `100.64.0.1` (`pc`) | `/home/apath/Work/PDC/Project` (WSL) | gRPC `GenerationOrchestrator` | `50052` |
| **B** | Dense + fusion gateway (BGE-M3/Qdrant + RRF) | `10.8.0.2` | `100.64.0.2` (`laptop`) | `D:\geo-distributed-hybrid-rag\node_b` | HTTP FastAPI gateway | `8000` |
| **C** | Sparse retrieval (Tantivy) + user-facing tier | `10.8.0.3` | `100.64.0.3` (`node-c`) | `C:\geo-distributed-hybrid-rag\node_c` | FastAPI gateway + local Tantivy | `8000` |

---

## 1. Node A (`10.8.0.1` / PC)

The generation host (RTX 3090 + vLLM). Runs in WSL2 Ubuntu on PC.

| Item | Value |
|:---|:---|
| Clone root | `/home/apath/Work/PDC/Project` (WSL2) |
| WireGuard IP | `10.8.0.1` (iface `eth0`, mirrored networking) |
| Tailscale IP | `100.64.0.1` (SSH alias: `pc`) |
| vLLM engine | `qwen3.8-27b` at `localhost:18020` (managed out-of-band; do not invoke GPU for non-campaign tasks) |
| Orchestrator gRPC | `:50052` (`GenerationOrchestrator`) |
| Orchestrator HTTP | `:8001` (FastAPI) |
| Orchestrator PID / Log | `/tmp/pdc_node_a_orchestrator.pid` / `/tmp/pdc_node_a_orchestrator.log` |
| WireGuard tunnel | `wg_a` @ `10.8.0.1`, public endpoint `203.0.113.1:51820` (DDNS); A is the **hub** of the hub-and-spoke topology (spokes B, C) — see `docs/RUNBOOK.md` "WireGuard hub-relay bring-up" |

**Lifecycle** (orchestrator only):
* Start: `bash scripts/node_a/start_stack.sh`
* Stop: `bash scripts/node_a/stop_stack.sh`

**Lifecycle** (orchestrator only):
* Start: `bash scripts/node_a/start_stack.sh`
* Stop: `bash scripts/node_a/stop_stack.sh`

**SSH / firewall ground truth (Updated 2026-10-20):**
* Node A's Windows OpenSSH server is **live and elevated**; inbound TCP 22 firewall rule is active.
* While Node A's WSL `~/.ssh/config` lacks a direct `Host pc` entry, loopback admin access to Node A Windows from WSL is fully operational via Node B:
  `ssh laptop "ssh pc <command>"` (provides elevated Windows command execution without interactive UAC prompts).
* WireGuard `wg_a` tunnel configuration with both peers (`10.8.0.2` Node B, `10.8.0.3` Node C) is persistently backed up at `C:\ProgramData\WireGuard\wg_a.conf`. Following any interface restart or Windows host reboot, forwarding must be asserted:
  `ssh laptop "ssh pc 'powershell -Command \"Set-NetIPInterface -InterfaceAlias wg_a -Forwarding Enabled\"'"`

---

## 2. Node B (`10.8.0.2` / Laptop)

The dense-retrieval + RRF-fusion gateway (GTX 1660 Ti). Runs native Windows + PowerShell 7.

| Item | Value |
|:---|:---|
| Clone root | `D:\geo-distributed-hybrid-rag\node_b` |
| WireGuard IP | `10.8.0.2` (SSH alias: `laptop-wg`, legacy `node-b`) |
| WireGuard tunnel | name **`wg`** (single peer A, endpoint `192.168.0.10:51820` — A's LAN IP; A and B share the `192.168.0.0/24` LAN), **`AllowedIps 10.8.0.0/24`** (required for C→B relayed traffic; the default `10.8.0.1/32` silently drops it). Config: `Data\Configurations\wg.conf.dpapi` (DPAPI-encrypted); last plaintext base `wg.conf.bak_ncwg2` (2026-09-29). Reinstall names the tunnel after the **file basename** |
| Tailscale IP | `100.64.0.2` (SSH alias: `laptop`) |
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
| Clone root | `C:\geo-distributed-hybrid-rag\node_c` |
| WireGuard IP | `10.8.0.3` (SSH alias from A/B: `friend-wg`, legacy `node-c`) |
| Tailscale IP | `100.64.0.3` (SSH alias from A/B: `node-c`, `friend`, `node_c_operator`) |
| Remote shell | **PowerShell 7.6.6 (`pwsh.exe`)** |
| Outbound SSH | **None** (cannot SSH into Node A or Node B) |
| Gateway | FastAPI on `0.0.0.0:8000` (per `systems/node_c/config.yaml`) |
| Sparse index | Tantivy BM25 local on C (`systems/node_c/data/tantivy_index`) |
| Dense leg | Forwarded to Node B `10.8.0.2:8000` |
| Generation | Transitive via Node B → Node A `10.8.0.1:50052` |
| Python venv | `systems\node_c\.venv` (Python 3.11) |

**Gateway Lifecycle**:
* Launch: `start_gateway_c.bat` (or `schtasks /Run /TN pdc_gateway_c`)
* **Precondition:** the `pdc_gateway_c` scheduled task must be **ENABLED**
  first — `schtasks /Change /TN pdc_gateway_c /ENABLE` — or `/Run` fails
  silently. If the task was found Disabled, enable it and then `schtasks
  /Run`; the gateway comes up in ~10 s (verified 2026-10-12).
* Verification: `curl.exe -s http://127.0.0.1:8000/health` (local check on Node C)
* **Remote Ops Pattern (from A or B)**: Always use single-line commands when issuing commands over SSH into Node C.
* **Direct WireGuard SSH**: Use `ssh friend-wg` (`10.8.0.3`) for low-latency direct connectivity (especially if Tailscale `node-c` is offline).
* **Critical Relay Verification (Mandatory before campaigns)**:
  ```bash
  ssh friend-wg "curl.exe -s --max-time 6 http://10.8.0.2:8000/health"
  ```
  **Assertion:** MUST return `{"status":"ok","node":"B",...}`. If it returns `"node":"C"`, C's requests to B are looping back locally.
* **Interface Hygiene**: Verify via `ssh friend-wg "wg show"` that only `wg0` is active. Never allow secondary tunnels (e.g., stale test tunnels like `T`) to capture `10.8.0.0/24`.

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
     ssh laptop "git -C 'D:\geo-distributed-hybrid-rag\node_b' pull origin <branch>"

     # To sync Node A (from B):
     ssh pc "wsl.exe -d Ubuntu -e bash -c 'cd /home/apath/Work/PDC/Project && git pull origin <branch>'"

     # To sync Node C (from A or B):
     ssh node-c "git -C 'C:\geo-distributed-hybrid-rag\node_c' pull origin <branch>"
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
| `apply.sh RTT_MS LOSS_PCT [RATE]` | Apply bidirectional shaping (LOSS_PCT: 0..100, at most one decimal place; halved per direction) |
| `clear.sh` | Remove all netem state (idempotent) |
| `show.sh` | Inspect current qdisc state (read-only) |
| `validate.sh` | Closed-loop RTT check (clear → baseline → apply → shaped) |

Run from Windows on PC as:
```cmd
wsl.exe -u root -e bash /home/apath/Work/PDC/Project/scripts/netem/apply.sh 15 5
```

---

## Ground-Truth Sources

- `~/.ssh/config` (on Node A & Node B) — `laptop` (100.64.0.2), `node-c` (100.64.0.3). **Not** `pc`: Node A's WSL `~/.ssh/config` has no `Host pc` block (2026-10-12); the Tailscale IP `100.64.0.1` for A is reached, if at all, by other means.
- `scripts/node_a/start_stack.sh`, `stop_stack.sh` — orchestrator lifecycle, ports 50052/8001
- `start_gateway.bat`, `start_gateway_c.bat` — Node B & Node C gateway launch contracts
- `systems/node_b/docker-compose.yml` — Qdrant `qdrant_node_b`, named volume `pdc_qdrant_storage`
- `systems/node_c/config.yaml` — Node C role, `:8000`, B/A endpoints, `dense_timeout_ms`
- `scripts/node_c_prep/NODE_C_CHECKLIST.md` — Node C bring-up and verification checklist
- `scripts/netem/apply.sh` — root netem shaping contract on `eth0` / `ifb0`
