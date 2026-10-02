import sys
sys.path.insert(0, r"D:\Work\Semester6\NLP\Project_Laptop\systems\node_b\implementation\.venv\Lib\site-packages")
import torch
print("CUDA Available:", torch.cuda.is_available())
if torch.cuda.is_available():
    print("Device Name:", torch.cuda.get_device_name(0))
    print("VRAM Allocated:", torch.cuda.memory_allocated(0))
