# Node C Onboarding Checklist — 3-Node Remote WAN Campaign

> **Purpose.** This package prepares the remote laptop **Node C** (WireGuard
> address `10.8.0.3`) to act as the *client / entry node* for the 3-node
> geo-distributed hybrid-RAG campaign. Node C sends queries to the **Node B**
> gateway (`10.8.0.2:8000`) and, transitively, to the **Node A** generation
> host (`10.8.0.1:50052`). Everything in this folder is self-contained and
> stdlib-only so it runs on a stock laptop with no GPU and no heavy
> dependencies.

**Node roles & addresses**

| Node | Role | WireGuard IP | Service | Port |
|------|------|--------------|---------|------|
| A | Generation host (RTX 3090, vLLM LLaMA-3 AWQ) | `10.8.0.1` | gRPC `GenerationOrchestrator` | `50052` |
| B | Edge gateway (GTX 1660 Ti, BM25 + BGE-M3 + RRF) | `10.8.0.2` | HTTP FastAPI gateway | `8000` |
| C | **Remote client / entry node (this laptop)** | `10.8.0.3` | — (client only) | — |

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
2. **Create the Node C peer config** `/etc/wireguard/wg0.conf`. Replace
   `PUBLIC_KEY_NODE_C` with Node C's public key and `PEER_*` with the
   public keys + endpoints of the existing peers (A and B). The `Address`
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
   ```

> **Note.** If the campaign uses a pre-shared mesh where Node C is already a
> peer, skip straight to the verify line. The rest of this checklist only
> needs `10.8.0.3` to be reachable from A and B.

---

## 2. SSH access

Node C is operated over SSH. Confirm you can reach it and that the working
directory is in place.

1. **From a trusted machine, open a session:**
   ```bash
   ssh <user>@<node-c-lan-ip>
   ```
2. **Confirm the repo / package is present:**
   ```bash
   ls scripts/node_c_prep/
   # expect: NODE_C_CHECKLIST.md  preflight.sh  run_node_c_benchmark.py
   ```
3. **Confirm the Python interpreter:**
   ```bash
   python3 --version   # 3.8+ (stdlib only; no pip installs required)
   ```
4. **Verify:** the three files above list cleanly and `python3` reports a
   version ≥ 3.8.

> If the laptop is a Windows/WSL host, run the package inside the WSL
> distribution so that `bash`, `ping`, and `/dev/tcp` behave as documented.

---

## 3. Python environment setup

The benchmark client is **stdlib-only** — no `pip install` is required. This
step only confirms the interpreter and the output location.

1. **Confirm no third-party imports are needed:**
   ```bash
   python3 -m py_compile scripts/node_c_prep/run_node_c_benchmark.py && echo OK
   ```
2. **Ensure the results directory exists** (the client creates it, but create
   it explicitly to catch permission problems early):
   ```bash
   mkdir -p benchmarks
   ```
3. **Verify:** `py_compile` prints `OK` and `benchmarks/` is writable.

> The client writes to `benchmarks/results_node_c.json` (relative to the
> project root). If you run from a different working directory, pass
> `--out <path>` to override.

---

## 4. Preflight connectivity probe

Run the automated probe **before** the campaign. It checks ping, MTU, and the
two service ports, and exits non-zero if anything is unreachable.

```bash
# Full probe (real network)
bash scripts/node_c_prep/preflight.sh

# Local syntax / logic test — no network, always exits 0
bash scripts/node_c_prep/preflight.sh --dry-run
```

**What it checks**

| Check | Target | Pass criterion |
|-------|--------|----------------|
| Ping | `10.8.0.1` (A), `10.8.0.2` (B) | ≥ 1 reply, RTT recorded |
| MTU | `10.8.0.1`, `10.8.0.2` | largest non-fragmented payload (≤ 1500) |
| Port | `10.8.0.1:50052` (gRPC) | TCP connect succeeds |
| Port | `10.8.0.2:8000` (HTTP) | TCP connect succeeds |

**Verify:** the probe prints a `PREFLIGHT: PASS` summary and exits `0`. If it
fails, fix the corresponding step above (usually WireGuard) and re-run.

---

## 5. Cross-site benchmark (SPHP on/off)

With the preflight green, run the cross-site benchmark. It queries the Node B
gateway with SPHP **disabled** and **enabled**, records client-side RTT, TTFT,
TPS, and total latency, and writes `benchmarks/results_node_c.json`.

```bash
# Default: 10 queries per SPHP mode, 0 ms simulated WAN delay
python3 scripts/node_c_prep/run_node_c_benchmark.py

# Full campaign shape: 50 queries, 40 ms one-way WAN delay, both SPHP modes
python3 scripts/node_c_prep/run_node_c_benchmark.py \
    --queries benchmarks/queries50.txt \
    --wan-delay 40 \
    --both-modes

# Quick smoke test (2 queries, SPHP on only)
python3 scripts/node_c_prep/run_node_c_benchmark.py --n 2 --sphp-only
```

**Key flags**

| Flag | Meaning | Default |
|------|---------|---------|
| `--gateway` | Node B base URL | `http://10.8.0.2:8000` |
| `--queries` | Path to a one-query-per-line file | built-in 10-query set |
| `--n` | Number of queries to run per mode | `10` |
| `--top-k` | Retrieval depth | `10` |
| `--wan-delay` | One-way WAN delay (ms) via `X-Simulate-WAN-Delay` | `0` |
| `--sphp-only` / `--no-sphp` / `--both-modes` | Which SPHP mode(s) to run | `--both-modes` |
| `--out` | Output JSON path | `benchmarks/results_node_c.json` |
| `--rtt-probes` | Number of RTT probe samples | `3` |

**Verify:** the run prints a per-mode summary table and writes
`benchmarks/results_node_c.json`. Open it and confirm both `sphp_enabled` and
`sphp_disabled` arrays are populated with `ttft_ms`, `tps`, `total_ms`, and
`client_rtt_ms` fields.

---

## 6. Pre-campaign sign-off

Before the remote WAN campaign starts, confirm all of the following:

- [ ] `wg0` is up and shows `10.8.0.3/24`
- [ ] `preflight.sh` prints `PREFLIGHT: PASS` (exit 0)
- [ ] `run_node_c_benchmark.py --n 2 --both-modes` completes and writes a
      valid `results_node_c.json`
- [ ] The SPHP-enabled TTFT is ≤ the SPHP-disabled TTFT (sanity check that the
      speculative path is actually engaged)

If all four boxes are ticked, Node C is ready for the 3-node remote WAN
campaign.
