import os
from pathlib import Path

# Server
HOST: str = "0.0.0.0"
PORT: int = 8001

# Model and DB paths (relative to implementation/)
MODEL_PATH: str = "../llama3-awq"
DB_PATH: str = str(Path(__file__).resolve().parent.parent / "corpus.sqlite")

# Timing / ranking constants
T_THRESHOLD_MS: int = 160
RRF_K: int = 60

# Generation params
MAX_TOKENS: int = 512
TEMPERATURE: float = 0.2

# Generation backend: "external" (OpenAI-compatible endpoint) or "vllm" (in-process engine)
GENERATION_BACKEND: str = "external"
EXTERNAL_ENDPOINT: str = "http://127.0.0.1:18020/v1"
EXTERNAL_MODEL: str = "qwen3.8-27b"

# Mode
MOCK_MODE: bool = False  # True: skip vLLM/AWQ model loading, return simulated responses

# vLLM options
GPU_MEMORY_UTILIZATION: float = 0.90
QUANTIZATION: str = "awq"
MAX_MODEL_LEN: int = 4096
ENFORCE_EAGER: bool = True

# Small helper to allow env overrides
def env(key: str, default):
    return os.environ.get(key, default)
