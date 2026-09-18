#!/usr/bin/env bash
# =============================================================================
# apply.sh — Apply bidirectional WAN emulation (netem) on node_A.
#
# Topology:
#   node_A (this WSL2 VM, iface eth4 = 10.8.0.1/24)  <->  peer 10.8.0.2 (laptop)
#   WSL2 mirrored networking: eth4 is the WireGuard-path interface.
#
# Semantics (TWO-WAY / BIDIRECTIONAL shaping — half-per-direction contract):
#   RTT_MS is the FULL round-trip target. It is split in half per direction:
#     - EGRESS  (packets leaving node_A):  eth4 root qdisc
#         netem delay (RTT_MS/2)ms loss (LOSS_PCT/2)% [rate RATE]
#     - INGRESS (packets arriving at node_A): ifb0 root qdisc, fed by the
#         eth4 ingress qdisc + a u32 filter mirred-redirecting all IP
#         traffic to ifb0:
#         netem delay (RTT_MS/2)ms loss (LOSS_PCT/2)% [rate RATE]
#   Integer division: 15ms -> 7ms per direction (14ms total); 0 stays 0.
#   RATE, when given, is applied at FULL value per direction (not halved).
#   A ping RTT therefore increases by ~RTT_MS (both halves sum).
#
# Idempotent: all qdiscs/filters are installed with 'replace'; ifb0 is
#   created only if absent.
#
# Usage:
#   apply.sh RTT_MS LOSS_PCT [RATE]
#     RTT_MS   full round-trip added delay in milliseconds (integer)
#     LOSS_PCT full round-trip packet loss percentage (integer 0..100)
#     RATE     optional token-bucket rate per direction, e.g. 10mbit, 100kbit
#
# Example:
#   apply.sh 15 5             # 7ms delay + 2% loss per direction
#   apply.sh 15 5 10mbit      # ... plus 10 Mbit/s cap per direction
#
# Permissions:
#   tc qdisc replace / ip link / modprobe require CAP_NET_ADMIN. This script
#   contains NO sudo; run it as root via:
#     wsl.exe -u root -e bash /home/apath/Work/PDC/Project/scripts/netem/apply.sh 15 5
# =============================================================================
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "ERROR: must be run as root (CAP_NET_ADMIN required for tc/ip/modprobe). Use: wsl.exe -u root -e bash $0 ..." >&2; exit 1; }

IFACE="eth4"
IFB="ifb0"

usage() {
  sed -n '2,38p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-1}"
}

# --- argument validation -------------------------------------------------------
[ $# -ge 2 ] || usage 1

RTT_MS="$1"
LOSS_PCT="$2"
RATE="${3:-}"

# RTT_MS: non-negative integer (full round-trip target)
[[ "$RTT_MS" =~ ^[0-9]+$ ]] || { echo "ERROR: RTT_MS must be a non-negative integer, got '$RTT_MS'" >&2; usage 1; }
# LOSS_PCT: integer 0..100 (full round-trip target)
[[ "$LOSS_PCT" =~ ^[0-9]+$ ]] || { echo "ERROR: LOSS_PCT must be an integer, got '$LOSS_PCT'" >&2; usage 1; }
[ "$LOSS_PCT" -le 100 ] || { echo "ERROR: LOSS_PCT must be <= 100, got '$LOSS_PCT'" >&2; usage 1; }
# RATE: optional, must look like a tc rate (e.g. 10mbit, 100kbit, 1000000bit)
if [ -n "$RATE" ]; then
  [[ "$RATE" =~ ^[0-9]+(kbit|mbit|gbit|bit)$ ]] || { echo "ERROR: RATE must be like 10mbit/100kbit, got '$RATE'" >&2; usage 1; }
fi

command -v tc >/dev/null 2>&1 || { echo "ERROR: 'tc' not found (install iproute2)" >&2; exit 1; }

# --- half-per-direction values --------------------------------------------------
HALF_DELAY=$(( RTT_MS / 2 ))
HALF_LOSS=$(( LOSS_PCT / 2 ))

# --- EGRESS: root qdisc on eth4 -------------------------------------------------
if [ -n "$RATE" ]; then
  echo "Applying EGRESS: tc qdisc replace dev ${IFACE} root netem delay ${HALF_DELAY}ms loss ${HALF_LOSS}% rate ${RATE}"
  tc qdisc replace dev "$IFACE" root netem delay "${HALF_DELAY}"ms loss "${HALF_LOSS}"% rate "$RATE"
else
  echo "Applying EGRESS: tc qdisc replace dev ${IFACE} root netem delay ${HALF_DELAY}ms loss ${HALF_LOSS}%"
  tc qdisc replace dev "$IFACE" root netem delay "${HALF_DELAY}"ms loss "${HALF_LOSS}"%
fi

# --- INGRESS: redirect eth4 ingress -> ifb0, shape on ifb0 root -----------------
modprobe ifb || { echo "ERROR: 'modprobe ifb' failed (ifb module unavailable in this kernel?)" >&2; exit 1; }
ip link show dev "$IFB" >/dev/null 2>&1 || ip link add dev "$IFB" type ifb
ip link set dev "$IFB" up

echo "Applying INGRESS: tc qdisc replace dev ${IFACE} ingress"
tc qdisc replace dev "$IFACE" ingress
echo "Applying INGRESS: tc filter replace dev ${IFACE} parent ffff: protocol ip u32 match u32 0 0 flowid 1:1 action mirred egress redirect dev ${IFB}"
tc filter replace dev "$IFACE" parent ffff: protocol ip u32 match u32 0 0 flowid 1:1 action mirred egress redirect dev "$IFB"

if [ -n "$RATE" ]; then
  echo "Applying INGRESS: tc qdisc replace dev ${IFB} root netem delay ${HALF_DELAY}ms loss ${HALF_LOSS}% rate ${RATE}"
  tc qdisc replace dev "$IFB" root netem delay "${HALF_DELAY}"ms loss "${HALF_LOSS}"% rate "$RATE"
else
  echo "Applying INGRESS: tc qdisc replace dev ${IFB} root netem delay ${HALF_DELAY}ms loss ${HALF_LOSS}%"
  tc qdisc replace dev "$IFB" root netem delay "${HALF_DELAY}"ms loss "${HALF_LOSS}"%
fi

# --- verify: echo what is active -------------------------------------------------
echo "--- tc qdisc show dev ${IFACE} ---"
tc qdisc show dev "$IFACE"
echo "--- tc qdisc show dev ${IFB} ---"
tc qdisc show dev "$IFB"

EGRESS_NETEM="$(tc qdisc show dev "$IFACE" | grep -c 'netem' || true)"
[ "$EGRESS_NETEM" -ge 1 ] || { echo "ERROR: egress netem not active on ${IFACE} after apply" >&2; exit 1; }
INGRESS_NETEM="$(tc qdisc show dev "$IFB" | grep -c 'netem' || true)"
[ "$INGRESS_NETEM" -ge 1 ] || { echo "ERROR: ingress netem not active on ${IFB} after apply" >&2; exit 1; }

echo "OK: bidirectional shaping active (half-per-direction: delay=${HALF_DELAY}ms loss=${HALF_LOSS}% per direction${RATE:+ rate=${RATE}/dir})."
echo "    Full RTT target: ${RTT_MS}ms (2 x ${HALF_DELAY}ms) / loss ${LOSS_PCT}% (2 x ${HALF_LOSS}%)."
echo "    Remove with: clear.sh"
