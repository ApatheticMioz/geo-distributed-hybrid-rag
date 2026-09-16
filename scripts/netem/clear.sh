#!/usr/bin/env bash
# =============================================================================
# clear.sh — Remove any netem/WAN-emulation qdisc from node_A egress.
#
# Topology:
#   node_A (this WSL2 VM, iface eth4 = 10.8.0.1/24)  <->  peer 10.8.0.2 (laptop)
#
# Semantics:
#   Deletes the root qdisc on eth4, restoring the default (unshaped) path.
#   Idempotent: if no qdisc is present, the delete is a no-op (suppressed)
#   and the script still exits 0.
#
# Usage:
#   clear.sh
#
# Permissions:
#   tc qdisc del requires CAP_NET_ADMIN. This script contains NO sudo;
#   run it as root via:  wsl.exe -u root -e bash /home/apath/Work/PDC/Project/scripts/netem/clear.sh
# =============================================================================
set -euo pipefail

IFACE="eth4"

usage() {
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-1}"
}

[ $# -eq 0 ] || usage 1

command -v tc >/dev/null 2>&1 || { echo "ERROR: 'tc' not found (install iproute2)" >&2; exit 1; }

# Delete the root qdisc; tolerate the "No such file" case (already clear).
tc qdisc del dev "$IFACE" root 2>/dev/null || true

# Confirm the qdisc is actually gone.
REMAINING="$(tc qdisc show dev "$IFACE" | grep -E 'qdisc (netem|hfsc|sfq|fq|prio|tbf|ingress)' || true)"
if [ -n "$REMAINING" ]; then
  echo "WARNING: qdisc still present on ${IFACE} after delete:" >&2
  echo "$REMAINING" >&2
  exit 1
fi

echo "OK: ${IFACE} root qdisc cleared (no netem shaping active)."
