#!/usr/bin/env bash
# =============================================================================
# validate.sh — Closed-loop check for per-leg bidirectional netem RTT.
#
# Topology:
#   node_A (10.8.0.1/24)  <->  peer B (10.8.0.2) / peer C (10.8.0.3)
#
# Semantics (TWO-WAY shaping — half-per-direction contract):
#   Checks that:
#     |shaped_median - baseline_median - RTT_MS| <= 30% of RTT_MS
#
# Usage:
#   validate.sh RTT_MS                              # Tests default Link B (10.8.0.2)
#   validate.sh --peer <b|c|all|IP> RTT_MS          # Tests specified link(s)
#
# Example:
#   validate.sh 15                                  # Validates Link B at 15ms RTT
#   validate.sh --peer c 40                         # Validates Link C at 40ms RTT
#   validate.sh --peer all 80                       # Validates both links at 80ms RTT
#
# Permissions:
#   Requires CAP_NET_ADMIN (tc) and ping. Run as root via:
#     wsl.exe -u root -e bash /home/apath/Work/PDC/Project/scripts/netem/validate.sh 15
# =============================================================================
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "ERROR: must be run as root (CAP_NET_ADMIN required). Use: wsl.exe -u root -e bash $0 ..." >&2; exit 1; }

command -v tc >/dev/null 2>&1 || { echo "ERROR: 'tc' not found (install iproute2)" >&2; exit 1; }
command -v ping >/dev/null 2>&1 || { echo "ERROR: 'ping' not found (install iputils-ping)" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PING_COUNT=5

usage() {
  echo "Usage:"
  echo "  validate.sh RTT_MS"
  echo "  validate.sh --peer <b|c|all|IP> RTT_MS"
  exit "${1:-1}"
}

PEER_OPT="b"
RTT_MS=""

if [ $# -eq 1 ]; then
  RTT_MS="$1"
elif [ $# -ge 2 ] && [ "$1" = "--peer" ]; then
  PEER_OPT="$2"
  RTT_MS="$3"
else
  usage 1
fi

[[ "$RTT_MS" =~ ^[0-9]+$ ]] || { echo "ERROR: RTT_MS must be a non-negative integer, got '$RTT_MS'" >&2; usage 1; }
[ "$RTT_MS" -ge 1 ] || { echo "ERROR: RTT_MS must be >= 1 for a meaningful check" >&2; usage 1; }

# Always restore unshaped path on exit
cleanup() {
  echo "--- cleanup: clearing qdisc ---"
  bash "$SCRIPT_DIR/clear.sh" >/dev/null 2>&1 || true
}
trap cleanup EXIT

median() {
  local values="$1"
  local n
  n="$(echo "$values" | grep -c . || true)"
  [ "$n" -ge 1 ] || { echo "ERROR: no ping samples" >&2; return 1; }
  local mid=$(( (n + 1) / 2 ))
  if [ $(( n % 2 )) -eq 1 ]; then
    echo "$values" | sort -n | sed -n "${mid}p"
  else
    local a b
    a="$(echo "$values" | sort -n | sed -n "${mid}p")"
    b="$(echo "$values" | sort -n | sed -n "$(( mid + 1 ))p")"
    awk -v a="$a" -v b="$b" 'BEGIN { printf "%.3f", (a + b) / 2 }'
  fi
}

ping_samples() {
  local target="$1"
  local i out
  for i in $(seq 1 "$PING_COUNT"); do
    out="$(ping -c 1 -W 2 "$target" 2>/dev/null | grep -oE 'time=[0-9]+(\.[0-9]+)?' | cut -d= -f2 || true)"
    [ -n "$out" ] && echo "$out"
  done
}

validate_single_peer() {
  local peer_label="$1"
  local peer_ip="$2"
  local rtt="$3"

  echo "========================================================================"
  echo " Validating Link ${peer_label} (${peer_ip}) for target RTT: ${rtt} ms"
  echo "========================================================================"

  # 1. Baseline
  echo "=== [1/4] Clearing qdisc ==="
  bash "$SCRIPT_DIR/clear.sh" >/dev/null

  echo "=== [2/4] Measuring baseline RTT to ${peer_ip} (${PING_COUNT} pings) ==="
  local base_samples
  base_samples="$(ping_samples "$peer_ip")"
  local base_n
  base_n="$(echo "$base_samples" | grep -c . || true)"
  [ "$base_n" -ge 1 ] || { echo "ERROR: no baseline pings succeeded to ${peer_ip} (is host online?)" >&2; return 1; }
  local base_median
  base_median="$(median "$base_samples")"
  echo "Baseline median: ${base_median} ms"

  # 2. Apply
  echo "=== [3/4] Applying netem: peer=${peer_label} target_RTT=${rtt} ms loss=0% ==="
  bash "$SCRIPT_DIR/apply.sh" --peer "$peer_label" "$rtt" 0 >/dev/null

  # 3. Shaped measurement
  echo "=== [4/4] Measuring shaped RTT to ${peer_ip} (${PING_COUNT} pings) ==="
  local shaped_samples
  shaped_samples="$(ping_samples "$peer_ip")"
  local shaped_n
  shaped_n="$(echo "$shaped_samples" | grep -c . || true)"
  [ "$shaped_n" -ge 1 ] || { echo "ERROR: no shaped pings succeeded to ${peer_ip}" >&2; return 1; }
  local shaped_median
  shaped_median="$(median "$shaped_samples")"
  echo "Shaped median: ${shaped_median} ms"

  # 4. Closed-loop check (tolerance = 30%)
  local diff tol lower upper pass
  diff="$(awk -v s="$shaped_median" -v b="$base_median" 'BEGIN { printf "%.3f", s - b }')"
  tol="$(awk -v t="$rtt" 'BEGIN { printf "%.3f", 0.30 * t }')"
  lower="$(awk -v t="$rtt" -v tol="$tol" 'BEGIN { printf "%.3f", t - tol }')"
  upper="$(awk -v t="$rtt" -v tol="$tol" 'BEGIN { printf "%.3f", t + tol }')"
  pass="$(awk -v d="$diff" -v l="$lower" -v u="$upper" 'BEGIN { print (d >= l && d <= u) ? 1 : 0 }')"

  echo "------------------------------------------------------------------------"
  echo "Link: ${peer_label} (${peer_ip})"
  echo "Target added RTT: ${rtt} ms (acceptable window [${lower}, ${upper}] ms)"
  echo "Observed added RTT (shaped - baseline): ${diff} ms"

  if [ "$pass" -eq 1 ]; then
    echo "Result: PASS (observed added RTT is within ±30% of target)"
  else
    echo "Result: FAIL (observed added RTT outside ±30% window)"
    return 1
  fi
}

case "$PEER_OPT" in
  b|B|node_b|10.8.0.2)
    validate_single_peer "B" "10.8.0.2" "$RTT_MS"
    ;;
  c|C|node_c|10.8.0.3)
    validate_single_peer "C" "10.8.0.3" "$RTT_MS"
    ;;
  all|ALL)
    validate_single_peer "B" "10.8.0.2" "$RTT_MS"
    validate_single_peer "C" "10.8.0.3" "$RTT_MS"
    ;;
  *)
    validate_single_peer "CUSTOM" "$PEER_OPT" "$RTT_MS"
    ;;
esac
