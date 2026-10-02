#!/usr/bin/env bash
# =============================================================================
# apply.sh — Apply bidirectional WAN emulation (netem) on node_A.
#
# Topology:
#   node_A (10.8.0.1/24)
#     <-> peer B (10.8.0.2, laptop gateway: dense + fusion)
#     <-> peer C (10.8.0.3, user-facing sparse tier)
#
# Semantics (TWO-WAY / BIDIRECTIONAL shaping — half-per-direction contract):
#   RTT_MS is the FULL round-trip target. It is split in half per direction:
#     - EGRESS  (packets leaving node_A towards target peer)
#     - INGRESS (packets arriving at node_A from target peer)
#   Integer division: 15ms -> 7ms per direction (14ms total); 0 stays 0.
#   RATE, when given, is applied at FULL value per direction (not halved).
#
# Multi-Peer / Independent Leg Shaping (Issue #3):
#   Uses an HTB qdisc with u32 IP filters to isolate peers:
#     - Class 1:10 (handle 10: netem) -> Link B (10.8.0.2)
#     - Class 1:20 (handle 20: netem) -> Link C (10.8.0.3)
#     - Class 1:30 (handle 30: default) -> Unshaped (SSH control, Tailscale, LAN)
#
# Usage:
#   apply.sh RTT_MS LOSS_PCT [RATE]                  # Default: shapes peer B (10.8.0.2)
#   apply.sh --peer <10.8.0.2|10.8.0.3|b|c|all> RTT_MS LOSS_PCT [RATE]
#   apply.sh --link-b RTT_B LOSS_B [RATE_B] [--link-c RTT_C LOSS_C [RATE_C]]
#
# Permissions:
#   Requires CAP_NET_ADMIN. Run as root via:
#     wsl.exe -u root -e bash /home/apath/Work/PDC/Project/scripts/netem/apply.sh 15 5
# =============================================================================
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "ERROR: must be run as root (CAP_NET_ADMIN required). Use: wsl.exe -u root -e bash $0 ..." >&2; exit 1; }

command -v tc >/dev/null 2>&1 || { echo "ERROR: 'tc' not found (install iproute2)" >&2; exit 1; }

PEER_B_IP="10.8.0.2"
PEER_C_IP="10.8.0.3"

# --- Detect active interface for 10.8.0.1 ---------------------------------------
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

usage() {
  echo "Usage:"
  echo "  apply.sh RTT_MS LOSS_PCT [RATE]                       # Shapes Link B (10.8.0.2)"
  echo "  apply.sh --peer <b|c|all|IP> RTT_MS LOSS_PCT [RATE]   # Shapes specified peer"
  echo "  apply.sh --link-b RTT LOSS [RATE] --link-c RTT LOSS [RATE] # Shapes both links"
  exit "${1:-1}"
}

# --- Parse Arguments -----------------------------------------------------------
RTT_B=""
LOSS_B=""
RATE_B=""
RTT_C=""
LOSS_C=""
RATE_C=""

validate_tier() {
  local rtt="$1" loss="$2" rate="${3:-}"
  [[ "$rtt" =~ ^[0-9]+$ ]] || { echo "ERROR: RTT_MS must be a non-negative integer, got '$rtt'" >&2; exit 1; }
  [[ "$loss" =~ ^[0-9]+$ ]] || { echo "ERROR: LOSS_PCT must be an integer 0..100, got '$loss'" >&2; exit 1; }
  [ "$loss" -le 100 ] || { echo "ERROR: LOSS_PCT must be <= 100, got '$loss'" >&2; exit 1; }
  if [ -n "$rate" ]; then
    [[ "$rate" =~ ^[0-9]+(kbit|mbit|gbit|bit)$ ]] || { echo "ERROR: RATE must be like 10mbit/100kbit, got '$rate'" >&2; exit 1; }
  fi
}

if [ $# -eq 0 ]; then
  usage 1
elif [ "$1" = "--link-b" ] || [ "$1" = "--link-c" ]; then
  while [ $# -gt 0 ]; do
    case "$1" in
      --link-b)
        [ $# -ge 3 ] || usage 1
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
        [ $# -ge 3 ] || usage 1
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
        usage 1
        ;;
    esac
  done
elif [ "$1" = "--peer" ]; then
  [ $# -ge 4 ] || usage 1
  TARGET_PEER="$2"
  RTT="$3"
  LOSS="$4"
  RATE="${5:-}"
  validate_tier "$RTT" "$LOSS" "$RATE"
  case "$TARGET_PEER" in
    b|B|node_b|10.8.0.2)
      RTT_B="$RTT"; LOSS_B="$LOSS"; RATE_B="$RATE"
      ;;
    c|C|node_c|10.8.0.3)
      RTT_C="$RTT"; LOSS_C="$LOSS"; RATE_C="$RATE"
      ;;
    all|ALL)
      RTT_B="$RTT"; LOSS_B="$LOSS"; RATE_B="$RATE"
      RTT_C="$RTT"; LOSS_C="$LOSS"; RATE_C="$RATE"
      ;;
    *)
      echo "ERROR: Unknown peer '$TARGET_PEER'. Choose b, c, all, 10.8.0.2, or 10.8.0.3" >&2
      exit 1
      ;;
  esac
else
  # Positional backward-compatible mode: applies to Node B (10.8.0.2)
  [ $# -ge 2 ] || usage 1
  RTT_B="$1"
  LOSS_B="$2"
  RATE_B="${3:-}"
  validate_tier "$RTT_B" "$LOSS_B" "$RATE_B"
fi

echo "=== Target Interface: ${IFACE} (ingress mirror -> ${IFB}) ==="

# --- Clear existing state before applying fresh HTB structure ------------------
tc qdisc del dev "$IFACE" root 2>/dev/null || true
tc qdisc del dev "$IFACE" ingress 2>/dev/null || true
tc qdisc del dev "$IFB" root 2>/dev/null || true
ip link del dev "$IFB" 2>/dev/null || true

# --- Setup ifb device for ingress -----------------------------------------------
modprobe ifb || { echo "ERROR: 'modprobe ifb' failed" >&2; exit 1; }
ip link add dev "$IFB" type ifb 2>/dev/null || true
ip link set dev "$IFB" up

# --- EGRESS: Root HTB qdisc on IFACE --------------------------------------------
echo "Applying EGRESS root HTB on ${IFACE} (default unshaped class 1:30)"
tc qdisc add dev "$IFACE" root handle 1: htb default 30
tc class add dev "$IFACE" parent 1: classid 1:1 htb rate 1000mbit ceil 1000mbit
tc class add dev "$IFACE" parent 1:1 classid 1:10 htb rate 1000mbit ceil 1000mbit
tc class add dev "$IFACE" parent 1:1 classid 1:20 htb rate 1000mbit ceil 1000mbit
tc class add dev "$IFACE" parent 1:1 classid 1:30 htb rate 1000mbit ceil 1000mbit

# Attach egress filters
tc filter add dev "$IFACE" protocol ip parent 1: prio 1 u32 match ip dst "$PEER_B_IP" flowid 1:10
tc filter add dev "$IFACE" protocol ip parent 1: prio 1 u32 match ip dst "$PEER_C_IP" flowid 1:20

# --- INGRESS: Mirror IFACE ingress to IFB ---------------------------------------
echo "Applying INGRESS redirect: ${IFACE} -> ${IFB}"
tc qdisc add dev "$IFACE" ingress
tc filter add dev "$IFACE" parent ffff: protocol ip u32 match u32 0 0 flowid 1:1 action mirred egress redirect dev "$IFB"

# --- INGRESS: Root HTB qdisc on IFB ---------------------------------------------
tc qdisc add dev "$IFB" root handle 1: htb default 30
tc class add dev "$IFB" parent 1: classid 1:1 htb rate 1000mbit ceil 1000mbit
tc class add dev "$IFB" parent 1:1 classid 1:10 htb rate 1000mbit ceil 1000mbit
tc class add dev "$IFB" parent 1:1 classid 1:20 htb rate 1000mbit ceil 1000mbit
tc class add dev "$IFB" parent 1:1 classid 1:30 htb rate 1000mbit ceil 1000mbit

# Attach ingress filters (match ip src)
tc filter add dev "$IFB" protocol ip parent 1: prio 1 u32 match ip src "$PEER_B_IP" flowid 1:10
tc filter add dev "$IFB" protocol ip parent 1: prio 1 u32 match ip src "$PEER_C_IP" flowid 1:20

# --- Helper to apply leaf netem ------------------------------------------------
apply_leaf_netem() {
  local dev="$1" parent="$2" handle="$3" rtt="$4" loss="$5" rate="$6"
  local half_delay=$(( rtt / 2 ))
  local half_loss=$(( loss / 2 ))

  if [ "$half_delay" -gt 0 ] || [ "$half_loss" -gt 0 ] || [ -n "$rate" ]; then
    local args=()
    [ "$half_delay" -gt 0 ] && args+=(delay "${half_delay}ms")
    [ "$half_loss" -gt 0 ] && args+=(loss "${half_loss}%")
    [ -n "$rate" ] && args+=(rate "$rate")
    tc qdisc add dev "$dev" parent "$parent" handle "$handle" netem "${args[@]}"
  fi
}

# --- Apply Link B shaping (10.8.0.2) -------------------------------------------
if [ -n "$RTT_B" ]; then
  echo "Link B (10.8.0.2): full RTT=${RTT_B}ms loss=${LOSS_B}% (half-per-dir: delay=$((RTT_B/2))ms loss=$((LOSS_B/2))%${RATE_B:+ rate=$RATE_B})"
  apply_leaf_netem "$IFACE" "1:10" "10:" "$RTT_B" "$LOSS_B" "$RATE_B"
  apply_leaf_netem "$IFB"   "1:10" "10:" "$RTT_B" "$LOSS_B" "$RATE_B"
else
  echo "Link B (10.8.0.2): unshaped (default pass-through)"
fi

# --- Apply Link C shaping (10.8.0.3) -------------------------------------------
if [ -n "$RTT_C" ]; then
  echo "Link C (10.8.0.3): full RTT=${RTT_C}ms loss=${LOSS_C}% (half-per-dir: delay=$((RTT_C/2))ms loss=$((LOSS_C/2))%${RATE_C:+ rate=$RATE_C})"
  apply_leaf_netem "$IFACE" "1:20" "20:" "$RTT_C" "$LOSS_C" "$RATE_C"
  apply_leaf_netem "$IFB"   "1:20" "20:" "$RTT_C" "$LOSS_C" "$RATE_C"
else
  echo "Link C (10.8.0.3): unshaped (default pass-through)"
fi

echo "OK: Independent per-link WAN shaping active on ${IFACE} and ${IFB}."
