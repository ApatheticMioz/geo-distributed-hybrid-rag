#!/usr/bin/env bash
# CM-3 D-C fault (async, detached): kill C gateway, restart after 4s
# (ENABLE precondition per NODE_ACCESS SS3). ControlPath=none: bypass ssh mux.
setsid nohup bash -c '
  {
    echo "start $(date -Ins)"
    ssh -o BatchMode=yes -o ConnectTimeout=8 -o ControlPath=none node-c "schtasks /End /TN pdc_gateway_c"
    echo "killed rc=$? at $(date -Ins)"
    sleep 4
    ssh -o BatchMode=yes -o ConnectTimeout=8 -o ControlPath=none node-c "schtasks /Change /TN pdc_gateway_c /ENABLE; schtasks /Run /TN pdc_gateway_c"
    echo "restarted rc=$? at $(date -Ins)"
  } >> /tmp/fault_DC_exec.log 2>&1
' >/dev/null 2>&1 < /dev/null &
exit 0
