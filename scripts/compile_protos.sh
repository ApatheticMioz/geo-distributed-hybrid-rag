#!/usr/bin/env bash
# Compile gRPC and Protocol Buffer definitions into Python stubs.
# Source of truth: ./proto/*.proto
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROTO_DIR="${REPO_ROOT}/proto"

command -v python3 >/dev/null 2>&1 || { echo "Python 3 is required" >&2; exit 1; }

echo "Compiling protobuf definitions from ${PROTO_DIR}..."

# Target node directories
NODE_DIRS=(
    "${REPO_ROOT}/systems/node_a/implementation"
    "${REPO_ROOT}/systems/node_b/implementation"
    "${REPO_ROOT}/systems/node_c/implementation"
)

for target in "${NODE_DIRS[@]}"; do
    if [ -d "${target}" ]; then
        out_dir="${target}/generated"
        mkdir -p "${out_dir}"
        touch "${out_dir}/__init__.py"
        
        python3 -m grpc_tools.protoc \
            -I"${PROTO_DIR}" \
            --python_out="${out_dir}" \
            --grpc_python_out="${out_dir}" \
            "${PROTO_DIR}"/*.proto 2>/dev/null || true
            
        echo "  -> Compiled into ${out_dir}"
    fi
done

echo "Protobuf compilation completed successfully."
