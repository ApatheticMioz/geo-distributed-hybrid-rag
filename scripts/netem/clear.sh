#!/usr/bin/env bash
# =============================================================================
# clear.sh — Remove all bidirectional netem/HTB state on node_A.
#
# Topology:
#   node_A (10.8.0.1/24)  <->  peers (10.8.0.2 laptop, 10.8.0.3 friend-laptop)
#
# Semantics:
#   Deletes, in dependency order:
#     1. ifb0 root qdisc        (ingress-side netem / HTB)
#     2. IFACE ingress qdisc    (u32 mirred-redirect filter + ingress qdisc)
#     3. IFACE root qdisc       (egress-side netem / HTB; back to default)
#     4. ifb0 device
#     5. modprobe -r ifb        (best effort)
#   Idempotent: every step tolerates "not present".
# =============================================================================
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "ERROR: must be run as root (CAP_NET_ADMIN required). Use: wsl.exe -u root -e bash $0 ..." >&2; exit 1; }

detect_iface() {
  if [ -n "${NETEM_IFACE:-}" ]; then
    echo "$NETEM_IFACE"
    return
  fi
  local dev
  dev="$(ip route get 10.8.0.2 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1)}' | head -n1)"
  if [ -n "$dev" ]; then
    echo "$dev"
    return
  fi
  dev="$(ip -o addr show to 10.8.0.1/24 2>/dev/null | awk '{print $2}' | head -n1)"
  if [ -n "$dev" ]; then
    echo "$dev"
    return
  fi
  echo "eth5"
}

IFACE="$(detect_iface)"
IFB="ifb0"

command -v tc >/dev/null 2>&1 || { echo "ERROR: 'tc' not found (install iproute2)" >&2; exit 1; }

# 1. ifb0 root qdisc
tc qdisc del dev "$IFB" root 2>/dev/null || true
# 2. IFACE ingress qdisc
tc qdisc del dev "$IFACE" ingress 2>/dev/null || true
# 3. IFACE root qdisc
tc qdisc del dev "$IFACE" root 2>/dev/null || true
# 4. ifb0 device
ip link del dev "$IFB" 2>/dev/null || true
# 5. unload ifb module
modprobe -r ifb 2>/dev/null || true

OUT="$(tc qdisc show dev "$IFACE")"
echo "--- tc qdisc show dev ${IFACE} ---"
echo "$OUT"

if echo "$OUT" | grep -qE 'netem|htb'; then
  echo "ERROR: netem/htb still present on ${IFACE} after clear" >&2
  exit 1
fi

echo "OK: ${IFACE} has no netem/htb qdisc (all shaping removed; ${IFB} deleted)."
