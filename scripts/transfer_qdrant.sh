#!/usr/bin/env bash
set -e
echo "Starting Qdrant storage transfer (36GB) to Node B..."
SRC="systems/node_b/qdrant_storage"  # after 2026-09-18 restructure (was node_B/)
REMOTE_DIR="D:/FAST/Semester6/NLP/Project_Laptop/systems/node_b/qdrant_storage  # valid on B only after its dir migration"

ssh node-b "powershell.exe -Command \"New-Item -ItemType Directory -Force -Path '$REMOTE_DIR' | Out-Null\""
tar -cf - -C "$SRC" . | ssh node-b "powershell.exe -Command \"Set-Location '$REMOTE_DIR'; tar.exe -xf -\""
echo "Qdrant storage transfer complete!"
