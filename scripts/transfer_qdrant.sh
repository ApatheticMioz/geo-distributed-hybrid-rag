#!/usr/bin/env bash
set -e
echo "Starting Qdrant storage transfer (36GB) to Node B..."
SRC="systems/node_b/qdrant_storage"  # after 2026-09-18 restructure (was node_B/)
REMOTE_HOST="${REMOTE_HOST:-laptop-wg}"
REMOTE_DIR="D:/FAST/Semester6/NLP/Project_Laptop/systems/node_b/qdrant_storage"

echo "Targeting host: $REMOTE_HOST -> $REMOTE_DIR"
ssh "$REMOTE_HOST" "pwsh.exe -Command \"New-Item -ItemType Directory -Force -Path '$REMOTE_DIR' | Out-Null\""
tar -cf - -C "$SRC" . | ssh "$REMOTE_HOST" "pwsh.exe -Command \"Set-Location '$REMOTE_DIR'; tar.exe -xf -\""
echo "Qdrant storage transfer complete!"

