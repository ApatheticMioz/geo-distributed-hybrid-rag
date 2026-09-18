#!/usr/bin/env bash
# Start the Node A generation orchestrator (gRPC :50052 + FastAPI :8001).
# The vLLM engine itself (qwen3.8-27b at localhost:18020) is managed
# separately (D:\LLM_Ecosystem\scripts\main) and must already be running.
# Fail-fast: no retries, no masking. PID recorded in /tmp/pdc_node_a_orchestrator.pid
set -euo pipefail

IMPL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../node_A/implementation" && pwd)"
PID_FILE="/tmp/pdc_node_a_orchestrator.pid"

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "orchestrator already running (pid $(cat "$PID_FILE"))"
    exit 0
fi

command -v ss >/dev/null || { echo "ss not available" >&2; exit 1; }
if ss -ltn | grep -q ':50052 '; then
    echo "FAIL: something already listens on :50052 but no pid file exists" >&2
    exit 1
fi

cd "$IMPL_DIR"
nohup .venv/bin/python -m src.main >> /tmp/pdc_node_a_orchestrator.log 2>&1 &
echo $! > "$PID_FILE"

# Wait (max 30s) for the gRPC listener; fail fast if it never comes up.
for _ in $(seq 1 30); do
    if ss -ltn | grep -q ':50052 '; then
        echo "orchestrator up (pid $(cat "$PID_FILE"), gRPC :50052, HTTP :8001)"
        exit 0
    fi
    if ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "FAIL: orchestrator process died during startup; tail of log:" >&2
        tail -20 /tmp/pdc_node_a_orchestrator.log >&2
        exit 1
    fi
    sleep 1
done

echo "FAIL: :50052 not listening after 30s; tail of log:" >&2
tail -20 /tmp/pdc_node_a_orchestrator.log >&2
exit 1
