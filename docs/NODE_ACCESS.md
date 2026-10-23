# Node Access & Deployment Matrix

This document specifies the operational topology, inter-node networking, and deployment contracts across the 3 physical tiers in the geo-distributed hybrid RAG testbed.

---

## 0. Topology & Control Matrix

The testbed connects three physical tiers across emulated or real wide-area network links. All nodes synchronize codebase state and benchmark manifests through Git.

| From \ To | Node A (Cloud Host) | Node B (Regional Gateway) | Node C (Edge Ingress) | Git Remote |
|:---|:---|:---|:---|:---|
| **Node A (Cloud Host)** | Local | `ssh node-b` / `ssh node-b-wg` | `ssh node-c` / `ssh node-c-wg` | Full push / pull |
| **Node B (Regional Gateway)** | `ssh node-a` / `ssh node-a-wg` | Local | `ssh node-c` / `ssh node-c-wg` | Full push / pull |
| **Node C (Edge Ingress)** | Transit via B | Transit via B | Local | Full push / pull |

### Node Endpoints at a Glance

| Node | Role | WireGuard IP | SSH Alias | Primary Service | Port |
|:---|:---|:---|:---|:---|:---|
| **A** | Cloud Generation Host (vLLM Qwen3.8-27B) | `10.8.0.1` | `node-a` | gRPC `GenerationOrchestrator` | `50052` (gRPC), `8001` (HTTP) |
| **B** | Regional Neural Gateway (BGE-M3 + Qdrant + RRF) | `10.8.0.2` | `node-b` | HTTP FastAPI Gateway | `8000` |
| **C** | Edge Ingress Tier (Tantivy BM25 + SPHP Hint Streamer) | `10.8.0.3` | `node-c` | HTTP FastAPI Gateway | `8000` |

---

## 1. Node A (Cloud Generation Host)

The central generation host features an AMD Ryzen 7 7700 processor and an NVIDIA RTX 3090 GPU (24 GB VRAM) serving the Qwen3.8-27B model under vLLM with AutoRound W4A16 quantization.

| Item | Value |
|:---|:---|
| Role | Generation Host (vLLM + SQLite Passage Store) |
| WireGuard IP | `10.8.0.1` (Hub interface) |
| vLLM Engine | `qwen3.8-27b` served at `http://127.0.0.1:18020/v1` |
| Orchestrator gRPC | `:50052` (`GenerationOrchestrator`) |
| Orchestrator HTTP | `:8001` (FastAPI health & metrics) |
| WireGuard Hub Endpoint | `<NODE_A_PUBLIC_IP>:51820` (Node A acts as hub relay for B and C) |

**Lifecycle**:
* Start Orchestrator: `bash scripts/node_a/start_stack.sh`
* Stop Orchestrator: `bash scripts/node_a/stop_stack.sh`
* Health Check: `curl -s http://127.0.0.1:8001/health`

---

## 2. Node B (Regional Neural Gateway)

The regional neural gateway runs on an Intel Core i7-10750H CPU and an NVIDIA GTX 1660 Ti GPU (6 GB VRAM). It executes compute-intensive BGE-M3 dense semantic encoding, queries a 1024-dimensional HNSW index in Qdrant, and runs reciprocal-rank fusion (RRF).

| Item | Value |
|:---|:---|
| Role | Dense Encoding, Vector Search & RRF Fusion |
| WireGuard IP | `10.8.0.2` (Peer `wg`) |
| Gateway Service | HTTP FastAPI on `0.0.0.0:8000` |
| Qdrant Container | `qdrant_node_b` (HTTP `:6333`, gRPC `:6334`), volume `pdc_qdrant_storage` |
| Python Environment | PyTorch with CUDA support, HuggingFace Transformers |

**Lifecycle**:
* Start Gateway: `scripts\launch\start_gateway_node_b.bat` (or Windows scheduled task `pdc_gateway`)
* Start Qdrant: `docker compose -f systems/node_b/docker-compose.yml up -d`
* Stop Qdrant: `docker stop qdrant_node_b`
* Health Check: `curl -s http://10.8.0.2:8000/health` (expects `"role":"hybrid_retrieval_gateway"`)

---

## 3. Node C (Edge Ingress & Sparse Retrieval Tier)

The edge ingress tier resides close to the client. It runs lightweight Tantivy BM25 lexical search over the 8.84M-passage MS MARCO corpus and immediately dispatches the provisional sparse hint ($\mathcal{H}$) to Node A while concurrently forwarding the query to Node B for dense retrieval.

| Item | Value |
|:---|:---|
| Role | Edge Ingress, Tantivy BM25 Sparse Search, SPHP Hint Dispatch |
| WireGuard IP | `10.8.0.3` |
| Gateway Service | HTTP FastAPI on `0.0.0.0:8000` |
| Local Sparse Index | Tantivy BM25 index on local SSD |
| Dense Forward | Forwarded to Node B at `10.8.0.2:8000` |
| Cloud Hint Forward | Forwarded to Node A at `10.8.0.1:50052` |

**Lifecycle**:
* Start Gateway: `scripts\launch\start_gateway_node_c.bat` (or Windows scheduled task `pdc_gateway_c`)
* Health Check: `curl -s http://10.8.0.3:8000/health` (expects status OK)

---

## 4. Cross-Node Deployment Flow

1. **Commit and Push Changes**:
   ```bash
   git commit -am "Update pipeline logic"
   git push origin <branch>
   ```

2. **Pull on Worker Nodes**:
   ```bash
   # From orchestrator (Node A) to Node B:
   ssh node-b "git pull origin <branch>"

   # From orchestrator (Node A) to Node C:
   ssh node-c "git pull origin <branch>"
   ```

3. **Restart Services as Needed**:
   - Node B: `scripts\restart_gateway.ps1`
   - Node C: Restart `pdc_gateway_c` task
   - Node A: `bash scripts/node_a/stop_stack.sh && bash scripts/node_a/start_stack.sh`

---

## 5. Linux `netem` WAN Emulation (Node A)

Network shaping is applied bidirectionally on Node A (`scripts/netem/*`):

| Command | Action |
|:---|:---|
| `sudo bash scripts/netem/apply.sh <rtt_ms> <loss_pct>` | Applies bidirectional delay and packet loss |
| `sudo bash scripts/netem/clear.sh` | Clears all qdisc delays and resets interfaces |
| `sudo bash scripts/netem/show.sh` | Displays active qdisc configuration |
| `sudo bash scripts/netem/validate.sh <rtt_ms>` | Runs closed-loop ping validation gate |
