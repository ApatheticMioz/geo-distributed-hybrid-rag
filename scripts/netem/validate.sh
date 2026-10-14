#!/usr/bin/env bash
# =============================================================================
# validate.sh — Closed-loop validation of per-link bidirectional netem RTT.
#
# Semantics:
#   1. Clear all qdiscs.
#   2. Measure baseline median RTT over $PING_COUNT pings.
#   3. Apply shaping via apply.sh for the target link (with LOSS_PCT if given).
#   4. Measure shaped median RTT over $PING_COUNT pings.
#   5. Enforce gate: |shaped - baseline - RTT_MS| <= 30% of RTT_MS.
#   6. If LOSS_PCT is given, additionally verify the netem LOSS readback:
#      `tc qdisc show` must report exactly loss/2 per direction (egress + ifb
#      ingress) within a 0.05pp tolerance.
#   7. Trap cleanup clears all netem state on exit.
#
# Syntax:
#   validate.sh --link-b <RTT_MS> [LOSS_PCT]
#   validate.sh --link-c <RTT_MS> [LOSS_PCT]
#   validate.sh --link-b <RTT_B> [LOSS_B] --link-c <RTT_C> [LOSS_C]
# =============================================================================
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "FATAL: validate.sh must run as root (CAP_NET_ADMIN required)." >&2
  exit 1
fi

command -v tc >/dev/null 2>&1 || { echo "FATAL: 'tc' not found." >&2; exit 1; }
command -v ping >/dev/null 2>&1 || { echo "FATAL: 'ping' not found." >&2; exit 1; }
command -v ip >/dev/null 2>&1 || { echo "FATAL: 'ip' not found." >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# 11 samples keeps the median odd-valued (no interpolation in median()) and
# tightens the median vs the 5-ping variance noted in the 2026-10-12 A-B
# validation (weakest tier: 15 ms target, 5-ping median high-variance).
PING_COUNT=11

usage() {
  echo "Usage:" >&2
  echo "  $0 --link-b <RTT_MS> [LOSS_PCT]" >&2
  echo "  $0 --link-c <RTT_MS> [LOSS_PCT]" >&2
  echo "  $0 --link-b <RTT_B> [LOSS_B] --link-c <RTT_C> [LOSS_C]" >&2
  exit 1
}

RTT_B=""
LOSS_B=""
RTT_C=""
LOSS_C=""

[ $# -gt 0 ] || usage

while [ $# -gt 0 ]; do
  case "$1" in
    --link-b)
      [ $# -ge 2 ] || usage
      RTT_B="$2"
      shift 2
      if [ $# -gt 0 ] && [[ ! "$1" =~ ^-- ]]; then
        LOSS_B="$1"
        shift 1
      fi
      ;;
    --link-c)
      [ $# -ge 2 ] || usage
      RTT_C="$2"
      shift 2
      if [ $# -gt 0 ] && [[ ! "$1" =~ ^-- ]]; then
        LOSS_C="$1"
        shift 1
      fi
      ;;
    *)
      echo "FATAL: Unrecognized argument '$1'. Ambiguous positional arguments are forbidden." >&2
      usage
      ;;
  esac
done

validate_rtt_value() {
  local val="$1" label="$2"
  if [[ ! "$val" =~ ^[0-9]+$ ]] || [ "$val" -lt 1 ]; then
    echo "FATAL: Target RTT for $label must be a positive integer >= 1 ms, got '$val'." >&2
    exit 1
  fi
}

validate_loss_value() {
  local val="$1" label="$2"
  if [[ ! "$val" =~ ^[0-9]+([.][0-9])?$ ]] || awk -v x="$val" 'BEGIN { exit !(x > 100) }'; then
    echo "FATAL: Loss for $label must be between 0 and 100 with at most one decimal place, got '$val'." >&2
    exit 1
  fi
}

[ -n "$RTT_B" ] && validate_rtt_value "$RTT_B" "Link B"
[ -n "$LOSS_B" ] && validate_loss_value "$LOSS_B" "Link B"
[ -n "$RTT_C" ] && validate_rtt_value "$RTT_C" "Link C"
[ -n "$LOSS_C" ] && validate_loss_value "$LOSS_C" "Link C"

# Cleanup on exit
cleanup() {
  bash "$SCRIPT_DIR/clear.sh" >/dev/null 2>&1 || true
}
trap cleanup EXIT

median() {
  local values="$1"
  local n
  n="$(echo "$values" | grep -c . || true)"
  [ "$n" -ge 1 ] || { echo "FATAL: No ping samples captured." >&2; return 1; }
  local mid=$(( (n + 1) / 2 ))
  if [ $(( n % 2 )) -eq 1 ]; then
    echo "$values" | sort -n | sed -n "${mid}p"
  else
    local a b
    a="$(echo "$values" | sort -n | sed -n "${mid}p")"
    b="$(echo "$values" | sort -n | sed -n "$(( mid + 1 ))p")"
    awk -v a="$a" -v b="$b" 'BEGIN { printf "%.3f", (a + b) / 2 }'
  fi
}

ping_samples() {
  local target="$1"
  local i out
  for i in $(seq 1 "$PING_COUNT"); do
    out="$(ping -c 1 -W 2 "$target" 2>/dev/null | grep -oE 'time=[0-9]+(\.[0-9]+)?' | cut -d= -f2 || true)"
    if [ -n "$out" ]; then
      echo "$out"
    fi
  done
}

# Extract the netem loss percentage from a qdisc-show output line, or empty if
# the qdisc has no `loss` param. Handles netem's rendered forms, e.g.
# "delay 7.5ms loss 0.5%" -> "0.5".
parse_netem_loss() {
  local line="$1"
  echo "$line" | grep -oE 'loss [0-9]+(\.[0-9]+)?%' | head -n1 | awk '{print $2}' | tr -d '%' || true
}

# Verify the applied netem loss readback matches the target loss (halved per
# direction): parse `tc qdisc show` for the iface's netem leaf (egress) and the
# ifb leaf (ingress); each must equal loss/2 within 0.05pp. Runs while shaping
# is active (before the trap cleanup fires).
validate_loss_readback() {
  local iface="$1" ifb="$2" peer_src="$3" loss="$4"

  local expected
  expected="$(awk -v x="$loss" 'BEGIN { h = x / 2; if (h == 0) print "0"; else if (h == int(h)) printf "%d", h; else printf "%.2f", h }')"

  local egress_line ifb_line
  # NOTE: `tc qdisc show` prints only the qdisc tree — it does NOT emit the
  # `tc filter` lines, so the peer IP that select() the class is never in this
  # output.  `grep "$peer_src"` therefore normally matches nothing and, under
  # this script's `set -euo pipefail`, the resulting non-zero pipeline status
  # would abort the whole script on the FIRST capture line, before the
  # fallback below could run — which is exactly why a fractional-loss readback
  # came back as `loss_gate=None` (the "Loss gate:" line was never printed).
  # `|| true` makes each capture tolerate a no-match; the explicit `[ -z ]` /
  # `[ -n ]` checks below do the real validation (and emit a proper FATAL if
  # no netem leaf exists at all).
  egress_line="$(tc qdisc show dev "$iface" | grep 'netem' | grep 'flowid\|parent' | head -n1 || true)"
  ifb_line="$(tc qdisc show dev "$ifb" 2>/dev/null | grep "netem" | grep "$peer_src" | head -n1 || true)"
  # Fallback: ifb leaf is untagged by peer in qdisc-show; take its single netem leaf.
  [ -n "$ifb_line" ] || ifb_line="$(tc qdisc show dev "$ifb" 2>/dev/null | grep 'netem' | head -n1 || true)"

  if [ -z "$egress_line" ]; then
    echo "FATAL: No netem qdisc found on egress ($iface) — cannot read back loss." >&2
    return 1
  fi
  if [ -z "$ifb_line" ]; then
    echo "FATAL: No netem qdisc found on ingress ($ifb) — cannot read back loss." >&2
    return 1
  fi

  local egress_loss ifb_loss
  egress_loss="$(parse_netem_loss "$egress_line")"
  ifb_loss="$(parse_netem_loss "$ifb_line")"

  if [ -z "$egress_loss" ]; then
    if awk -v x="$expected" 'BEGIN { exit !(x > 0) }'; then
      echo "FATAL: Egress netem qdisc has no 'loss' parameter but target loss is ${loss}%: ${egress_line}" >&2
      return 1
    fi
    egress_loss="0"
  fi
  if [ -z "$ifb_loss" ]; then
    if awk -v x="$expected" 'BEGIN { exit !(x > 0) }'; then
      echo "FATAL: Ingress (ifb) netem qdisc has no 'loss' parameter but target loss is ${loss}%: ${ifb_line}" >&2
      return 1
    fi
    ifb_loss="0"
  fi

  echo "Loss readback       : egress=${egress_loss}%  ingress(ifb)=${ifb_loss}%  (expected ${expected}%/dir for ${loss}% total)"

  local e_ok i_ok
  e_ok="$(awk -v g="$egress_loss" -v e="$expected" 'BEGIN { d = g - e; if (d < 0) d = -d; print (d <= 0.05) ? 1 : 0 }')"
  i_ok="$(awk -v g="$ifb_loss" -v e="$expected" 'BEGIN { d = g - e; if (d < 0) d = -d; print (d <= 0.05) ? 1 : 0 }')"

  if [ "$e_ok" -eq 1 ] && [ "$i_ok" -eq 1 ]; then
    echo "Loss gate: PASS (|observed - ${expected}%| <= 0.05pp per direction)"
  else
    echo "Loss gate: FAIL (expected ${expected}%/dir; observed egress=${egress_loss}%, ingress=${ifb_loss}%; tolerance 0.05pp)" >&2
    return 1
  fi
}

validate_target_link() {
  local label="$1" ip="$2" target_rtt="$3" target_loss="${4:-0}"

  echo "========================================================================"
  echo " VALIDATION: Link ${label} (${ip}) — Target Added RTT: ${target_rtt} ms, Loss: ${target_loss}%"
  echo "========================================================================"

  bash "$SCRIPT_DIR/clear.sh" >/dev/null

  # 1. Baseline
  local base_samples
  base_samples="$(ping_samples "$ip")"
  local count
  count="$(echo "$base_samples" | grep -c . || true)"
  if [ "$count" -lt 1 ]; then
    echo "FATAL: Ping baseline failed. Peer ${ip} is unreachable." >&2
    return 1
  fi
  local base_med
  base_med="$(median "$base_samples")"
  echo "Baseline median RTT : ${base_med} ms"

  # 2. Apply Shaping
  if [ "$label" = "B" ]; then
    bash "$SCRIPT_DIR/apply.sh" --link-b "$target_rtt" "$target_loss" >/dev/null
  else
    bash "$SCRIPT_DIR/apply.sh" --link-c "$target_rtt" "$target_loss" >/dev/null
  fi

  # 3. Shaped Measurement
  local shaped_samples
  shaped_samples="$(ping_samples "$ip")"
  count="$(echo "$shaped_samples" | grep -c . || true)"
  if [ "$count" -lt 1 ]; then
    echo "FATAL: Shaped ping measurement failed for ${ip}." >&2
    return 1
  fi
  local shaped_med
  shaped_med="$(median "$shaped_samples")"
  echo "Shaped median RTT   : ${shaped_med} ms"

  # 4. Enforce ±30% Gate
  local diff tol lower upper pass
  diff="$(awk -v s="$shaped_med" -v b="$base_med" 'BEGIN { printf "%.3f", s - b }')"
  tol="$(awk -v t="$target_rtt" 'BEGIN { printf "%.3f", 0.30 * t }')"
  lower="$(awk -v t="$target_rtt" -v tol="$tol" 'BEGIN { printf "%.3f", t - tol }')"
  upper="$(awk -v t="$target_rtt" -v tol="$tol" 'BEGIN { printf "%.3f", t + tol }')"
  pass="$(awk -v d="$diff" -v l="$lower" -v u="$upper" 'BEGIN { print (d >= l && d <= u) ? 1 : 0 }')"

  echo "Added RTT (Observed): ${diff} ms (Acceptable Gate [${lower}, ${upper}] ms)"

  if [ "$pass" -eq 1 ]; then
    echo "Status: PASS"
  else
    echo "Status: FAIL (Observed RTT ${diff} ms is outside ±30% acceptance window)"
    return 1
  fi

  # 5. Enforce Loss Readback Gate (only if loss was requested)
  if [ "$target_loss" != "0" ]; then
    local iface ifb peer_src
    if [ -n "${NETEM_IFACE:-}" ]; then
      iface="$NETEM_IFACE"
    else
      iface="$(ip -4 -o addr show to 10.8.0.1/24 2>/dev/null | awk '{print $2}' | head -n1)"
    fi
    ifb="ifb0"
    if [ "$label" = "B" ]; then
      peer_src="10.8.0.2"
    else
      peer_src="10.8.0.3"
    fi
    validate_loss_readback "$iface" "$ifb" "$peer_src" "$target_loss"
  fi
}

[ -n "$RTT_B" ] && validate_target_link "B" "10.8.0.2" "$RTT_B" "$LOSS_B"
[ -n "$RTT_C" ] && validate_target_link "C" "10.8.0.3" "$RTT_C" "$LOSS_C"

echo "ALL VALIDATIONS PASSED."
