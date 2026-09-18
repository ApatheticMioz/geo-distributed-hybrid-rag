# Node C Onboarding Checklist — Sparse Retrieval + User-Facing Tier

> **Purpose.** This package prepares the remote laptop **Node C** (WireGuard
> address `10.8.0.3`) for its role in the 3-node geo-distributed hybrid-RAG
> campaign. Node C is **not** a bare client: it (a) hosts the **Tantivy BM25
> sparse index**, so the sparse leg — and the SPHP sparse hint — originates
> one hop closer to the user, and (b) acts as the **user-facing tier**: client
> queries enter at C, which runs sparse locally and forwards the dense leg to
> the Node B gateway (`10.8.0.2:8000`). Generation remains on Node A behind
> Node B's existing P2 path (`10.8.0.1:50052`, reached transitively).
>
> This supersedes the earlier client-only design (retired 2026-09-18).

**Node roles & addresses**

| Node | Role | WireGuard IP | Service | Port |
|------|------|--------------|---------|------|
| A | Generation host (RTX 3090, vLLM Qwen3.8-27B W4A16) | `10.8.0.1` | gRPC `GenerationOrchestrator` | `50052` |
| B | Dense + fusion gateway (GTX 1660 Ti, BGE-M3/Qdrant + RRF) | `10.8.0.2` | HTTP FastAPI gateway | `8000` |
| C | **Sparse retrieval (Tantivy) + user-facing tier (this laptop)** | `10.8.0.3` | FastAPI gateway + local Tantivy | `8000` |

Work through the steps in order. Each step has a **verify** line; do not
proceed until it passes.

---

## 1. WireGuard configuration

Node C must be a peer on the same WireGuard mesh as Nodes A and B.

1. **Install WireGuard** (if not present):
   ```bash
   # Debian/Ubuntu
   sudo apt-get update && sudo apt-get install -y wireguard
   # Fedora
   sudo dnf install -y wireguard-tools
   ```
2. **Create the Node C peer config** `/etc/wireguard/wg0.conf`. The `Address`
   **must** be `10.8.0.3/24` to match the campaign topology:
   ```ini
   [Interface]
   Address = 10.8.0.3/24
   DNS     = 1.1.1.1
   PrivateKey = <NODE_C_PRIVATE_KEY>

   [Peer]  # Node A
   PublicKey  = <NODE_A_PUBLIC_KEY>
   AllowedIPs = 10.8.0.1/32

   [Peer]  # Node B
   PublicKey  = <NODE_B_PUBLIC_KEY>
   AllowedIPs = 10.8.0.2/32
   ```
3. **Bring the interface up:**
   ```bash
   sudo wg-quick up wg0
   ```
4. **Verify:**
   ```bash
   ip addr show wg0            # must show 10.8.0.3/24
   wg show                     # must list the interface with peers
   ping -c 3 10.8.0.2          # record RTT (B)
   ping -c 3 10.8.0.1          # record RTT (A)
   ```

---

## 2. SSH access and repo sync

Node C is operated over SSH and gets code via git (same deploy pattern as
Node B: push from Node A, pull on the node).

1. **From Node A (or a trusted machine), open a session:**
   ```bash
   ssh <user>@<node-c-lan-ip>
   ```
2. **Clone or pull the project repo** (Node C runs from the same repository;
   its runtime config is `Node C/config.yaml` until the repo restructure
   relocates it):
   ```bash
   git -C <repo-path> pull --ff-only
   ```
3. **Verify:** `git -C <repo-path> log --oneline -1` matches the commit Node A
   pushed most recently.

---

## 3. Python environment

Unlike the old client-only package, the sparse tier needs real dependencies
(Tantivy + FastAPI), not just stdlib.

1. **Create an isolated environment:**
   ```bash
   python3 --version            # 3.10+ recommended
   python3 -m venv .venv-nodec
   . .venv-nodec/bin/activate
   pip install tantivy fastapi uvicorn requests pyyaml
   ```
2. **Verify:**
   ```bash
   python3 -c "import tantivy, fastapi, uvicorn, requests; print('OK')"
   ```

> If Node C is a Windows/WSL host, run everything inside the WSL
> distribution so that `bash`, `ping`, and `tar` behave as documented.

---

## 4. Transfer the Tantivy index (~3.2 GB)

The full sparse index lives on Node B at
`node_B/implementation/data/tantivy_index_full`. Transfer it with the same
tar-over-ssh pattern used by `scripts/transfer_tantivy.sh`, targeted at C:

```bash
# From Node B (or from Node A ssh'd to B), adjust <user>@<node-c-ip>:
REMOTE_DIR=<repo-path>/data/tantivy_index
ssh <user>@<node-c-ip> "mkdir -p '$REMOTE_DIR'"
tar -cf - -C node_B/implementation/data/tantivy_index_full . \
    | ssh <user>@<node-c-ip> "tar -xf - -C '$REMOTE_DIR'"
```

1. **Verify the index loads** (build/load check, do not rebuild the corpus):
   ```bash
   python3 - <<'PY'
   import tantivy
   idx = tantivy.Index.open("<repo-path>/data/tantivy_index")
   print("index OK:", idx.num_docs(), "documents")
   PY
   ```
   Expect ≈ 8,841,823 documents.
2. **Spot-check one BM25 query** returns ranked hits in well under a second
   (cold first query may be slower while the page cache warms).

---

## 5. Preflight connectivity probe

Run the automated probe **before** the campaign
(`bash scripts/node_c_prep/preflight.sh`; `--dry-run` for a no-network logic
test). Required checks for the new role:

| Check | Target | Pass criterion |
|-------|--------|----------------|
| Ping | `10.8.0.1` (A), `10.8.0.2` (B) | ≥ 1 reply, RTT recorded |
| MTU | `10.8.0.1`, `10.8.0.2` | largest non-fragmented payload (≤ 1500) |
| Port | `10.8.0.2:8000` (B gateway, dense+fusion leg) | TCP connect + HTTP 200 |
| Port | `10.8.0.1:50052` (A gRPC, transitive via B) | TCP connect from B |

**Verify:** the probe prints a `PREFLIGHT: PASS` summary and exits `0`.

---

## 6. End-to-end smoke through Node C

Bring up C's user-facing gateway and prove the full 3-node path once:

1. **Start C's gateway** (sparse at C; dense+fusion forwarded to B):
   ```bash
   . .venv-nodec/bin/activate
   python3 -m uvicorn gateway:app --host 0.0.0.0 --port 8000   # per config.yaml
   ```
2. **Query it** from another shell:
   ```bash
   curl -s http://10.8.0.3:8000/query -H 'Content-Type: application/json' \
        -d '{"query":"who wrote the origin of species","top_k":10}'
   ```
3. **Verify:** the response carries per-leg timings with the sparse leg
   served locally at C (sub-100 ms warm) and the dense leg crossing to B;
   the generated tokens stream back through C to the client.

> **SPHP note for the 3-node topology:** because the sparse tier lives on C
> (the user-facing node), the provisional sparse hint no longer waits for a
> cross-link sparse leg — it is available immediately. Measuring how this
> reshapes the SPHP cold/warm deltas is a primary 3-node deliverable.

---

## 7. Pre-campaign sign-off

Before the 3-node campaign starts, confirm all of the following:

- [ ] `wg0` is up and shows `10.8.0.3/24`; RTTs to A and B recorded
- [ ] Node C repo is at the same commit Node A pushed
- [ ] Tantivy index loads on C with ≈ 8.84M documents; BM25 spot-query OK
- [ ] `preflight.sh` prints `PREFLIGHT: PASS` (exit 0)
- [ ] End-to-end smoke through `10.8.0.3:8000` returns per-leg timings with
      sparse served at C
- [ ] Netem plan for the two legs (C–B, B–A) agreed; shaping validated
      bidirectionally per leg before any measured window

If all boxes are ticked, Node C is ready for the 3-node campaign.
