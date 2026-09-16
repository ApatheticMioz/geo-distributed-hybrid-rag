#!/usr/bin/env bash
# =============================================================================
# validate.sh — Closed-loop check that netem shaping produces the expected RTT.
#
# Topology:
#   node_A (this WSL2 VM, iface eth4 = 10.8.0.1/24)  <->  peer 10.8.0.2 (laptop)
#
# Semantics (ONE-WAY shaping):
#   Only node_A egress is shaped, so each outgoing ping request gains delay D
#   while the reply is unshaped. Therefore the measured RTT increases by
#   exactly D (one-way), NOT 2*D. This script verifies:
#     shaped_median - baseline_median  ~=  RTT_MS  (within +/-20%)
#
# Procedure:
#   1. clear.sh          (start from a known-unshaped state)
#   2. 5 pings to 10.8.0.2 -> baseline median RTT (ms, via sort)
#   3. apply.sh RTT_MS 0 (delay only, no loss, for a clean RTT measurement)
#   4. 5 pings to 10.8.0.2 -> shaped median RTT
#   5. PASS if |shaped - baseline - RTT_MS| <= 20% of RTT_MS, else exit 1
#   6. ALWAYS clear.sh at the end (trap), even on failure.
#
# Usage:
#   validate.sh RTT_MS
#
# Permissions:
#   Requires CAP_NET_ADMIN (tc) and ping. This script contains NO sudo;
#   run it as root via:  wsl.exe -u root -e bash /home/apath/Work/PDC/Project/scripts/netem/validate.sh 150
# =============================================================================
set -euo pipefail

IFACE="eth4"
PEER="10.8.0.2"
PING_COUNT=5
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-1}"
}

# --- argument validation -------------------------------------------------------
[ $# -eq 1 ] || usage 1
RTT_MS="$1"
[[ "$RTT_MS" =~ ^[0-9]+$ ]] || { echo "ERROR: RTT_MS must be a non-negative integer, got '$RTT_MS'" >&2; usage 1; }
[ "$RTT_MS" -ge 1 ] || { echo "ERROR: RTT_MS must be >= 1 for a meaningful check" >&2; usage 1; }

command -v tc >/dev/null 2>&1 || { echo "ERROR: 'tc' not found (install iproute2)" >&2; exit 1; }
command -v ping >/dev/null 2>&1 || { echo "ERROR: 'ping' not found (install iputils-ping)" >&2; exit 1; }

# --- always restore the unshaped path, even on failure --------------------------
cleanup() {
  echo "--- cleanup: clearing qdisc on ${IFACE} ---"
  bash "$SCRIPT_DIR/clear.sh"
}
trap cleanup EXIT

# --- median of a list of numbers (ms, one per line) ----------------------------
median() {
  # $1 = newline-separated values; prints the median (middle of sorted list)
  local n
  n="$(echo "$1" | wc -l)"
  [ "$n" -ge 1 ] || { echo "ERROR: no ping samples" >&2; return 1; }
  # odd count -> middle element; even count -> average of the two middle ones
  local mid=$(( (n + 1) / 2 ))
  if [ $(( n % 2 )) -eq 1 ]; then
    echo "$1" | sort -n | sed -n "${mid}p"
  else
    local a b
    a="$(echo "$1" | sort -n | sed -n "${mid}p")"
    b="$(echo "$1" | sort -n | sed -n "$(( mid + 1 ))p")"
    awk -v a="$a" -v b="$b" 'BEGIN { printf "%.3f", (a + b) / 2 }'
  fi
}

# --- run N pings, print one RTT (ms) per line ---------------------------------
ping_samples() {
  local i out
  for i in $(seq 1 "$PING_COUNT"); do
    # Extract ONLY the per-ping 'time=<ms>' value. The stats line
    # ('... 0% packet loss, time 0ms') uses a SPACE, not '=', so a
    # 'time=' pattern never matches it — the old 'time[= ]+' pattern
    # matched both and injected spurious 0s into the sample list.
    out="$(ping -c 1 -W 2 "$PEER" 2>/dev/null | grep -oE 'time=[0-9]+(\.[0-9]+)?' | cut -d= -f2 || true)"
    [ -n "$out" ] && echo "$out"
  done
}

# --- 1. baseline (unshaped) ----------------------------------------------------
echo "=== [1/4] clearing qdisc on ${IFACE} ==="
bash "$SCRIPT_DIR/clear.sh"

echo "=== [2/4] baseline: ${PING_COUNT} pings to ${PEER} (unshaped) ==="
BASE_SAMPLES="$(ping_samples)"
BASE_N="$(echo "$BASE_SAMPLES" | grep -c . || true)"
[ "$BASE_N" -ge 1 ] || { echo "ERROR: no baseline pings succeeded (is ${PEER} reachable?)" >&2; exit 1; }
BASE_MEDIAN="$(median "$BASE_SAMPLES")"
echo "baseline samples (ms): $(echo "$BASE_SAMPLES" | tr '\n' ' ')"
echo "baseline median: ${BASE_MEDIAN} ms"

# --- 3. apply shaping (delay only, loss=0 for a clean RTT measurement) --------
echo "=== [3/4] applying netem delay ${RTT_MS}ms loss 0% on ${IFACE} ==="
bash "$SCRIPT_DIR/apply.sh" "$RTT_MS" 0

# --- 4. shaped measurement -----------------------------------------------------
echo "=== [4/4] shaped: ${PING_COUNT} pings to ${PEER} ==="
SHAPED_SAMPLES="$(ping_samples)"
SHAPED_N="$(echo "$SHAPED_SAMPLES" | grep -c . || true)"
[ "$SHAPED_N" -ge 1 ] || { echo "ERROR: no shaped pings succeeded" >&2; exit 1; }
SHAPED_MEDIAN="$(median "$SHAPED_SAMPLES")"
echo "shaped samples (ms): $(echo "$SHAPED_SAMPLES" | tr '\n' ' ')"
echo "shaped median: ${SHAPED_MEDIAN} ms"

# --- 5. verdict: shaped - baseline ~= RTT_MS within +/-20% ---------------------
# (one-way shaping: RTT delta should equal the one-way delay, not 2x)
# PASS iff |delta - RTT_MS| <= 0.2 * RTT_MS  (symmetric tolerance: a delta
# far BELOW expected is a FAIL, not a PASS — the old 'ad - d <= tol' test
# accepted any delta below expected, e.g. 49.925 vs 100).
VERDICT="$(awk -v b="$BASE_MEDIAN" -v s="$SHAPED_MEDIAN" -v d="$RTT_MS" 'BEGIN {
  delta = s - b
  tol = d * 0.20
  diff = delta - d
  if (diff < 0) ad = -diff; else ad = diff
  if (ad <= tol) { printf "PASS delta=%.3fms (shaped=%.3f - baseline=%.3f) expected=%.0fms tol=+/-%.2fms", delta, s, b, d, tol }
  else { printf "FAIL delta=%.3fms (shaped=%.3f - baseline=%.3f) expected=%.0fms tol=+/-%.2fms", delta, s, b, d, tol }
}')"
echo
echo "RESULT: ${VERDICT}"

case "$VERDICT" in
  PASS*)
    echo "PASS: one-way shaping verified (RTT delta ~= ${RTT_MS}ms)."
    exit 0
    ;;
  *)
    echo "FAIL: RTT delta does not match ${RTT_MS}ms within 20%." >&2
    exit 1
    ;;
esac
# cleanup trap fires here: qdisc is always cleared before exit.
