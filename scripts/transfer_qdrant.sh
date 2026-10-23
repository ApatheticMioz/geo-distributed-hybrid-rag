#!/usr/bin/env bash
set -euo pipefail
echo "Starting Qdrant storage transfer to Node B..."
SRC="systems/node_b/qdrant_storage"
REMOTE_HOST="${REMOTE_HOST:-laptop-wg}"
REMOTE_DIR="${REMOTE_DIR:-D:/geo-distributed-hybrid-rag/systems/node_b/qdrant_storage}"

echo "Targeting host: $REMOTE_HOST -> $REMOTE_DIR"
ssh "$REMOTE_HOST" "pwsh.exe -Command \"New-Item -ItemType Directory -Force -Path '$REMOTE_DIR' | Out-Null\""
tar -cf - -C "$SRC" . | ssh "$REMOTE_HOST" "pwsh.exe -Command \"Set-Location '$REMOTE_DIR'; tar.exe -xf -\""
echo "Qdrant storage transfer complete!"
