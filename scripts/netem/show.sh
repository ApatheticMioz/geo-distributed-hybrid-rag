#!/usr/bin/env bash
# =============================================================================
# show.sh — Inspect the current bidirectional qdisc (WAN-emulation) state.
#
# Topology:
#   node_A (this WSL2 VM, iface eth4 = 10.8.0.1/24)  <->  peer 10.8.0.2 (laptop)
#
# Semantics (half-per-direction contract):
#   Displays BOTH directions:
#     - EGRESS:  tc -s qdisc show dev eth4  (root qdisc = egress netem)
#     - INGRESS: tc -s qdisc show dev ifb0  (ingress netem, fed by the
#               eth4 ingress qdisc + u32 mirred-redirect filter)
#   Read-only.
#
# Usage:
#   show.sh
#
# Permissions:
#   Requires CAP_NET_ADMIN (tc qdisc show). This script contains NO sudo;
#   run it as root via:
#     wsl.exe -u root -e bash /home/apath/Work/PDC/Project/scripts/netem/show.sh
# =============================================================================
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "ERROR: must be run as root (CAP_NET_ADMIN required for tc)." >&2; exit 1; }

IFACE="eth4"
IFB="ifb0"

usage() {
  sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-1}"
}

[ $# -eq 0 ] || usage 1

command -v tc >/dev/null 2>&1 || { echo "ERROR: 'tc' not found (install iproute2)" >&2; exit 1; }

echo "=== EGRESS (packets leaving node_A): tc -s qdisc show dev ${IFACE} ==="
tc -s qdisc show dev "$IFACE"
echo
echo "=== INGRESS (packets arriving at node_A): tc -s qdisc show dev ${IFB} ==="
if ip link show dev "$IFB" >/dev/null 2>&1; then
  tc -s qdisc show dev "$IFB"
else
  echo "(${IFB} not present — ingress shaping not active)"
fi
echo
echo "=== INGRESS redirect filter on ${IFACE} (parent ffff:) ==="
tc filter show dev "$IFACE" parent ffff: || true

# --- summary of both directions -------------------------------------------------
EG="$(tc qdisc show dev "$IFACE")"
if echo "$EG" | grep -q 'netem'; then
  echo "--- EGRESS: netem active on ${IFACE} root ---"
else
  echo "--- EGRESS: no netem on ${IFACE} root ---"
fi
if ip link show dev "$IFB" >/dev/null 2>&1 && tc qdisc show dev "$IFB" | grep -q 'netem'; then
  echo "--- INGRESS: netem active on ${IFB} (via ${IFACE} ingress redirect) ---"
else
  echo "--- INGRESS: no netem on ${IFB} ---"
fi
