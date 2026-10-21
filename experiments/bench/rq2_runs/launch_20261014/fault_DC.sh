#!/usr/bin/env bash
# CM-3 D-C fault (async, detached): kill C gateway, restart after 45s (outage must exceed the 30s request timeout so in-flight requests genuinely ERROR; same rationale as fault_DB.sh)
# (ENABLE precondition per NODE_ACCESS SS3). ControlPath=none: bypass ssh mux.
setsid nohup bash -c '
  SSH_CMD="ssh -o BatchMode=yes -o ConnectTimeout=8 -o ControlPath=none -i ~/.ssh/id_ed25519 -F ~/.ssh/config friend-wg"
  {
    echo "start $(date -Ins)"
    $SSH_CMD "schtasks /End /TN pdc_gateway_c"
    echo "killed rc=$? at $(date -Ins)"
    sleep 45
    $SSH_CMD "schtasks /Change /TN pdc_gateway_c /ENABLE; schtasks /Run /TN pdc_gateway_c"
    echo "restarted rc=$? at $(date -Ins)"
  } >> /tmp/fault_DC_exec.log 2>&1
' >/dev/null 2>&1 < /dev/null &
exit 0
