import os
import sqlite3
import shutil
from pathlib import Path
import numpy as np
import torch
import tantivy
from FlagEmbedding import BGEM3FlagModel
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams
from tqdm import tqdm

DB_PATH = Path(__file__).resolve().parent / "corpus.sqlite"
TANTIVY_DIR = Path(__file__).resolve().parent / "data" / "tantivy_index"
QDRANT_HOST = "localhost"
QDRANT_PORT = 6333
COLLECTION_NAME = "msmarco_passages"
LIMIT = 5000
MODEL_NAME = "BAAI/bge-m3"
BATCH_SIZE = 64

def main():
    # Check if DB exists
    if not DB_PATH.exists():
        print(f"Error: Database not found at {DB_PATH}")
        return

    # 1. Setup Qdrant
    print(f"Connecting to Qdrant at {QDRANT_HOST}:{QDRANT_PORT}...")
    qdrant_client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

    # Recreate collection
    try:
        qdrant_client.delete_collection(COLLECTION_NAME)
        print(f"Deleted existing Qdrant collection '{COLLECTION_NAME}'")
    except Exception:
        pass

    qdrant_client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=1024, distance=Distance.COSINE)
    )
    print(f"Created Qdrant collection '{COLLECTION_NAME}'")

    print(f"Connecting to SQLite database: {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    # Fetch first 5000 passages (following the alphabetical order in the DB)
    cursor.execute("SELECT doc_id, text FROM passages LIMIT ?", (LIMIT,))
    passages = cursor.fetchall()
    conn.close()
    print(f"Loaded {len(passages)} passages from SQLite.")

    # 2. Setup Tantivy Index
    print("Setting up Tantivy Index...")
    if TANTIVY_DIR.exists():
        try:
            shutil.rmtree(TANTIVY_DIR)
        except Exception as e:
            print(f"Warning: Could not remove existing Tantivy directory: {e}. Trying to delete contents...")
            for item in TANTIVY_DIR.iterdir():
                try:
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()
                except Exception as ex:
                    print(f"Failed to delete {item}: {ex}")
    TANTIVY_DIR.mkdir(parents=True, exist_ok=True)

    schema_builder = tantivy.SchemaBuilder()
    schema_builder.add_text_field("doc_id", stored=True, tokenizer_name="raw")
    schema_builder.add_text_field("body", stored=True, tokenizer_name="en_stem")
    schema = schema_builder.build()

    index = tantivy.Index(schema, path=str(TANTIVY_DIR))
    writer = index.writer(512 * 1024 * 1024)

    for doc_id, text in passages:
        writer.add_document(
            tantivy.Document(
                doc_id=[str(doc_id)],
                body=[str(text)],
            )
        )
    writer.commit()
    # Ensure all changes are flushed to disk
    index.reload()
    print(f"Tantivy Index built successfully. Total docs in index: {index.searcher().num_docs}")

    # 3. Load embedding model
    print(f"Loading {MODEL_NAME} embedding model...")
    model = BGEM3FlagModel(MODEL_NAME, use_fp16=torch.cuda.is_available())
    print("Embedding model loaded.")

    # Index in batches
    print("Generating embeddings and indexing to Qdrant...")
    for i in tqdm(range(0, len(passages), BATCH_SIZE)):
        batch = passages[i:i+BATCH_SIZE]
        batch_ids = [row[0] for row in batch]
        batch_texts = [row[1] for row in batch]

        # Generate embeddings
        with torch.inference_mode():
            embeddings = model.encode(
                batch_texts,
                return_dense=True,
                return_sparse=False,
                return_colbert_vecs=False
            )["dense_vecs"]

        points = []
        for idx, (doc_id, emb) in enumerate(zip(batch_ids, embeddings)):
            points.append(
                PointStruct(
                    id=i + idx,
                    vector=emb.tolist(),
                    payload={"doc_id": str(doc_id)}
                )
            )

        qdrant_client.upsert(
            collection_name=COLLECTION_NAME,
            points=points,
            wait=True
        )

    print("Qdrant indexing complete.")
    print(f"Qdrant collection count: {qdrant_client.get_collection(COLLECTION_NAME).points_count}")

if __name__ == "__main__":
    main()
