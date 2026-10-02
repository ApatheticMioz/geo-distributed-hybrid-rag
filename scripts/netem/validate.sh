#!/usr/bin/env bash
# =============================================================================
# validate.sh — Closed-loop validation of per-link bidirectional netem RTT.
#
# Semantics:
#   1. Clear all qdiscs.
#   2. Measure baseline median RTT over 5 pings.
#   3. Apply shaping via apply.sh for the target link.
#   4. Measure shaped median RTT over 5 pings.
#   5. Enforce gate: |shaped - baseline - RTT_MS| <= 30% of RTT_MS.
#   6. Trap cleanup clears all netem state on exit.
#
# Syntax:
#   validate.sh --link-b <RTT_MS>
#   validate.sh --link-c <RTT_MS>
#   validate.sh --link-b <RTT_B> --link-c <RTT_C>
# =============================================================================
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "FATAL: validate.sh must run as root (CAP_NET_ADMIN required)." >&2
  exit 1
fi

command -v tc >/dev/null 2>&1 || { echo "FATAL: 'tc' not found." >&2; exit 1; }
command -v ping >/dev/null 2>&1 || { echo "FATAL: 'ping' not found." >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PING_COUNT=5

usage() {
  echo "Usage:" >&2
  echo "  $0 --link-b <RTT_MS>" >&2
  echo "  $0 --link-c <RTT_MS>" >&2
  echo "  $0 --link-b <RTT_B> --link-c <RTT_C>" >&2
  exit 1
}

RTT_B=""
RTT_C=""

[ $# -gt 0 ] || usage

while [ $# -gt 0 ]; do
  case "$1" in
    --link-b)
      [ $# -ge 2 ] || usage
      RTT_B="$2"
      shift 2
      ;;
    --link-c)
      [ $# -ge 2 ] || usage
      RTT_C="$2"
      shift 2
      ;;
    *)
      echo "FATAL: Unrecognized argument '$1'. Ambiguous positional arguments are forbidden." >&2
      usage
      ;;
  esac
done

validate_rtt_value() {
  local val="$1" label="$2"
  if [[ ! "$val" =~ ^[0-9]+$ ]] || [ "$val" -lt 1 ]; then
    echo "FATAL: Target RTT for $label must be a positive integer >= 1 ms, got '$val'." >&2
    exit 1
  fi
}

[ -n "$RTT_B" ] && validate_rtt_value "$RTT_B" "Link B"
[ -n "$RTT_C" ] && validate_rtt_value "$RTT_C" "Link C"

# Cleanup on exit
cleanup() {
  bash "$SCRIPT_DIR/clear.sh" >/dev/null 2>&1 || true
}
trap cleanup EXIT

median() {
  local values="$1"
  local n
  n="$(echo "$values" | grep -c . || true)"
  [ "$n" -ge 1 ] || { echo "FATAL: No ping samples captured." >&2; return 1; }
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
    if [ -n "$out" ]; then
      echo "$out"
    fi
  done
}

validate_target_link() {
  local label="$1" ip="$2" target_rtt="$3"

  echo "========================================================================"
  echo " VALIDATION: Link ${label} (${ip}) — Target Added RTT: ${target_rtt} ms"
  echo "========================================================================"

  bash "$SCRIPT_DIR/clear.sh" >/dev/null

  # 1. Baseline
  local base_samples
  base_samples="$(ping_samples "$ip")"
  local count
  count="$(echo "$base_samples" | grep -c . || true)"
  if [ "$count" -lt 1 ]; then
    echo "FATAL: Ping baseline failed. Peer ${ip} is unreachable." >&2
    return 1
  fi
  local base_med
  base_med="$(median "$base_samples")"
  echo "Baseline median RTT : ${base_med} ms"

  # 2. Apply Shaping
  if [ "$label" = "B" ]; then
    bash "$SCRIPT_DIR/apply.sh" --link-b "$target_rtt" 0 >/dev/null
  else
    bash "$SCRIPT_DIR/apply.sh" --link-c "$target_rtt" 0 >/dev/null
  fi

  # 3. Shaped Measurement
  local shaped_samples
  shaped_samples="$(ping_samples "$ip")"
  count="$(echo "$shaped_samples" | grep -c . || true)"
  if [ "$count" -lt 1 ]; then
    echo "FATAL: Shaped ping measurement failed for ${ip}." >&2
    return 1
  fi
  local shaped_med
  shaped_med="$(median "$shaped_samples")"
  echo "Shaped median RTT   : ${shaped_med} ms"

  # 4. Enforce ±30% Gate
  local diff tol lower upper pass
  diff="$(awk -v s="$shaped_med" -v b="$base_med" 'BEGIN { printf "%.3f", s - b }')"
  tol="$(awk -v t="$target_rtt" 'BEGIN { printf "%.3f", 0.30 * t }')"
  lower="$(awk -v t="$target_rtt" -v tol="$tol" 'BEGIN { printf "%.3f", t - tol }')"
  upper="$(awk -v t="$target_rtt" -v tol="$tol" 'BEGIN { printf "%.3f", t + tol }')"
  pass="$(awk -v d="$diff" -v l="$lower" -v u="$upper" 'BEGIN { print (d >= l && d <= u) ? 1 : 0 }')"

  echo "Added RTT (Observed): ${diff} ms (Acceptable Gate [${lower}, ${upper}] ms)"

  if [ "$pass" -eq 1 ]; then
    echo "Status: PASS"
  else
    echo "Status: FAIL (Observed RTT ${diff} ms is outside ±30% acceptance window)"
    return 1
  fi
}

[ -n "$RTT_B" ] && validate_target_link "B" "10.8.0.2" "$RTT_B"
[ -n "$RTT_C" ] && validate_target_link "C" "10.8.0.3" "$RTT_C"

echo "ALL VALIDATIONS PASSED."
