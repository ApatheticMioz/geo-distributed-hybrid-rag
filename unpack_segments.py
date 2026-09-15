import os
import glob
import subprocess
import time

segments_dir = r"D:\FAST\Semester6\NLP\Project_Laptop\node_B\qdrant_storage\collections\msmarco_passages\0\segments"
tar_files = sorted(glob.glob(os.path.join(segments_dir, "*.tar")))
print(f"Found {len(tar_files)} segment tar files to unpack.")

t0 = time.time()
for i, tar_path in enumerate(tar_files, 1):
    uuid = os.path.basename(tar_path).replace(".tar", "")
    dest_dir = os.path.join(segments_dir, uuid)
    os.makedirs(dest_dir, exist_ok=True)
    
    t_start = time.time()
    cmd = ["tar.exe", "-xf", tar_path, "-C", dest_dir, "--strip-components=2"]
    subprocess.run(cmd, check=True)
    os.remove(tar_path)
    elapsed = time.time() - t_start
    print(f"[{i}/{len(tar_files)}] Unpacked segment {uuid} in {elapsed:.1f}s")

total_time = time.time() - t0
print(f"All {len(tar_files)} segments unpacked successfully in {total_time:.1f}s!")
