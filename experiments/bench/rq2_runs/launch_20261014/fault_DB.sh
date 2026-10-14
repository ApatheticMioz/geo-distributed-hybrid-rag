#!/usr/bin/env bash
# CM-3 D-B fault (async): kill B gateway, restart after 4s. MUST return <1s so
# the driver stream keeps sampling the outage; never ssh synchronously here.
setsid nohup bash -c '
  ssh -o BatchMode=yes -o ConnectTimeout=8 laptop "schtasks /End /TN pdc_gateway"
  sleep 4
  ssh -o BatchMode=yes -o ConnectTimeout=8 laptop "schtasks /Run /TN pdc_gateway"
' >/dev/null 2>&1 < /dev/null &
exit 0
