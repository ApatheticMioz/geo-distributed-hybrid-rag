#!/usr/bin/env bash
# =============================================================================
# apply.sh — Apply bidirectional WAN emulation (netem) on Node A.
#
# Topology:
#   Node A (10.8.0.1/24)
#     Link B: Node B (10.8.0.2, edge gateway: dense + fusion)
#     Link C: Node C (10.8.0.3, user-facing sparse tier)
#
# Contract:
#   Explicit link targeting via --link-b and/or --link-c is REQUIRED.
#   No positional arguments or implicit defaults are permitted.
#
# Architecture:
#   Egress ($IFACE)  : Root HTB (handle 1:) with:
#                      - class 1:10 -> u32 dst 10.8.0.2 -> netem leaf 10:
#                      - class 1:20 -> u32 dst 10.8.0.3 -> netem leaf 20:
#                      - class 1:30 -> default unshaped (SSH, Tailscale, LAN)
#   Ingress ($IFB)   : $IFACE ingress redirect -> ifb0 with:
#                      - class 1:10 -> u32 src 10.8.0.2 -> netem leaf 10:
#                      - class 1:20 -> u32 src 10.8.0.3 -> netem leaf 20:
#                      - class 1:30 -> default unshaped
#
# Syntax:
#   apply.sh --link-b <RTT_MS> <LOSS_PCT> [RATE]
#   apply.sh --link-c <RTT_MS> <LOSS_PCT> [RATE]
#   apply.sh --link-b <RTT_MS> <LOSS_PCT> [RATE] --link-c <RTT_MS> <LOSS_PCT> [RATE]
# =============================================================================
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "FATAL: apply.sh must run as root (CAP_NET_ADMIN required)." >&2
  exit 1
fi

command -v tc >/dev/null 2>&1 || { echo "FATAL: 'tc' not found in PATH." >&2; exit 1; }
command -v ip >/dev/null 2>&1 || { echo "FATAL: 'ip' not found in PATH." >&2; exit 1; }

PEER_B_IP="10.8.0.2"
PEER_C_IP="10.8.0.3"
IFB="ifb0"

# --- Resolve Interface (Strict; no fallbacks) ---------------------------------
if [ -n "${NETEM_IFACE:-}" ]; then
  IFACE="$NETEM_IFACE"
else
  # Discover interface owning 10.8.0.1/24
  IFACE="$(ip -4 -o addr show to 10.8.0.1/24 2>/dev/null | awk '{print $2}' | head -n1)"
fi

if [ -z "$IFACE" ]; then
  echo "FATAL: No interface assigned 10.8.0.1/24 found. WireGuard interface is not configured or down." >&2
  exit 1
fi

IF_STATE="$(ip -o link show dev "$IFACE" 2>/dev/null | grep -o 'state [A-Z]*' | awk '{print $2}' || true)"
if [ "$IF_STATE" = "DOWN" ]; then
  echo "FATAL: Interface '$IFACE' (10.8.0.1/24) is in state DOWN." >&2
  exit 1
fi

# --- CLI Validation -----------------------------------------------------------
usage() {
  echo "Usage:" >&2
  echo "  $0 --link-b <RTT_MS> <LOSS_PCT> [RATE]" >&2
  echo "  $0 --link-c <RTT_MS> <LOSS_PCT> [RATE]" >&2
  echo "  $0 --link-b <RTT_B> <LOSS_B> [RATE_B] --link-c <RTT_C> <LOSS_C> [RATE_C]" >&2
  echo "" >&2
  echo "Arguments:" >&2
  echo "  RTT_MS   Non-negative integer full round-trip delay (halved per direction)" >&2
  echo "  LOSS_PCT Integer 0..100 packet loss percentage (halved per direction)" >&2
  echo "  RATE     Optional token-bucket rate (e.g. 10mbit, 100mbit, 1gbit)" >&2
  exit 1
}

validate_tier() {
  local rtt="$1" loss="$2" rate="${3:-}"
  if [[ ! "$rtt" =~ ^[0-9]+$ ]]; then
    echo "FATAL: Invalid RTT_MS '$rtt'. Must be a non-negative integer." >&2
    exit 1
  fi
  if [[ ! "$loss" =~ ^[0-9]+$ ]] || [ "$loss" -gt 100 ]; then
    echo "FATAL: Invalid LOSS_PCT '$loss'. Must be an integer between 0 and 100." >&2
    exit 1
  fi
  if [ -n "$rate" ] && [[ ! "$rate" =~ ^[0-9]+(kbit|mbit|gbit)$ ]]; then
    echo "FATAL: Invalid RATE '$rate'. Must match format like 10mbit, 100mbit, 1gbit." >&2
    exit 1
  fi
}

RTT_B=""
LOSS_B=""
RATE_B=""
RTT_C=""
LOSS_C=""
RATE_C=""

[ $# -gt 0 ] || usage

while [ $# -gt 0 ]; do
  case "$1" in
    --link-b)
      [ $# -ge 3 ] || usage
      RTT_B="$2"
      LOSS_B="$3"
      shift 3
      if [ $# -gt 0 ] && [[ ! "$1" =~ ^-- ]]; then
        RATE_B="$1"
        shift 1
      fi
      validate_tier "$RTT_B" "$LOSS_B" "$RATE_B"
      ;;
    --link-c)
      [ $# -ge 3 ] || usage
      RTT_C="$2"
      LOSS_C="$3"
      shift 3
      if [ $# -gt 0 ] && [[ ! "$1" =~ ^-- ]]; then
        RATE_C="$1"
        shift 1
      fi
      validate_tier "$RTT_C" "$LOSS_C" "$RATE_C"
      ;;
    *)
      echo "FATAL: Unrecognized option '$1'. Positional and ambiguous arguments are forbidden." >&2
      usage
      ;;
  esac
done

if [ -z "$RTT_B" ] && [ -z "$RTT_C" ]; then
  echo "FATAL: Must specify at least one link target (--link-b or --link-c)." >&2
  exit 1
fi

# --- Teardown existing qdisc state ---------------------------------------------
tc qdisc del dev "$IFACE" root 2>/dev/null || true
tc qdisc del dev "$IFACE" ingress 2>/dev/null || true
tc qdisc del dev "$IFB" root 2>/dev/null || true
ip link del dev "$IFB" 2>/dev/null || true

# --- Setup IFB device for ingress mirror ---------------------------------------
modprobe ifb || { echo "FATAL: 'modprobe ifb' failed." >&2; exit 1; }
ip link add dev "$IFB" type ifb
ip link set dev "$IFB" up

# --- Setup Egress HTB on IFACE -------------------------------------------------
tc qdisc add dev "$IFACE" root handle 1: htb default 30
tc class add dev "$IFACE" parent 1: classid 1:1 htb rate 1000mbit ceil 1000mbit
tc class add dev "$IFACE" parent 1:1 classid 1:10 htb rate 1000mbit ceil 1000mbit
tc class add dev "$IFACE" parent 1:1 classid 1:20 htb rate 1000mbit ceil 1000mbit
tc class add dev "$IFACE" parent 1:1 classid 1:30 htb rate 1000mbit ceil 1000mbit

tc filter add dev "$IFACE" protocol ip parent 1: prio 1 u32 match ip dst "$PEER_B_IP" flowid 1:10
tc filter add dev "$IFACE" protocol ip parent 1: prio 1 u32 match ip dst "$PEER_C_IP" flowid 1:20

# --- Setup Ingress Mirror & Ingress HTB on IFB ---------------------------------
tc qdisc add dev "$IFACE" ingress
tc filter add dev "$IFACE" parent ffff: protocol ip u32 match u32 0 0 flowid 1:1 action mirred egress redirect dev "$IFB"

tc qdisc add dev "$IFB" root handle 1: htb default 30
tc class add dev "$IFB" parent 1: classid 1:1 htb rate 1000mbit ceil 1000mbit
tc class add dev "$IFB" parent 1:1 classid 1:10 htb rate 1000mbit ceil 1000mbit
tc class add dev "$IFB" parent 1:1 classid 1:20 htb rate 1000mbit ceil 1000mbit
tc class add dev "$IFB" parent 1:1 classid 1:30 htb rate 1000mbit ceil 1000mbit

tc filter add dev "$IFB" protocol ip parent 1: prio 1 u32 match ip src "$PEER_B_IP" flowid 1:10
tc filter add dev "$IFB" protocol ip parent 1: prio 1 u32 match ip src "$PEER_C_IP" flowid 1:20

# --- Helper: Apply Netem Leaf --------------------------------------------------
apply_leaf() {
  local dev="$1" parent="$2" handle="$3" rtt="$4" loss="$5" rate="$6"
  local half_delay=$(( rtt / 2 ))
  local half_loss=$(( loss / 2 ))
  local args=()

  [ "$half_delay" -gt 0 ] && args+=(delay "${half_delay}ms")
  [ "$half_loss" -gt 0 ] && args+=(loss "${half_loss}%")
  [ -n "$rate" ] && args+=(rate "$rate")

  if [ ${#args[@]} -gt 0 ]; then
    tc qdisc add dev "$dev" parent "$parent" handle "$handle" netem "${args[@]}"
  fi
}

# --- Apply Configured Links ----------------------------------------------------
if [ -n "$RTT_B" ]; then
  apply_leaf "$IFACE" "1:10" "10:" "$RTT_B" "$LOSS_B" "$RATE_B"
  apply_leaf "$IFB"   "1:10" "10:" "$RTT_B" "$LOSS_B" "$RATE_B"
  echo "Link B (10.8.0.2) shaped: RTT=${RTT_B}ms (half-delay=$((RTT_B/2))ms) loss=${LOSS_B}%${RATE_B:+ rate=${RATE_B}}"
fi

if [ -n "$RTT_C" ]; then
  apply_leaf "$IFACE" "1:20" "20:" "$RTT_C" "$LOSS_C" "$RATE_C"
  apply_leaf "$IFB"   "1:20" "20:" "$RTT_C" "$LOSS_C" "$RATE_C"
  echo "Link C (10.8.0.3) shaped: RTT=${RTT_C}ms (half-delay=$((RTT_C/2))ms) loss=${LOSS_C}%${RATE_C:+ rate=${RATE_C}}"
fi

echo "STATUS: Per-link shaping active on interface '$IFACE' (ingress -> '$IFB')."
