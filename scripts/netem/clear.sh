#!/usr/bin/env bash
# =============================================================================
# clear.sh — Remove all bidirectional netem/WAN-emulation state on node_A.
#
# Topology:
#   node_A (this WSL2 VM, iface eth4 = 10.8.0.1/24)  <->  peer 10.8.0.2 (laptop)
#
# Semantics (inverse of apply.sh's half-per-direction contract):
#   Deletes, in dependency order:
#     1. ifb0 root qdisc        (ingress-side netem)
#     2. eth4 ingress qdisc     (u32 mirred-redirect filter + ingress qdisc)
#     3. eth4 root qdisc        (egress-side netem; back to kernel default)
#     4. ifb0 device
#     5. modprobe -r ifb        (best effort; other consumers may keep it)
#   Idempotent: every step tolerates "not present"; safe to run twice.
#   Verifies at the end that 'tc qdisc show dev eth4' shows no netem.
#
# Usage:
#   clear.sh
#
# Permissions:
#   Requires CAP_NET_ADMIN. This script contains NO sudo; run it as root via:
#     wsl.exe -u root -e bash /home/apath/Work/PDC/Project/scripts/netem/clear.sh
# =============================================================================
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "ERROR: must be run as root (CAP_NET_ADMIN required for tc/ip/modprobe)." >&2; exit 1; }

IFACE="eth4"
IFB="ifb0"

usage() {
  sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-1}"
}

[ $# -eq 0 ] || usage 1

command -v tc >/dev/null 2>&1 || { echo "ERROR: 'tc' not found (install iproute2)" >&2; exit 1; }

# 1. ifb0 root qdisc (no-op if ifb0 absent)
tc qdisc del dev "$IFB" root 2>/dev/null || true
# 2. eth4 ingress qdisc (removes the u32 mirred-redirect filter with it)
tc qdisc del dev "$IFACE" ingress 2>/dev/null || true
# 3. eth4 root qdisc -> kernel default
tc qdisc del dev "$IFACE" root 2>/dev/null || true
# 4. ifb0 device
ip link del dev "$IFB" 2>/dev/null || true
# 5. unload ifb module (best effort: fails if other ifb devices remain)
modprobe -r ifb 2>/dev/null || true

# --- verify: eth4 must show no netem -------------------------------------------
OUT="$(tc qdisc show dev "$IFACE")"
echo "--- tc qdisc show dev ${IFACE} ---"
echo "$OUT"
if echo "$OUT" | grep -q 'netem'; then
  echo "ERROR: netem still present on ${IFACE} after clear" >&2
  exit 1
fi
echo "OK: ${IFACE} has no netem qdisc (egress and ingress shaping removed; ${IFB} deleted)."
