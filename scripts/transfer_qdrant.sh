#!/usr/bin/env bash
set -e
echo "Starting Qdrant storage transfer (36GB) to Node B..."
SRC="node_B/qdrant_storage"
REMOTE_DIR="D:/FAST/Semester6/NLP/Project_Laptop/node_B/qdrant_storage"

ssh node-b "powershell.exe -Command \"New-Item -ItemType Directory -Force -Path '$REMOTE_DIR' | Out-Null\""
tar -cf - -C "$SRC" . | ssh node-b "powershell.exe -Command \"Set-Location '$REMOTE_DIR'; tar.exe -xf -\""
echo "Qdrant storage transfer complete!"
