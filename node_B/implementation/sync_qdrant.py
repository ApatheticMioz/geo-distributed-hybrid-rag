"""
MS MARCO (and optional WikiQA) Sync to Qdrant Vector Database

This script:
1. Streams the MS MARCO passage corpus from a local SQLite database
2. Stores passages in a local SQLite corpus database
3. Loads a BGE-M3 embedding model
4. Connects to Qdrant and ensures collections exist
5. Encodes passages in batches and upserts them to Qdrant

Requirements:
- FlagEmbedding, qdrant-client
- Qdrant service running at QDRANT_HOST:QDRANT_PORT
"""

import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

import torch
from FlagEmbedding import BGEM3FlagModel
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams
from tqdm import tqdm

# ============================================================================
# CONFIGURATION VARIABLES (Modify these as needed)
# ============================================================================

# Qdrant Vector Database Configuration (Node B)
QDRANT_HOST = os.getenv("QDRANT_HOST", "127.0.0.1")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
QDRANT_TIMEOUT_SECONDS = float(os.getenv("QDRANT_TIMEOUT_SECONDS", "30"))
QDRANT_CONNECT_RETRIES = int(os.getenv("QDRANT_CONNECT_RETRIES", "5"))
QDRANT_CONNECT_BACKOFF_SECONDS = float(os.getenv("QDRANT_CONNECT_BACKOFF_SECONDS", "5"))

# SQLite Database Configuration (Node A - text hydration)
# Update this path if running from a different location
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CORPUS_DB_PATH = os.getenv(
    "CORPUS_DB_PATH",
    str(Path(__file__).resolve().parent / "corpus.sqlite")
)
SOURCE_MSMARCO_DB_PATH = os.getenv(
    "SOURCE_MSMARCO_DB_PATH",
    str(PROJECT_ROOT / "node_A" / "implementation" / "corpus.sqlite")
)
WIKIQA_DB_PATH = os.getenv(
    "WIKIQA_DB_PATH",
    str(PROJECT_ROOT / "node_A" / "implementation" / "wikiqa.sqlite")
)
WIKIQA_DOC_ID_PREFIX = os.getenv("WIKIQA_DOC_ID_PREFIX", "wikiqa")
AUTO_FETCH_WIKIQA = os.getenv("AUTO_FETCH_WIKIQA", "true").lower() in {"1", "true", "yes"}

# Embedding Model Configuration
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "128"))
VECTOR_SIZE = 1024  # BGE-M3 dense vector size
WAIT_FOR_UPSERT = os.getenv("WAIT_FOR_UPSERT", "true").lower() in {"1", "true", "yes"}
RECREATE_COLLECTION = os.getenv("RECREATE_COLLECTION", "true").lower() in {"1", "true", "yes"}
MAX_DOCS = int(os.getenv("MAX_DOCS", "0")) or None
INCLUDE_WIKIQA = os.getenv("INCLUDE_WIKIQA", "false").lower() in {"1", "true", "yes"}
START_OFFSET = int(os.getenv("START_OFFSET", "0"))
ORDER_BY_DOC_ID = os.getenv("ORDER_BY_DOC_ID", "true").lower() in {"1", "true", "yes"}
PROCESS_MSMARCO = os.getenv("PROCESS_MSMARCO", "true").lower() in {"1", "true", "yes"}
PROCESS_WIKIQA = os.getenv("PROCESS_WIKIQA", "").lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class DatasetConfig:
    name: str
    split: str
    collection: str
    id_prefix: str


MSMARCO_CONFIG = DatasetConfig(
    name="msmarco_corpus",
    split="corpus",
    collection="msmarco_passages",
    id_prefix="msmarco"
)
WIKIQA_CONFIG = DatasetConfig(
    name="wiki_qa",
    split="train",
    collection="wikiqa_passages",
    id_prefix="wikiqa"
)
DATASETS = []
if PROCESS_MSMARCO:
    DATASETS.append(MSMARCO_CONFIG)
if PROCESS_WIKIQA or INCLUDE_WIKIQA:
    DATASETS.append(WIKIQA_CONFIG)

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def make_hashed_doc_id(prefix: str, text: str) -> str:
    """
    Generate a deterministic doc_id from text content.
    """
    uid = uuid.uuid5(uuid.NAMESPACE_DNS, f"{prefix}:{text}")
    return f"{prefix}_{uid.hex}"


def apply_id_prefix(doc_id: str, prefix: Optional[str]) -> str:
    """
    Prefix doc_id only when it is not already prefixed.
    """
    if not prefix:
        return str(doc_id)
    doc_id_str = str(doc_id)
    prefix_token = f"{prefix}_"
    if doc_id_str.startswith(prefix_token):
        return doc_id_str
    return f"{prefix_token}{doc_id_str}"


def iter_sqlite_passages(
    db_path: str,
    id_prefix: Optional[str],
    start_offset: int = 0
) -> Iterator[Tuple[str, str]]:
    """
    Stream passages from a local SQLite corpus database.
    """
    if not db_path:
        raise ValueError("SQLite source path is required")

    source_path = Path(db_path)
    if not source_path.exists():
        raise FileNotFoundError(f"SQLite source not found: {source_path}")

    conn = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    cursor = conn.cursor()
    query = "SELECT doc_id, text FROM passages"
    if ORDER_BY_DOC_ID:
        query += " ORDER BY doc_id"
    if start_offset > 0:
        query += " LIMIT -1 OFFSET ?"
        cursor.execute(query, (start_offset,))
    else:
        cursor.execute(query)

    while True:
        rows = cursor.fetchmany(10000)
        if not rows:
            break
        for doc_id, text in rows:
            if text is None:
                continue
            cleaned = str(text).strip()
            if not cleaned:
                continue
            final_id = apply_id_prefix(doc_id, id_prefix)
            yield final_id, cleaned

    conn.close()


def iter_msmarco_passages() -> Iterator[Tuple[str, str]]:
    """
    Stream the full MS MARCO passage corpus (8.8M) from local SQLite.
    """
    return iter_sqlite_passages(SOURCE_MSMARCO_DB_PATH, id_prefix=None, start_offset=START_OFFSET)


def iter_wikiqa_passages() -> Iterator[Tuple[str, str]]:
    """
    Stream WikiQA passages from a local SQLite database.
    """
    if not WIKIQA_DB_PATH:
        raise ValueError("WIKIQA_DB_PATH must be set when INCLUDE_WIKIQA is enabled")
    return iter_sqlite_passages(WIKIQA_DB_PATH, id_prefix=WIKIQA_DOC_ID_PREFIX)


def open_sqlite_db() -> sqlite3.Connection:
    """
    Open or create the SQLite database and ensure the schema exists.
    """
    db_path = Path(CORPUS_DB_PATH)
    if not db_path.exists():
        print(f"[!] Warning: Database not found at {db_path}")
        print(f"[*] Creating new database at {db_path}...")
        db_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"[*] Connecting to SQLite database: {db_path}")
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA synchronous = OFF")
    conn.execute("PRAGMA journal_mode = MEMORY")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS passages (
            doc_id TEXT PRIMARY KEY,
            text TEXT
        )
    """)
    conn.commit()
    return conn


def build_wikiqa_sqlite(db_path: str) -> None:
    """
    Fetch WikiQA from HuggingFace and write unique answers into SQLite.
    """
    from datasets import load_dataset

    target_path = Path(db_path)
    if target_path.exists():
        return

    target_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[*] Building WikiQA SQLite at {target_path}")

    dataset = load_dataset("wiki_qa", split="train")
    unique_answers = set()
    for example in dataset:
        answer = example.get("answer", "")
        if not answer:
            continue
        text = str(answer).strip()
        if text:
            unique_answers.add(text)

    sorted_answers = sorted(unique_answers)
    conn = sqlite3.connect(str(target_path))
    conn.execute("PRAGMA synchronous = OFF")
    conn.execute("PRAGMA journal_mode = MEMORY")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS passages (
            doc_id TEXT PRIMARY KEY,
            text TEXT
        )
    """)
    conn.commit()

    batch = []
    batch_size = 100000
    for idx, text in enumerate(sorted_answers):
        batch.append((str(idx), text))
        if len(batch) >= batch_size:
            cursor.executemany("INSERT OR IGNORE INTO passages (doc_id, text) VALUES (?, ?)", batch)
            conn.commit()
            batch.clear()

    if batch:
        cursor.executemany("INSERT OR IGNORE INTO passages (doc_id, text) VALUES (?, ?)", batch)
        conn.commit()

    conn.close()
    print(f"[✓] WikiQA SQLite ready with {len(sorted_answers)} passages")


def same_path(path_a: str, path_b: str) -> bool:
    """
    Compare two filesystem paths for equality after resolving.
    """
    try:
        return Path(path_a).resolve() == Path(path_b).resolve()
    except FileNotFoundError:
        return False


def insert_passages(conn: sqlite3.Connection, pairs: List[Tuple[str, str]]) -> None:
    """
    Insert doc_id and text pairs into the SQLite database.
    """
    if not pairs:
        return
    cursor = conn.cursor()
    cursor.executemany(
        "INSERT OR IGNORE INTO passages (doc_id, text) VALUES (?, ?)",
        pairs
    )
    conn.commit()


def ensure_qdrant_collection(client: QdrantClient, collection_name: str) -> None:
    """
    Create or recreate a Qdrant collection for passage vectors.
    """
    if RECREATE_COLLECTION:
        try:
            client.delete_collection(collection_name)
            print(f"[*] Deleted existing collection '{collection_name}'")
        except Exception:
            pass

    try:
        client.get_collection(collection_name)
        print(f"[*] Collection '{collection_name}' already exists")
        return
    except Exception:
        pass

    print(f"[*] Creating Qdrant collection '{collection_name}'...")
    client.create_collection(
        collection_name=collection_name,
        vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE)
    )
    print(f"[✓] Collection '{collection_name}' created successfully")


def connect_qdrant() -> QdrantClient:
    """
    Connect to Qdrant with retries.
    """
    last_error: Optional[Exception] = None
    for attempt in range(1, QDRANT_CONNECT_RETRIES + 1):
        try:
            client = QdrantClient(
                host=QDRANT_HOST,
                port=QDRANT_PORT,
                timeout=QDRANT_TIMEOUT_SECONDS
            )
            client.get_collections()
            return client
        except Exception as exc:
            last_error = exc
            print(f"[!] Qdrant connection attempt {attempt} failed: {exc}")
            if attempt < QDRANT_CONNECT_RETRIES:
                time.sleep(QDRANT_CONNECT_BACKOFF_SECONDS)

    raise RuntimeError(f"Failed to connect to Qdrant after {QDRANT_CONNECT_RETRIES} attempts") from last_error


def generate_deterministic_id(doc_id: str) -> int:
    """
    Generate a deterministic integer ID from a string doc_id.
    Uses uuid5 with DNS namespace for reproducibility.
    """
    uid = uuid.uuid5(uuid.NAMESPACE_DNS, doc_id)
    # Convert UUID to integer (take first 64 bits for Qdrant compatibility)
    return int(uid.int & 0xFFFFFFFFFFFFFFFF)


def encode_and_upsert_batch(
    client: QdrantClient,
    collection_name: str,
    doc_ids: List[str],
    sentences: List[str],
    model: BGEM3FlagModel
) -> None:
    """
    Encode a batch of sentences and upsert into Qdrant.
    """
    if not sentences:
        return

    embeddings = model.encode(
        sentences,
        return_dense=True,
        return_sparse=False,
        return_colbert_vecs=False
    )["dense_vecs"]

    if embeddings.shape[1] != VECTOR_SIZE:
        print(f"[!] Warning: Expected vector size {VECTOR_SIZE}, got {embeddings.shape[1]}")

    points = [
        PointStruct(
            id=generate_deterministic_id(doc_id),
            vector=embedding.tolist(),
            payload={"doc_id": doc_id},
        )
        for doc_id, embedding in zip(doc_ids, embeddings)
    ]

    client.upsert(
        collection_name=collection_name,
        points=points,
        wait=WAIT_FOR_UPSERT
    )


def process_dataset(
    client: QdrantClient,
    conn: sqlite3.Connection,
    model: BGEM3FlagModel,
    dataset: DatasetConfig
) -> None:
    """
    Stream passages for a dataset, store in SQLite, and upsert embeddings.
    """
    if dataset.name == "msmarco_corpus":
        stream = iter_msmarco_passages()
        source_db_path = SOURCE_MSMARCO_DB_PATH
        resume_offset = START_OFFSET
    elif dataset.name == "wiki_qa":
        stream = iter_wikiqa_passages()
        source_db_path = WIKIQA_DB_PATH
        resume_offset = 0
    else:
        raise ValueError(f"Unsupported dataset: {dataset.name}")

    ensure_qdrant_collection(client, dataset.collection)
    if resume_offset > 0:
        print(f"[*] Starting ingestion for {dataset.name} -> {dataset.collection} (resume offset {resume_offset})")
    else:
        print(f"[*] Starting ingestion for {dataset.name} -> {dataset.collection}")

    batch_ids: List[str] = []
    batch_texts: List[str] = []
    processed = 0
    pbar_total = MAX_DOCS
    if pbar_total:
        progress = tqdm(total=pbar_total, desc=f"{dataset.name}", unit="passage")
    else:
        progress = tqdm(desc=f"{dataset.name}", unit="passage")

    for doc_id, text in stream:
        batch_ids.append(doc_id)
        batch_texts.append(text)
        processed += 1

        if len(batch_texts) >= BATCH_SIZE:
            if not same_path(source_db_path, CORPUS_DB_PATH):
                insert_passages(conn, list(zip(batch_ids, batch_texts)))
            encode_and_upsert_batch(client, dataset.collection, batch_ids, batch_texts, model)
            progress.update(len(batch_texts))
            batch_ids.clear()
            batch_texts.clear()

        if MAX_DOCS and processed >= MAX_DOCS:
            break

    if batch_texts:
        if not same_path(source_db_path, CORPUS_DB_PATH):
            insert_passages(conn, list(zip(batch_ids, batch_texts)))
        encode_and_upsert_batch(client, dataset.collection, batch_ids, batch_texts, model)
        progress.update(len(batch_texts))

    progress.close()
    collection_info = client.get_collection(dataset.collection)
    print(
        f"[✓] {dataset.name} upsert complete. "
        f"Collection now contains {collection_info.points_count} vectors"
    )


def main():
    """
    Main execution flow for MS MARCO (and optional WikiQA) sync to Qdrant.
    """
    print("=" * 80)
    print("Dataset Sync to Qdrant - Starting")
    print("=" * 80)
    
    # Check CUDA availability
    print(f"[*] CUDA available: {torch.cuda.is_available()}")
    if torch.cuda.is_available():
        print(f"[*] GPU: {torch.cuda.get_device_name(0)}")
        print(f"[*] CUDA Version: {torch.version.cuda}")
    else:
        print("[!] Warning: CUDA not available. CPU encoding will be very slow.")
    
    print()
    
    # Step 1: Initialize embedding model
    print(f"[*] Loading embedding model '{EMBEDDING_MODEL}'...")
    print("    This may take 1-2 minutes on the first run...")
    model = BGEM3FlagModel(EMBEDDING_MODEL, use_fp16=torch.cuda.is_available())
    print("[✓] Embedding model loaded successfully")

    print()

    # Step 2: Open SQLite database
    conn = open_sqlite_db()

    print()

    # Step 2.1: Ensure WikiQA SQLite exists if needed
    if INCLUDE_WIKIQA:
        wikiqa_path = Path(WIKIQA_DB_PATH)
        if not wikiqa_path.exists():
            if AUTO_FETCH_WIKIQA:
                build_wikiqa_sqlite(str(wikiqa_path))
            else:
                raise FileNotFoundError(
                    f"WikiQA SQLite not found at {wikiqa_path}. Set WIKIQA_DB_PATH or enable AUTO_FETCH_WIKIQA."
                )

    print()

    # Step 3: Connect to Qdrant (Node B)
    print(f"[*] Connecting to Qdrant at {QDRANT_HOST}:{QDRANT_PORT}...")
    client = connect_qdrant()
    print("[✓] Connected to Qdrant successfully")

    # Step 4: Ingest datasets
    for dataset in DATASETS:
        process_dataset(client, conn, model, dataset)
        print()

    conn.close()

    print("=" * 80)
    print("Dataset Sync Complete!")
    print("=" * 80)
    print("Summary:")
    print(f"  - Datasets ingested: {', '.join(d.name for d in DATASETS)}")
    print(f"  - SQLite database: {CORPUS_DB_PATH}")
    print()


if __name__ == "__main__":
    main()