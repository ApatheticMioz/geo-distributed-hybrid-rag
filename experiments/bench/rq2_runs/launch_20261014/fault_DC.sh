#!/usr/bin/env bash
# CM-3 D-C fault (async): kill C gateway, restart after 4s (ENABLE precondition
# per NODE_ACCESS SS3). MUST return <1s; never ssh synchronously here.
setsid nohup bash -c '
  ssh -o BatchMode=yes -o ConnectTimeout=8 friend-laptop "schtasks /End /TN pdc_gateway_c"
  sleep 4
  ssh -o BatchMode=yes -o ConnectTimeout=8 friend-laptop "schtasks /Change /TN pdc_gateway_c /ENABLE; schtasks /Run /TN pdc_gateway_c"
' >/dev/null 2>&1 < /dev/null &
exit 0
