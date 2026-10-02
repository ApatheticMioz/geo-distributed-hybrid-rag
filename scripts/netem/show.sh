#!/usr/bin/env bash
# =============================================================================
# show.sh — Inspect active WAN emulation and HTB shaping state on Node A.
# =============================================================================
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "FATAL: show.sh must run as root (CAP_NET_ADMIN required)." >&2
  exit 1
fi

command -v tc >/dev/null 2>&1 || { echo "FATAL: 'tc' not found." >&2; exit 1; }
command -v ip >/dev/null 2>&1 || { echo "FATAL: 'ip' not found." >&2; exit 1; }

if [ -n "${NETEM_IFACE:-}" ]; then
  IFACE="$NETEM_IFACE"
else
  IFACE="$(ip -4 -o addr show to 10.8.0.1/24 2>/dev/null | awk '{print $2}' | head -n1)"
fi

if [ -z "$IFACE" ]; then
  echo "FATAL: WireGuard interface (10.8.0.1/24) not found." >&2
  exit 1
fi

IFB="ifb0"

echo "=== Interface: ${IFACE} (Ingress Mirror: ${IFB}) ==="
echo "--- Egress Qdiscs on ${IFACE} ---"
tc -s qdisc show dev "$IFACE"
echo "--- Egress Classes on ${IFACE} ---"
tc class show dev "$IFACE" || true
echo "--- Egress Filters on ${IFACE} ---"
tc filter show dev "$IFACE" || true
echo ""

if ip link show dev "$IFB" >/dev/null 2>&1; then
  echo "--- Ingress Qdiscs on ${IFB} ---"
  tc -s qdisc show dev "$IFB"
  echo "--- Ingress Classes on ${IFB} ---"
  tc class show dev "$IFB" || true
  echo "--- Ingress Filters on ${IFB} ---"
  tc filter show dev "$IFB" || true
else
  echo "--- Ingress Mirror (${IFB}) Not Active ---"
fi
