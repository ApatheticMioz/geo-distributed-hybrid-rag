#!/usr/bin/env bash
set -e
echo "Starting Tantivy index transfer (3.2GB) to Node B..."
SRC="node_B/implementation/data/tantivy_index_full"
REMOTE_DIR="D:/FAST/Semester6/NLP/Project_Laptop/node_B/implementation/data/tantivy_index"

ssh node-b "powershell.exe -Command \"New-Item -ItemType Directory -Force -Path '$REMOTE_DIR' | Out-Null\""
tar -cf - -C "$SRC" . | ssh node-b "powershell.exe -Command \"Set-Location '$REMOTE_DIR'; tar.exe -xf -\""
echo "Tantivy transfer complete!"
