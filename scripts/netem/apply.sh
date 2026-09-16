#!/usr/bin/env bash
# =============================================================================
# apply.sh — Apply one-way WAN emulation (netem) on node_A egress.
#
# Topology:
#   node_A (this WSL2 VM, iface eth4 = 10.8.0.1/24)  <->  peer 10.8.0.2 (laptop)
#   WSL2 mirrored networking: eth4 is the WireGuard-path interface.
#
# Semantics (ONE-WAY shaping):
#   Only packets LEAVING node_A are shaped. Each egress packet gains
#   delay D (ms) and loss L (%). A reply from the peer is NOT shaped,
#   so a ping RTT increases by exactly D (one-way), not 2*D.
#
# Usage:
#   apply.sh RTT_MS LOSS_PCT [RATE]
#     RTT_MS   one-way added delay in milliseconds (integer)
#     LOSS_PCT packet loss percentage, e.g. 0, 5, 10 (integer)
#     RATE     optional token-bucket rate, e.g. 10mbit, 100kbit
#
# Example:
#   apply.sh 150 5            # 150 ms one-way delay, 5% loss
#   apply.sh 150 5 10mbit     # ... plus 10 Mbit/s rate cap
#
# Permissions:
#   tc qdisc replace requires CAP_NET_ADMIN. This script contains NO sudo;
#   run it as root via:  wsl.exe -u root -e bash /home/apath/Work/PDC/Project/scripts/netem/apply.sh 150 5
# =============================================================================
set -euo pipefail

IFACE="eth4"

usage() {
  sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-1}"
}

# --- argument validation -----------------------------------------------------
[ $# -ge 2 ] || usage 1

RTT_MS="$1"
LOSS_PCT="$2"
RATE="${3:-}"

# RTT_MS: non-negative integer
[[ "$RTT_MS" =~ ^[0-9]+$ ]] || { echo "ERROR: RTT_MS must be a non-negative integer, got '$RTT_MS'" >&2; usage 1; }
# LOSS_PCT: integer 0..100
[[ "$LOSS_PCT" =~ ^[0-9]+$ ]] || { echo "ERROR: LOSS_PCT must be an integer, got '$LOSS_PCT'" >&2; usage 1; }
[ "$LOSS_PCT" -le 100 ] || { echo "ERROR: LOSS_PCT must be <= 100, got '$LOSS_PCT'" >&2; usage 1; }
# RATE: optional, must look like a tc rate (e.g. 10mbit, 100kbit, 1000000bit)
if [ -n "$RATE" ]; then
  [[ "$RATE" =~ ^[0-9]+(kbit|mbit|gbit|bit)$ ]] || { echo "ERROR: RATE must be like 10mbit/100kbit, got '$RATE'" >&2; usage 1; }
fi

command -v tc >/dev/null 2>&1 || { echo "ERROR: 'tc' not found (install iproute2)" >&2; exit 1; }

# --- build and apply the qdisc ------------------------------------------------
# netem delay <D>ms loss <L>% [rate <R>]
# 'replace' is idempotent: re-running overwrites any existing root qdisc.
if [ -n "$RATE" ]; then
  echo "Applying: tc qdisc replace dev ${IFACE} root netem delay ${RTT_MS}ms loss ${LOSS_PCT}% rate ${RATE}"
  tc qdisc replace dev "$IFACE" root netem delay "${RTT_MS}"ms loss "${LOSS_PCT}"% rate "$RATE"
else
  echo "Applying: tc qdisc replace dev ${IFACE} root netem delay ${RTT_MS}ms loss ${LOSS_PCT}%"
  tc qdisc replace dev "$IFACE" root netem delay "${RTT_MS}"ms loss "${LOSS_PCT}"%
fi

echo "OK: one-way shaping active on ${IFACE} egress (delay=${RTT_MS}ms loss=${LOSS_PCT}%${RATE:+ rate=${RATE}})."
echo "    Expected ping RTT to 10.8.0.2 increases by ~${RTT_MS}ms (one-way, not doubled)."
echo "    Remove with: clear.sh"
