"""
WikiQA Dataset Sync to Qdrant Vector Database

This script:
1. Loads the WikiQA dataset from HuggingFace
2. Extracts unique sentences and stores them in Node A's SQLite database
3. Loads a BGE-M3 embedding model on GPU (Node A's RTX 3080)
4. Connects to Node B's Qdrant instance and creates a collection
5. Encodes sentences in batches and upserts them to Qdrant

Requirements:
- FlagEmbedding, qdrant-client, datasets (pip install datasets if missing)
- Node A: GPU with CUDA 12.1+ support (RTX 3080 recommended)
- Node B: Qdrant service running at QDRANT_HOST:QDRANT_PORT
"""

import sqlite3
import uuid
from typing import List, Set
from pathlib import Path

import torch
from datasets import load_dataset
from FlagEmbedding import BGEM3FlagModel
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams
from tqdm import tqdm

# ============================================================================
# CONFIGURATION VARIABLES (Modify these as needed)
# ============================================================================

# Qdrant Vector Database Configuration (Node B)
QDRANT_HOST = "10.8.0.5"
QDRANT_PORT = 6333

# SQLite Database Configuration (Node A - text hydration)
# Update this path if running from a different location
CORPUS_DB_PATH = "../node_A/implementation/corpus.sqlite"

# Embedding Model Configuration
EMBEDDING_MODEL = "BAAI/bge-m3"
BATCH_SIZE = 128
COLLECTION_NAME = "wikiqa_passages"
VECTOR_SIZE = 1024  # BGE-M3 dense vector size

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def load_and_extract_sentences() -> tuple[List[str], List[str]]:
    """
    Load WikiQA dataset and extract unique answer passages from the train split.
    
    WikiQA dataset contains question-answer pairs. We extract unique answers
    as our passage corpus for retrieval.
    
    Returns:
        Tuple of (doc_ids, passages) - parallel lists where doc_ids[i] 
        corresponds to passages[i]
    """
    print("[*] Loading WikiQA dataset from HuggingFace...")
    dataset = load_dataset("wiki_qa")
    
    unique_passages: Set[str] = set()
    print("[*] Extracting unique answer passages from train split...")
    print(f"    Dataset has {len(dataset['train'])} examples")
    
    for example in dataset["train"]:
        # WikiQA has 'answer' field with passage text
        answer = example.get("answer", "").strip()
        if answer:  # Only add non-empty answers
            unique_passages.add(answer)
    
    # Create deterministic doc_ids
    passages_list = sorted(list(unique_passages))
    doc_ids = [f"wikiqa_{i}" for i in range(len(passages_list))]
    
    print(f"[✓] Extracted {len(doc_ids)} unique answer passages from WikiQA")
    return doc_ids, passages_list


def hydrate_sqlite_db(doc_ids: List[str], sentences: List[str]) -> None:
    """
    Insert doc_id and sentence pairs into the local SQLite database.
    Uses INSERT OR IGNORE to skip duplicates.
    """
    db_path = Path(CORPUS_DB_PATH)
    
    if not db_path.exists():
        print(f"[!] Warning: Database not found at {db_path}")
        print(f"[*] Creating new database at {db_path}...")
        db_path.parent.mkdir(parents=True, exist_ok=True)
    
    print(f"[*] Connecting to SQLite database: {db_path}")
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    
    # Ensure the passages table exists with the correct schema
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS passages (
            doc_id TEXT PRIMARY KEY,
            text TEXT
        )
    """)
    conn.commit()
    
    # Prepare batch insertion
    pairs = list(zip(doc_ids, sentences))
    batch_size = 100000
    
    print(f"[*] Inserting {len(pairs)} passages into SQLite database...")
    for i in tqdm(range(0, len(pairs), batch_size), desc="SQLite Insert"):
        batch = pairs[i : i + batch_size]
        cursor.executemany(
            "INSERT OR IGNORE INTO passages (doc_id, text) VALUES (?, ?)",
            batch
        )
        conn.commit()
    
    conn.close()
    print(f"[✓] SQLite database updated with {len(pairs)} passages")


def create_qdrant_collection(client: QdrantClient) -> None:
    """
    Create or recreate the Qdrant collection for WikiQA passages.
    """
    try:
        # Try to delete existing collection to start fresh
        client.delete_collection(COLLECTION_NAME)
        print(f"[*] Deleted existing collection '{COLLECTION_NAME}'")
    except Exception:
        pass  # Collection doesn't exist, which is fine
    
    # Create new collection with specified vector parameters
    print(f"[*] Creating Qdrant collection '{COLLECTION_NAME}'...")
    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE)
    )
    print(f"[✓] Collection '{COLLECTION_NAME}' created successfully")


def generate_deterministic_id(doc_id: str) -> int:
    """
    Generate a deterministic integer ID from a string doc_id.
    Uses uuid5 with DNS namespace for reproducibility.
    """
    uid = uuid.uuid5(uuid.NAMESPACE_DNS, doc_id)
    # Convert UUID to integer (take first 64 bits for Qdrant compatibility)
    return int(uid.int & 0xFFFFFFFFFFFFFFFF)


def encode_and_upsert_batches(
    client: QdrantClient,
    doc_ids: List[str],
    sentences: List[str],
    model: BGEM3FlagModel
) -> None:
    """
    Encode sentences in batches and upsert to Qdrant.
    """
    print(f"[*] Starting batch encoding and upsert to Qdrant...")
    print(f"    Batch size: {BATCH_SIZE}, Total sentences: {len(sentences)}")
    
    total_batches = (len(sentences) + BATCH_SIZE - 1) // BATCH_SIZE
    
    for batch_idx in tqdm(range(total_batches), desc="Encoding & Upserting"):
        start = batch_idx * BATCH_SIZE
        end = min(start + BATCH_SIZE, len(sentences))
        
        batch_docs = sentences[start:end]
        batch_ids = doc_ids[start:end]
        
        # Encode batch using BGE-M3 model
        # Return only dense vectors (not sparse or colbert vectors)
        embeddings = model.encode(
            batch_docs,
            return_dense=True,
            return_sparse=False,
            return_colbert_vecs=False
        )["dense_vecs"]
        
        # Verify embedding dimension
        if embeddings.shape[1] != VECTOR_SIZE:
            print(f"[!] Warning: Expected vector size {VECTOR_SIZE}, got {embeddings.shape[1]}")
        
        # Create PointStruct objects with deterministic IDs
        # Payload is empty to save memory on Node B
        points = [
            PointStruct(
                id=generate_deterministic_id(doc_id),
                vector=embedding.tolist()
            )
            for doc_id, embedding in zip(batch_ids, embeddings)
        ]
        
        # Upsert to Qdrant
        client.upsert(
            collection_name=COLLECTION_NAME,
            points=points
        )
    
    # Verify collection size
    collection_info = client.get_collection(COLLECTION_NAME)
    print(f"[✓] Upsert complete. Collection now contains {collection_info.points_count} vectors")


def main():
    """
    Main execution flow for WikiQA sync to Qdrant.
    """
    print("=" * 80)
    print("WikiQA Dataset Sync to Qdrant - Starting")
    print("=" * 80)
    
    # Check CUDA availability
    print(f"[*] CUDA available: {torch.cuda.is_available()}")
    if torch.cuda.is_available():
        print(f"[*] GPU: {torch.cuda.get_device_name(0)}")
        print(f"[*] CUDA Version: {torch.version.cuda}")
    else:
        print("[!] Warning: CUDA not available. CPU encoding will be very slow.")
    
    print()
    
    # Step 1: Load and extract sentences
    doc_ids, sentences = load_and_extract_sentences()
    
    print()
    
    # Step 2: Hydrate SQLite database (Node A)
    hydrate_sqlite_db(doc_ids, sentences)
    
    print()
    
    # Step 3: Initialize embedding model
    print(f"[*] Loading embedding model '{EMBEDDING_MODEL}' on GPU...")
    print(f"    This may take 1-2 minutes on the first run...")
    model = BGEM3FlagModel(EMBEDDING_MODEL, use_fp16=True)
    print(f"[✓] Embedding model loaded successfully")
    
    print()
    
    # Step 4: Connect to Qdrant (Node B)
    print(f"[*] Connecting to Qdrant at {QDRANT_HOST}:{QDRANT_PORT}...")
    try:
        client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
        client.get_collections()  # Test connection
        print(f"[✓] Connected to Qdrant successfully")
    except Exception as e:
        print(f"[!] Error connecting to Qdrant: {e}")
        print(f"[!] Please ensure Qdrant is running at {QDRANT_HOST}:{QDRANT_PORT}")
        raise
    
    print()
    
    # Step 5: Create Qdrant collection
    create_qdrant_collection(client)
    
    print()
    
    # Step 6: Encode and upsert in batches
    encode_and_upsert_batches(client, doc_ids, sentences, model)
    
    print()
    print("=" * 80)
    print("WikiQA Sync Complete!")
    print("=" * 80)
    print(f"Summary:")
    print(f"  - Sentences ingested: {len(sentences)}")
    print(f"  - Qdrant collection: {COLLECTION_NAME}")
    print(f"  - SQLite database: {CORPUS_DB_PATH}")
    print()


if __name__ == "__main__":
    main()