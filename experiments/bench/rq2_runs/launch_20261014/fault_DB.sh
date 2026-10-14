#!/usr/bin/env bash
# CM-3 D-B fault (async, detached): kill B gateway, restart after 4s.
# MUST return <1s. ControlPath=none bypasses ssh mux (stale ControlPersist
# sockets stalled detached ssh ~35s). Timestamps -> /tmp/fault_DB_exec.log.
setsid nohup bash -c '
  {
    echo "start $(date -Ins)"
    ssh -o BatchMode=yes -o ConnectTimeout=8 -o ControlPath=none laptop "schtasks /End /TN pdc_gateway"
    echo "killed rc=$? at $(date -Ins)"
    sleep 4
    ssh -o BatchMode=yes -o ConnectTimeout=8 -o ControlPath=none laptop "schtasks /Run /TN pdc_gateway"
    echo "restarted rc=$? at $(date -Ins)"
  } >> /tmp/fault_DB_exec.log 2>&1
' >/dev/null 2>&1 < /dev/null &
exit 0
