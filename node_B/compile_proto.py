"""
Helper script to compile Protocol Buffer files
Run this after installing grpc-tools
"""

import subprocess
import sys
from pathlib import Path

def compile_proto():
    """Compile retrieval.proto to Python gRPC code."""
    project_root = Path(__file__).parent
    proto_file = project_root / "retrieval.proto"
    output_dir = project_root / "implementation"
    
    if not proto_file.exists():
        print(f"Error: {proto_file} not found")
        sys.exit(1)
    
    print(f"Compiling {proto_file}...")
    
    try:
        cmd = [
            sys.executable,
            "-m",
            "grpc_tools.protoc",
            f"--python_out={output_dir}",
            f"--grpc_python_out={output_dir}",
            f"--pyi_out={output_dir}",
            "-I",
            str(project_root),
            str(proto_file)
        ]
        
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        print(f"✓ Proto compilation successful!")
        print(f"  Generated files:")
        print(f"    - {output_dir}/retrieval_pb2.py")
        print(f"    - {output_dir}/retrieval_pb2_grpc.py")
        print(f"    - {output_dir}/retrieval_pb2.pyi")
        
    except subprocess.CalledProcessError as e:
        print(f"✗ Proto compilation failed!")
        print(f"STDOUT: {e.stdout}")
        print(f"STDERR: {e.stderr}")
        sys.exit(1)
    except FileNotFoundError:
        print("✗ Error: grpc_tools.protoc not found")
        print("  Make sure grpc-tools is installed: pip install grpc-tools")
        sys.exit(1)

if __name__ == "__main__":
    compile_proto()
