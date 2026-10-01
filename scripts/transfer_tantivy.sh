#!/usr/bin/env bash
set -e
echo "Starting Tantivy index transfer (3.2GB) to Node B..."
SRC="systems/node_b/implementation/data/tantivy_index_full"
REMOTE_HOST="${REMOTE_HOST:-laptop-wg}"
REMOTE_DIR="D:/Work/Semester6/NLP/Project_Laptop/systems/node_b/implementation/data/tantivy_index"

echo "Targeting host: $REMOTE_HOST -> $REMOTE_DIR"
ssh "$REMOTE_HOST" "pwsh.exe -Command \"New-Item -ItemType Directory -Force -Path '$REMOTE_DIR' | Out-Null\""
tar -cf - -C "$SRC" . | ssh "$REMOTE_HOST" "pwsh.exe -Command \"Set-Location '$REMOTE_DIR'; tar.exe -xf -\""
echo "Tantivy transfer complete!"

