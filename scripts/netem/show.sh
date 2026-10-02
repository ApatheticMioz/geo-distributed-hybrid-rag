#!/usr/bin/env bash
# =============================================================================
# show.sh — Inspect the current bidirectional qdisc / WAN-emulation state.
#
# Topology:
#   node_A (10.8.0.1/24)  <->  peers (10.8.0.2 laptop, 10.8.0.3 friend-laptop)
# =============================================================================
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "ERROR: must be run as root (CAP_NET_ADMIN required)." >&2; exit 1; }

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

echo "=== EGRESS (packets leaving node_A on ${IFACE}) ==="
tc -s qdisc show dev "$IFACE"
echo "--- Classes on ${IFACE} ---"
tc class show dev "$IFACE" || true
echo "--- Filters on ${IFACE} ---"
tc filter show dev "$IFACE" || true
echo

echo "=== INGRESS (packets arriving at node_A, mirrored to ${IFB}) ==="
if ip link show dev "$IFB" >/dev/null 2>&1; then
  tc -s qdisc show dev "$IFB"
  echo "--- Classes on ${IFB} ---"
  tc class show dev "$IFB" || true
  echo "--- Filters on ${IFB} ---"
  tc filter show dev "$IFB" || true
else
  echo "(${IFB} not present — ingress shaping not active)"
fi
echo

echo "=== Summary ==="
EG="$(tc qdisc show dev "$IFACE")"
if echo "$EG" | grep -q 'htb'; then
  echo "Root: HTB multi-link shaping active on ${IFACE}"
  tc qdisc show dev "$IFACE" | grep 'netem' || echo "  (no netem leaves active on egress)"
elif echo "$EG" | grep -q 'netem'; then
  echo "Root: Legacy direct netem active on ${IFACE}"
else
  echo "Root: Unshaped (default) on ${IFACE}"
fi

if ip link show dev "$IFB" >/dev/null 2>&1; then
  ING="$(tc qdisc show dev "$IFB")"
  if echo "$ING" | grep -q 'netem'; then
    echo "Ingress: netem active on ${IFB}"
  else
    echo "Ingress: no netem leaves active on ${IFB}"
  fi
fi
