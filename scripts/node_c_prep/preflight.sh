#!/usr/bin/env bash
# =============================================================================
# preflight.sh — Node C connectivity probe for the 3-node remote WAN campaign.
#
# Probes, from Node C (10.8.0.3):
#   * ping  + MTU discovery to Node A (10.8.0.1) and Node B (10.8.0.2)
#   * TCP port probes for gRPC :50052 (Node A) and HTTP :8000 (Node B)
#
# Usage:
#   preflight.sh                 # real network probe; exit 0 only if all pass
#   preflight.sh --dry-run       # mock mode: no network, always exit 0
#   preflight.sh --mock          # alias for --dry-run
#
# The script is intentionally dependency-light: it uses ping, and a
# /dev/tcp-based port probe (bash builtin) so it runs on a stock laptop.
# =============================================================================
set -uo pipefail

# --- defaults ----------------------------------------------------------------
NODE_A="10.8.0.1"
NODE_B="10.8.0.2"
PORT_A=50052          # Node A gRPC GenerationOrchestrator
PORT_B=8000           # Node B HTTP FastAPI gateway
PING_COUNT=3
MTU_MAX=1500
MTU_MIN=576
DRY_RUN=0

# --- argument parsing ---------------------------------------------------------
for arg in "$@"; do
  case "$arg" in
    --dry-run|--mock) DRY_RUN=1 ;;
    --node-a) : ;;        # reserved for future overrides
    -h|--help)
      sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "Unknown argument: $arg (try --help)" >&2; exit 2 ;;
  esac
done

# --- helpers ------------------------------------------------------------------
PASS=0
FAIL=0

ok()   { printf '  [PASS] %s\n' "$1"; PASS=$((PASS+1)); }
bad()  { printf '  [FAIL] %s\n' "$1"; FAIL=$((FAIL+1)); }
info() { printf '  [info] %s\n' "$1"; }

# ping_probe HOST -> prints "ok" / "fail"; records RTT in global LAST_RTT
ping_probe() {
  local host="$1"
  if ping -c "$PING_COUNT" -W 2 "$host" >/dev/null 2>&1; then
    # Extract a representative RTT (average line) if available.
    local rtt
    rtt=$(ping -c "$PING_COUNT" -W 2 "$host" 2>/dev/null \
          | grep -oE 'rtt=([0-9.]+)' | head -1 | grep -oE '[0-9.]+')
    LAST_RTT="${rtt:-?}"
    echo "ok"
  else
    LAST_RTT="n/a"
    echo "fail"
  fi
}

# mtu_probe HOST -> prints the largest non-fragmented payload (or "n/a").
# Uses ping -M do (do-not-fragment) with a binary search between MTU_MIN and
# MTU_MAX. Falls back gracefully if the platform lacks -M.
mtu_probe() {
  local host="$1"
  local lo=$MTU_MIN hi=$MTU_MAX best=$MTU_MIN
  # Probe the max first; if it passes we are done.
  if ping -M do -s "$((hi-28))" -c 1 -W 2 "$host" >/dev/null 2>&1; then
    echo "$hi"; return
  fi
  # Binary search for the largest payload that does not fragment.
  while [ "$lo" -lt "$hi" ]; do
    local mid=$(( (lo + hi + 1) / 2 ))
    if ping -M do -s "$((mid-28))" -c 1 -W 2 "$host" >/dev/null 2>&1; then
      lo=$mid; best=$mid
    else
      hi=$((mid-1))
    fi
  done
  echo "$best"
}

# port_probe HOST PORT -> "ok" / "fail" using bash /dev/tcp (no nc required).
port_probe() {
  local host="$1" port="$2"
  if (exec 3<>"/dev/tcp/${host}/${port}") 2>/dev/null; then
    exec 3>&- 3<&- 2>/dev/null || true
    echo "ok"
  else
    echo "fail"
  fi
}

# =============================================================================
#  DRY-RUN / MOCK MODE — no network, deterministic, always exit 0.
# =============================================================================
if [ "$DRY_RUN" -eq 1 ]; then
  echo "==================================================================="
  echo "PREFLIGHT (DRY-RUN / MOCK) — no network calls are made"
  echo "==================================================================="
  echo "Node A: $NODE_A (gRPC :$PORT_A)"
  echo "Node B: $NODE_B (HTTP :$PORT_B)"
  echo
  echo "  [PASS] ping  $NODE_A  (mock RTT=1.0 ms)"
  echo "  [PASS] ping  $NODE_B  (mock RTT=1.0 ms)"
  echo "  [PASS] mtu   $NODE_A  (mock 1500)"
  echo "  [PASS] mtu   $NODE_B  (mock 1500)"
  echo "  [PASS] port  $NODE_A:$PORT_A  (mock open)"
  echo "  [PASS] port  $NODE_B:$PORT_B  (mock open)"
  echo
  echo "PREFLIGHT: PASS (dry-run) — 6/6 checks simulated OK"
  exit 0
fi

# =============================================================================
#  REAL NETWORK PROBE
# =============================================================================
echo "==================================================================="
echo "PREFLIGHT — Node C connectivity probe"
echo "  Node A: $NODE_A (gRPC :$PORT_A)"
echo "  Node B: $NODE_B (HTTP :$PORT_B)"
echo "==================================================================="

# --- ping + MTU to Node A -----------------------------------------------------
echo
echo "[Node A: $NODE_A]"
if [ "$(ping_probe "$NODE_A")" = "ok" ]; then
  ok "ping $NODE_A (RTT=${LAST_RTT} ms)"
  m=$(mtu_probe "$NODE_A")
  ok "MTU $NODE_A = $m"
else
  bad "ping $NODE_A unreachable"
  bad "MTU $NODE_A skipped (no connectivity)"
fi

# --- ping + MTU to Node B -----------------------------------------------------
echo
echo "[Node B: $NODE_B]"
if [ "$(ping_probe "$NODE_B")" = "ok" ]; then
  ok "ping $NODE_B (RTT=${LAST_RTT} ms)"
  m=$(mtu_probe "$NODE_B")
  ok "MTU $NODE_B = $m"
else
  bad "ping $NODE_B unreachable"
  bad "MTU $NODE_B skipped (no connectivity)"
fi

# --- port probes --------------------------------------------------------------
echo
echo "[Service ports]"
if [ "$(port_probe "$NODE_A" "$PORT_A")" = "ok" ]; then
  ok "gRPC  $NODE_A:$PORT_A open"
else
  bad "gRPC  $NODE_A:$PORT_A closed/unreachable"
fi
if [ "$(port_probe "$NODE_B" "$PORT_B")" = "ok" ]; then
  ok "HTTP  $NODE_B:$PORT_B open"
else
  bad "HTTP  $NODE_B:$PORT_B closed/unreachable"
fi

# --- summary ------------------------------------------------------------------
echo
echo "-------------------------------------------------------------------"
echo "PREFLIGHT: $((PASS+FAIL)) checks — $PASS passed, $FAIL failed"
if [ "$FAIL" -eq 0 ]; then
  echo "PREFLIGHT: PASS"
  exit 0
else
  echo "PREFLIGHT: FAIL — fix the items above and re-run."
  exit 1
fi
