#!/usr/bin/env bash
# =============================================================================
# show.sh — Inspect the current qdisc (WAN-emulation state) on node_A egress.
#
# Topology:
#   node_A (this WSL2 VM, iface eth4 = 10.8.0.1/24)  <->  peer 10.8.0.2 (laptop)
#
# Semantics:
#   Prints 'tc -s qdisc show dev eth4' (with per-qdisc statistics: packets,
#   bytes, drops, overlimits). Read-only: safe to run as a plain user, but
#   note that on some kernels reading qdisc stats still requires
#   CAP_NET_ADMIN — in that case tc prints an EPERM error, which is the
#   expected evidence that the script must be run via:
#     wsl.exe -u root -e bash /home/apath/Work/PDC/Project/scripts/netem/show.sh
#
# Usage:
#   show.sh
# =============================================================================
set -euo pipefail

IFACE="eth4"

usage() {
  sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-1}"
}

[ $# -eq 0 ] || usage 1

command -v tc >/dev/null 2>&1 || { echo "ERROR: 'tc' not found (install iproute2)" >&2; exit 1; }

echo "--- tc -s qdisc show dev ${IFACE} ---"
# Do NOT let a permission failure abort the script: capture and report it.
if ! OUT="$(tc -s qdisc show dev "$IFACE" 2>&1)"; then
  echo "$OUT"
  echo "NOTE: tc failed (likely EPERM — run via 'wsl.exe -u root' to see stats)."
  exit 1
fi
echo "$OUT"

if echo "$OUT" | grep -q 'netem'; then
  echo "--- netem shaping IS active on ${IFACE} egress (one-way: ping RTT += delay) ---"
else
  echo "--- no netem qdisc on ${IFACE} (path is unshaped) ---"
fi
