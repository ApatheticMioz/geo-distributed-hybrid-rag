#!/usr/bin/env bash
# =============================================================================
# clear.sh — Remove all WAN emulation and HTB shaping state on Node A.
#
# Semantics:
#   Deletes ingress mirror, ifb0 interface, and root qdiscs.
#   Asserts that no netem or htb qdisc remains active.
# =============================================================================
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "FATAL: clear.sh must run as root (CAP_NET_ADMIN required)." >&2
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

# Teardown ingress and egress
tc qdisc del dev "$IFB" root 2>/dev/null || true
tc qdisc del dev "$IFACE" ingress 2>/dev/null || true
tc qdisc del dev "$IFACE" root 2>/dev/null || true
ip link del dev "$IFB" 2>/dev/null || true
modprobe -r ifb 2>/dev/null || true

OUT="$(tc qdisc show dev "$IFACE")"
if echo "$OUT" | grep -qE 'netem|htb'; then
  echo "FATAL: Netem or HTB qdisc still present on '$IFACE' after teardown:" >&2
  echo "$OUT" >&2
  exit 1
fi

echo "STATUS: Interface '$IFACE' completely cleared. All shaping removed."
