#!/usr/bin/env bash
# =============================================================================
# preflight.sh — Node C (10.8.0.3) self-readiness probe for the 3-node
# geo-distributed hybrid-RAG campaign.
#
# Node C is a Windows host whose gateway runs under a Windows scheduled task
# (`pdc_gateway_c` -> start_gateway_c.bat -> the Windows venv). This probe is
# a bash script that runs in the C host's WSL distro but delegates every
# Python check to the *Windows* venv (via WSL<->Windows interop) so that the
# network probes use the Windows/WireGuard network stack (WSL2 is NAT and
# cannot reach the 10.8.0.0/24 mesh directly).
#
# Checks and their severity:
#   PASS-gated (a failure here => overall FAIL):
#     * venv_imports   — the Windows venv imports tantivy/fastapi/uvicorn/
#                        httpx/yaml/grpc (grpcio) cleanly
#     * config_loads   — systems/node_c/config.yaml loads via load_config()
#     * index_num_docs — the Tantivy index opens and reports exactly
#                        8,841,823 documents
#     * wg_addr        — the WireGuard adapter carries 10.8.0.3
#     * telemetry      — systems/node_c/data/telemetry is writable
#   WARN (informational; a failure here does NOT block PASS):
#     * b_health       — Node B 10.8.0.2:8000 /health returns ok
#     * a_grpc_tcp     — Node A 10.8.0.1:50052 (gRPC) is TCP-reachable
#
# Usage:
#   preflight.sh                 # real probe; exit 0 iff no FAIL
#   preflight.sh --dry-run       # mock mode: no network/python, always PASS
#
# The final line is exactly `PREFLIGHT: PASS` iff there is no FAIL (WARNs are
# tolerated); otherwise it is `PREFLIGHT: FAIL`.
# =============================================================================
set -uo pipefail

# --- locate the repo root (this file lives at scripts/node_c_prep/) --------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
IMPL_DIR="$REPO_ROOT/systems/node_c/implementation"
TELEMETRY_DIR="$REPO_ROOT/systems/node_c/data/telemetry"
# The Windows venv, seen from WSL through the /mnt/<drive> interop mount.
VENV="$REPO_ROOT/systems/node_c/.venv/Scripts/python.exe"

# The Windows python.exe cannot resolve WSL-style /mnt/<drive>/... paths (they
# only exist inside the WSL filesystem). When a path is handed to the Windows
# interpreter as an *argument* (sys.path entry, config/index location) it must
# be a native Windows path (C:/...). Executing the .exe itself via the /mnt/c
# path is fine (WSL interop), but the strings it receives are not.
to_win_path() {
  local p="$1"
  if [[ "$p" =~ ^/mnt/([a-zA-Z])/(.*)$ ]]; then
    printf '%s:/%s' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
  else
    printf '%s' "$p"
  fi
}
IMPL_DIR_WIN="$(to_win_path "$IMPL_DIR")"

NODE_A="10.8.0.1"
PORT_A=50052
NODE_B="10.8.0.2"
PORT_B=8000
EXPECTED_DOCS=8841823
WG_ADDR="10.8.0.3"

DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run|--mock) DRY_RUN=1 ;;
    -h|--help) sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown argument: $arg (try --help)" >&2; exit 2 ;;
  esac
done

# --- result bookkeeping -----------------------------------------------------
# Each check emits a machine-readable line:  RESULT|<STATUS>|<name>|<detail>
# STATUS is one of PASS / WARN / FAIL.
RESULTS_FILE="$(mktemp)"
trap 'rm -f "$RESULTS_FILE"' EXIT

emit() { # emit STATUS NAME DETAIL
  printf 'RESULT|%s|%s|%s\n' "$1" "$2" "$3" >> "$RESULTS_FILE"
}

# --- human summary + final verdict ------------------------------------------
_print_summary() {
  echo
  echo "-------------------------------------------------------------------"
  local fail=0 warn=0 pass=0
  while IFS='|' read -r _ st nm dt; do
    case "$st" in
      PASS) pass=$((pass+1)); printf '  [PASS] %-16s %s\n' "$nm" "$dt" ;;
      WARN) warn=$((warn+1)); printf '  [WARN] %-16s %s\n' "$nm" "$dt" ;;
      FAIL) fail=$((fail+1)); printf '  [FAIL] %-16s %s\n' "$nm" "$dt" ;;
    esac
  done < "$RESULTS_FILE"
  echo "-------------------------------------------------------------------"
  echo "PREFLIGHT: $((pass+warn+fail)) checks — $pass passed, $warn warned, $fail failed"
  if [ "$fail" -eq 0 ]; then
    echo "PREFLIGHT: PASS"
    return 0
  else
    echo "PREFLIGHT: FAIL — fix the items above and re-run."
    return 1
  fi
}

# =============================================================================
#  DRY-RUN / MOCK MODE — no network, no python, deterministic PASS.
# =============================================================================
if [ "$DRY_RUN" -eq 1 ]; then
  echo "==================================================================="
  echo "PREFLIGHT (DRY-RUN / MOCK) — no network or python calls are made"
  echo "==================================================================="
  for line in \
    "PASS|venv_imports|mock" \
    "PASS|config_loads|mock" \
    "PASS|index_num_docs|mock $EXPECTED_DOCS" \
    "PASS|wg_addr|mock $WG_ADDR" \
    "PASS|telemetry|mock" \
    "WARN|b_health|mock" \
    "WARN|a_grpc_tcp|mock"; do
    IFS='|' read -r st nm dt <<< "$line"
    emit "$st" "$nm" "$dt"
  done
  _print_summary
  exit 0
fi

# =============================================================================
#  REAL PROBE
# =============================================================================
echo "==================================================================="
echo "PREFLIGHT — Node C self-readiness probe"
echo "  repo : $REPO_ROOT"
echo "  venv : $VENV"
echo "  Node A: $NODE_A (gRPC :$PORT_A)   Node B: $NODE_B (HTTP :$PORT_B)"
echo "==================================================================="

# --- Python-side checks (run under the Windows venv via WSL interop) --------
if [ ! -x "$VENV" ]; then
  emit FAIL venv_imports "venv python not found at $VENV"
  emit FAIL config_loads "skipped (no venv)"
  emit FAIL index_num_docs "skipped (no venv)"
  emit WARN b_health "skipped (no venv)"
  emit WARN a_grpc_tcp "skipped (no venv)"
else
  # One Windows-python invocation performs all Python + network checks so the
  # sockets use the Windows/WireGuard stack. Each check is isolated in a
  # try/except so a single failure cannot abort the rest. Its RESULT lines are
  # teed into the results file for the summary below.
  "$VENV" - "$IMPL_DIR_WIN" "$NODE_A" "$PORT_A" "$NODE_B" "$PORT_B" "$EXPECTED_DOCS" <<'PY' 2>/dev/null | grep '^RESULT|' >> "$RESULTS_FILE"
import sys, socket
impl_dir, node_a, port_a, node_b, port_b, expected_docs = sys.argv[1:7]
sys.path.insert(0, impl_dir)
def emit(status, name, detail):
    print(f"RESULT|{status}|{name}|{detail}")
try:
    import tantivy, fastapi, uvicorn, httpx, yaml, grpc
    emit("PASS", "venv_imports", f"tantivy/fastapi/uvicorn/httpx/yaml/grpc({grpc.__version__})")
except Exception as e:
    emit("FAIL", "venv_imports", f"{type(e).__name__}: {e}")
cfg = None
try:
    from src.gateway import load_config, DEFAULT_CONFIG_PATH
    cfg = load_config(DEFAULT_CONFIG_PATH)
    emit("PASS", "config_loads", f"role={cfg['role']} wg={cfg['wireguard_ip']} gw={cfg['gateway']['host']}:{cfg['gateway']['port']}")
except Exception as e:
    emit("FAIL", "config_loads", f"{type(e).__name__}: {e}")
try:
    import tantivy
    idx = tantivy.Index.open(cfg["corpus"]["tantivy_index_path"])
    n = idx.searcher().num_docs
    if n == int(expected_docs):
        emit("PASS", "index_num_docs", f"{n:,} (expected {int(expected_docs):,})")
    else:
        emit("FAIL", "index_num_docs", f"{n:,} != expected {int(expected_docs):,}")
except Exception as e:
    emit("FAIL", "index_num_docs", f"{type(e).__name__}: {e}")
try:
    import httpx
    r = httpx.get(f"http://{node_b}:{port_b}/health", timeout=5)
    if r.status_code == 200 and '"ok"' in r.text:
        emit("WARN", "b_health", f"HTTP {r.status_code} {r.text.strip()}")
    else:
        emit("WARN", "b_health", f"HTTP {r.status_code} {r.text.strip()[:80]}")
except Exception as e:
    emit("WARN", "b_health", f"unreachable ({type(e).__name__})")
try:
    s = socket.create_connection((node_a, int(port_a)), timeout=5)
    s.close()
    emit("WARN", "a_grpc_tcp", f"{node_a}:{port_a} open")
except Exception as e:
    emit("WARN", "a_grpc_tcp", f"{node_a}:{port_a} closed ({type(e).__name__})")
PY
fi

# --- WireGuard address (Windows-side adapter, via cmd ipconfig) -------------
if cmd.exe /c ipconfig 2>/dev/null | grep -q "$WG_ADDR"; then
  emit PASS wg_addr "$WG_ADDR present on WireGuard adapter"
else
  emit FAIL wg_addr "$WG_ADDR not found in ipconfig"
fi

# --- telemetry dir writable --------------------------------------------------
if [ -d "$TELEMETRY_DIR" ] && [ -w "$TELEMETRY_DIR" ]; then
  emit PASS telemetry "writable: $TELEMETRY_DIR"
else
  # Attempt to create it (best-effort) and re-test.
  if mkdir -p "$TELEMETRY_DIR" 2>/dev/null && [ -w "$TELEMETRY_DIR" ]; then
    emit PASS telemetry "created+writable: $TELEMETRY_DIR"
  else
    emit FAIL telemetry "not writable: $TELEMETRY_DIR"
  fi
fi

# --- summary + verdict -------------------------------------------------------
_print_summary
exit $?
