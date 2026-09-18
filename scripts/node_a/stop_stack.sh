#!/usr/bin/env bash
# Stop the Node A generation orchestrator started by start_stack.sh.
# The vLLM engine at localhost:18020 is NOT touched by this script.
set -euo pipefail

PID_FILE="/tmp/pdc_node_a_orchestrator.pid"

if [ ! -f "$PID_FILE" ]; then
    echo "no pid file; orchestrator not started by start_stack.sh"
    exit 0
fi

PID="$(cat "$PID_FILE")"
if kill -0 "$PID" 2>/dev/null; then
    kill "$PID"
    for _ in $(seq 1 15); do
        kill -0 "$PID" 2>/dev/null || break
        sleep 1
    done
    if kill -0 "$PID" 2>/dev/null; then
        echo "process $PID did not exit after SIGTERM; sending SIGKILL" >&2
        kill -9 "$PID"
    fi
    echo "orchestrator stopped (was pid $PID)"
else
    echo "pid $PID not alive; cleaning stale pid file"
fi
rm -f "$PID_FILE"
