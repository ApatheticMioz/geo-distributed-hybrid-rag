# Netem Fractional Loss Tiers — Validation Evidence (AB-4 pre-flight)

**Date:** 2026-10-14 (UTC) · **Scripts:** `scripts/netem/apply.sh`, `validate.sh` (fractional-loss
patch, commit "fix(netem): fractional loss tiers in apply/validate") · **Host:** Node A
(WSL2 kernel 6.18.33.2-microsoft-standard-WSL2), iface `eth4` (10.8.0.1/24),
privileged host-network Docker container (debian bookworm, iproute2, kmod,
`/lib/modules` bind-mounted, `options ifb numifbs=0`), exactly the documented
execution path from `validation_ab_20261012T145653Z.md`.

## Scope and method

The task's verification gate was split into two parts with different feasibility:

1. **Kernel-level shaping + loss readback** — `apply.sh --link-b 15 {0.5,1,2}`,
   `tc qdisc show` readback of the netem loss on both directions, `show.sh` with
   loss active, `clear.sh` → pristine. **Executed in full** (below).
2. **Closed-loop 11-ping ±30% RTT gate** (`validate.sh`'s ping measurement) —
   **NOT executable in this run**: both WireGuard peers `10.8.0.2` and
   `10.8.0.3` are currently unreachable (see "Barrier" below), so every ping
   window is 100% loss. The RTT gate is therefore recorded as *not executed*
   (same status class as the 2026-10-12 A→C gate, `validation_ac_...md`), not
   as failed. `validate.sh`'s **new loss-readback sub-gate** was instead
   exercised directly against the live kernel state via a harness that
   `eval`s the actual `parse_netem_loss()`/`validate_loss_readback()`
   functions out of the committed `validate.sh` (no logic copied by hand).

## Barrier (demonstrated, not assumed)

- `ping -c 3` to `10.8.0.2` and `10.8.0.3`: **100% loss** (from inside the
  privileged container — raw ICMP permitted there, so this is peer state, not
  sandbox caps).
- Full ICMP sweep of `10.8.0.0/24` from the container: only `10.8.0.1` alive.
- `ip neigh dev eth4`: both peers resolve to a **single** MAC
  `00:15:5d:20:9b:2c` (a shared NAT gateway) — consistent with the WG tunnel
  being down and 10.8.0.x being routed to a gateway that has no route back.
- TCP probes to both Tailscale IPs (`100.64.0.2`, `100.64.0.3`) on 22 and
  8000, and to the WG IPs on 22: all fail. Neither `wg_a` nor a tailscaled
  process is present in this session's WSL view.
- The 15 ms tier's ±30% window is `[10.5, 19.5] ms`; with 0/11 pings there is
  no shaped median to compute, so the gate cannot pass or fail — it is
  blocked upstream of the gate itself.

**To complete the full gate:** bring the WG tunnel up on Node A (per
`docs/RUNBOOK.md` hub-relay bring-up / `docs/NODE_ACCESS.md` §5) and Node B's
tunnel, then:

```
# privileged host-network container (iproute2 + iputils-ping + kmod,
# /lib/modules bind-mounted, /etc/modprobe.d/ifb.conf = "options ifb numifbs=0"):
bash scripts/netem/validate.sh --link-b 15 0.5   # RTT gate [10.5,19.5] + loss readback 0.25%/dir
bash scripts/netem/validate.sh --link-b 15 1     # ... + 0.5%/dir
bash scripts/netem/validate.sh --link-b 15 2     # ... + 1%/dir
bash scripts/netem/clear.sh                      # final clear + assert
```

Max 2 attempts per tier (policy from the A→B gate).

## Results — loss readback per direction (kernel ground truth)

| Tier (total loss) | Expected /dir | Egress readback (`tc qdisc show dev eth4`, netem 10: parent 1:10) | Ingress readback (`tc qdisc show dev ifb0`, netem 10: parent 1:10) | Readback gate (±0.05pp) |
|---|---|---|---|---|
| `0.5` | 0.25% | `qdisc netem 10: parent 1:10 limit 1000 delay 7ms loss 0.25%` | `qdisc netem 10: parent 1:10 limit 1000 delay 7ms loss 0.25%` | **PASS** (0.25 / 0.25) |
| `1`   | 0.5%  | `... delay 7ms loss 0.5%` | `... delay 7ms loss 0.5%` | **PASS** (0.5 / 0.5) |
| `2`   | 1%    | `... delay 7ms loss 1%` | `... delay 7ms loss 1%` | **PASS** (1 / 1) |

- All three `apply.sh` runs exited rc=0; the only stderr noise is the known
  cosmetic `sch_htb: quantum ... is big` warnings (same as the 2026-10-12 run).
- Direction-split semantics unchanged: half per direction, as before; only the
  arithmetic became decimal-aware (`0.5 → 0.25` now instead of being
  rejected; `1 → 0.5` instead of the old `1/2 → 0` truncation bug).
- `show.sh` with loss active (tier 2 left in place): renders the netem leaf
  cleanly in both directions (`loss 1%`, `delay 7ms`), stats lines normal,
  filters/fh intact. No changes were needed to `show.sh` or `clear.sh`.
- `clear.sh` after the last tier: `STATUS: Interface 'eth4' completely cleared.
  All shaping removed.` (rc=0); `show.sh` post-clear shows only stock
  `mq`/`pfifo_fast` egress and "Ingress Mirror (ifb0) Not Active" — pristine.

### validate.sh loss-readback sub-gate (harness against live state)

```
### tier loss=0.5 (expected 0.25%/dir)
Loss readback       : egress=0.25%  ingress(ifb)=0.25%  (expected 0.25%/dir for 0.5% total)
Loss gate: PASS (|observed - 0.25%| <= 0.05pp per direction)
### tier loss=1 (expected 0.5%/dir)
Loss readback       : egress=0.5%  ingress(ifb)=0.5%  (expected 0.50%/dir for 1% total)
Loss gate: PASS (|observed - 0.50%| <= 0.05pp per direction)
### tier loss=2 (expected 1%/dir)
Loss readback       : egress=1%  ingress(ifb)=1%  (expected 1%/dir for 2% total)
Loss gate: PASS (|observed - 1%| <= 0.05pp per direction)
HARNESS_RESULT: ALL_PASS
```

### Negative tests (live kernel)

- Claiming `--link-b 15 1.0` while the kernel actually held the `0.5` tier
  (0.25%/dir): readback gate **FAILed as designed**
  (`Loss gate: FAIL (expected 0.50%/dir; observed egress=0.25%, ingress=0.25%; tolerance 0.05pp)`).
- Rejection before any `tc` mutation (no qdisc state left behind; `show.sh`
  netem line count = 0 afterwards):
  `0.55` (2 decimals) ✗, `1.25` ✗, `101` ✗, `abc` ✗, `-1` ✗ — all rejected by
  both `apply.sh` and `validate.sh`; edges `100` ✓ and `100.0` ✓ accepted.

## Final state

Host left pristine: `clear.sh` rc=0, `show.sh` stock `mq`/`pfifo_fast` only,
no `ifb0` device, no netem/htb. Raw per-tier logs: `.scratch/netem_frac_probe/`
(`apply_{0.5,1,2}.log`, `qdisc_{egress,ifb}_{0.5,1,2}.log`,
`show_with_loss.log`, `clear_final.log`, `show_final.log`).

## Notes

- 0.05pp tolerance note: netem normalizes `loss 0.50%` → `loss 0.5%` on
  readback, so the comparison is numeric, not string-based — the readback
  harness handles both formats.
- `apply.sh`'s `usage` text now documents `LOSS_PCT` as "0..100, at most one
  decimal place". `docs/NODE_ACCESS.md` §5 table row updated to match.
