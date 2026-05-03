# WikiQA Dataset Sync to Qdrant - Setup & Usage Guide

## Overview
The `node_B/implementation/sync_qdrant.py` script automates the ingestion of the WikiQA dataset into your distributed RAG pipeline:

1. **Downloads WikiQA dataset** from HuggingFace
2. **Hydrates Node A's SQLite database** with unique sentences
3. **Loads BGE-M3 embedding model** on Node A's RTX 3080 GPU
4. **Encodes sentences in batches** (batch size: 64)
5. **Upserts embeddings to Node B's Qdrant** instance

---

## Installation & Setup

### Step 1: Install Dependencies on Node A

Run this command in Node A's implementation directory:

```bash
cd /home/apath/Work/PDC/Project/node_A/implementation
pip install -r requirements.txt
```

This installs the new packages:
- `datasets>=2.14.0` — Load WikiQA from HuggingFace
- `FlagEmbedding>=1.2.9` — BGE-M3 embedding model
- `qdrant-client>=1.9.0` — Connect to Qdrant
- `tqdm>=4.66.0` — Progress bars

### Step 2: Verify Node B's Qdrant is Running

Ensure Qdrant is running on Node B at the configured address:

```bash
# Default: 10.0.0.2:6333
curl http://10.0.0.2:6333/health
```

If Qdrant isn't accessible, update these variables at the top of `sync_qdrant.py`:

```python
QDRANT_HOST = "10.0.0.2"   # Change this to your Node B IP
QDRANT_PORT = 6333         # Change this if using a different port
```

### Step 3: Update Database Path (if needed)

The script expects Node A's SQLite database at:

```python
CORPUS_DB_PATH = "../node_A/implementation/corpus.sqlite"
```

If running from a different directory, update this path accordingly.

---

## Running the Script

### From Node A (Recommended)

Navigate to the project root and run:

```bash
cd /home/apath/Work/PDC/Project
python node_B/implementation/sync_qdrant.py
```

Or directly:

```bash
python /home/apath/Work/PDC/Project/node_B/implementation/sync_qdrant.py
```

### Expected Output

```
================================================================================
WikiQA Dataset Sync to Qdrant - Starting
================================================================================
[*] CUDA available: True
[*] GPU: NVIDIA RTX 3080
[*] CUDA Version: 12.1

[*] Loading WikiQA dataset from HuggingFace...
[*] Extracting unique sentences from train split...
[✓] Extracted 87360 unique sentences from WikiQA

[*] Connecting to SQLite database: ../node_A/implementation/corpus.sqlite
[*] Inserting 87360 passages into SQLite database...
SQLite Insert: 100%|████████| 1/1 [00:15<00:00, 15.23s/batch]
[✓] SQLite database updated with 87360 passages

[*] Loading embedding model 'BAAI/bge-m3' on GPU...
    This may take 1-2 minutes on the first run...
[✓] Embedding model loaded successfully

[*] Connecting to Qdrant at 10.0.0.2:6333...
[✓] Connected to Qdrant successfully

[*] Deleted existing collection 'wikiqa_passages'
[*] Creating Qdrant collection 'wikiqa_passages'...
[✓] Collection 'wikiqa_passages' created successfully

[*] Starting batch encoding and upsert to Qdrant...
    Batch size: 64, Total sentences: 87360
Encoding & Upserting: 100%|████████| 1365/1365 [15:42<00:00,  1.45s/batch]
[✓] Upsert complete. Collection now contains 87360 vectors

================================================================================
WikiQA Sync Complete!
================================================================================
Summary:
  - Sentences ingested: 87360
  - Qdrant collection: wikiqa_passages
  - SQLite database: ../node_A/implementation/corpus.sqlite
```

---

## Script Features

### Configuration Variables (Top of Script)

All settings are easily configurable at the top:

```python
# Qdrant Vector Database Configuration (Node B)
QDRANT_HOST = "10.0.0.2"
QDRANT_PORT = 6333

# SQLite Database Configuration (Node A)
CORPUS_DB_PATH = "../node_A/implementation/corpus.sqlite"

# Embedding Model Configuration
EMBEDDING_MODEL = "BAAI/bge-m3"
BATCH_SIZE = 64
COLLECTION_NAME = "wikiqa_passages"
VECTOR_SIZE = 1024  # BGE-M3 dense vector size
```

### Key Design Decisions

1. **Deterministic IDs**: Uses `uuid5(uuid.NAMESPACE_DNS, doc_id)` to generate consistent integer IDs for Qdrant. This means the same sentences always get the same vector ID.

2. **Empty Payloads**: `PointStruct` objects contain only `id` and `vector` (no text) to minimize memory usage on Node B. Text lookups happen via Node A's SQLite database.

3. **Batch Processing**: 
   - SQLite inserts: 100,000 rows per batch
   - Encoding: 64 sentences per batch
   - All processed with progress bars using `tqdm`

4. **Collection Recreation**: If the collection already exists, it's deleted and recreated from scratch.

5. **GPU Acceleration**: Uses FP16 (half-precision) embedding to maximize RTX 3080 throughput.

---

## Troubleshooting

### "ModuleNotFoundError: No module named 'datasets'"

Install the missing package:

```bash
pip install datasets>=2.14.0
```

### "Connection refused" to Qdrant

Check:
1. Qdrant is running on Node B
2. Network connectivity: `ping 10.0.0.2`
3. Port is correct: `QDRANT_PORT = 6333`

### Slow Embedding / CPU-Only Encoding

Verify GPU is available:

```bash
python -c "import torch; print(torch.cuda.is_available())"
```

If false, ensure CUDA 12.1 is installed on Node A.

### Out of Memory Errors

Reduce `BATCH_SIZE`:

```python
BATCH_SIZE = 32  # Or even smaller: 16
```

### SQLite "Database is locked"

Another process is accessing the database. Ensure only one instance of the script runs at a time.

---

## Performance Notes

**On RTX 3080 with CUDA 12.1:**
- Model loading: ~1-2 minutes (first run, then cached)
- Embedding throughput: ~1,000-2,000 sentences/second
- Expected total time for ~87k sentences: **15-20 minutes**

---

## Files Modified

1. **`node_B/implementation/sync_qdrant.py`** — Completely rewritten for WikiQA ingestion
2. **`node_A/implementation/requirements.txt`** — Added datasets, FlagEmbedding, qdrant-client, tqdm
3. **`node_B/implementation/requirements.txt`** — Added datasets, tqdm

---

## Next Steps

After successful ingestion:

1. Verify data in Qdrant:
   ```python
   from qdrant_client import QdrantClient
   client = QdrantClient(host="10.0.0.2", port=6333)
   info = client.get_collection("wikiqa_passages")
   print(f"Collection size: {info.points_count}")
   ```

2. Query Node A's SQLite to retrieve text:
   ```python
   import sqlite3
   conn = sqlite3.connect("node_A/implementation/corpus.sqlite")
   cursor = conn.cursor()
   cursor.execute("SELECT text FROM passages WHERE doc_id = ?", ("wikiqa_0",))
   print(cursor.fetchone())
   ```

3. Test end-to-end retrieval with your dispatch pipeline

---

## Dataset Info

**WikiQA**:
- Source: HuggingFace `wiki_qa` dataset
- Training split sentences: ~87,360 unique
- Vector embedding: 1024-dimensional (BGE-M3 dense)
- Collection name in Qdrant: `wikiqa_passages`
- Doc ID format: `wikiqa_0`, `wikiqa_1`, ..., `wikiqa_87359`
