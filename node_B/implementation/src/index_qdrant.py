"""
Offline Indexing Script for Node B
Indexes the corpus into Qdrant Vector Database
"""

import logging
import sqlite3
from pathlib import Path
from typing import List, Tuple

import numpy as np
from FlagEmbedding import BGEM3FlagModel
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
QDRANT_HOST = "localhost"
QDRANT_PORT = 6333
COLLECTION_NAME = "msmarco_passages"
VECTOR_SIZE = 1024
BATCH_SIZE = 128
MODEL_NAME = "BAAI/bge-m3"
DATASET_PATH = Path(__file__).parent.parent / "corpus.sqlite"


def load_model() -> BGEM3FlagModel:
    """Load BGE-M3 model with FP16 for memory efficiency."""
    logger.info(f"Loading {MODEL_NAME} with FP16 precision...")
    model = BGEM3FlagModel(MODEL_NAME, use_fp16=True)
    logger.info("Model loaded successfully")
    return model


def init_qdrant() -> QdrantClient:
    """Initialize Qdrant client and create collection."""
    logger.info(f"Connecting to Qdrant at {QDRANT_HOST}:{QDRANT_PORT}...")
    logger.info(f"Connecting to Qdrant at {QDRANT_HOST}...")
    client = QdrantClient(
        host=QDRANT_HOST, 
        port=QDRANT_PORT,
        grpc_port=6334,
        prefer_grpc=True  # Use the high-speed lane
    )
    
    # Check if collection exists
    try:
        collection_info = client.get_collection(COLLECTION_NAME)
        logger.info(f"Collection '{COLLECTION_NAME}' already exists. Skipping creation.")
    except Exception:
        # Collection doesn't exist, create it
        logger.info(f"Creating collection '{COLLECTION_NAME}'...")
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )
        logger.info(f"Collection '{COLLECTION_NAME}' created successfully")
    
    return client


def load_dataset() -> List[Tuple[str, str]]:
    """Load corpus.sqlite dataset."""
    if not DATASET_PATH.exists():
        raise FileNotFoundError(
            f"Dataset not found at {DATASET_PATH}. "
            "Please ensure corpus.sqlite exists in the implementation folder."
        )
    
    logger.info(f"Loading dataset from {DATASET_PATH}...")
    data = []
    
    conn = sqlite3.connect(str(DATASET_PATH))
    cursor = conn.cursor()
    cursor.execute("SELECT doc_id, text FROM passages LIMIT 1000000")
    
    for line_num, (doc_id, text) in enumerate(cursor.fetchall(), 1):
        data.append((doc_id, text))
        if line_num % 10000 == 0:
            logger.info(f"  Loaded {line_num} documents...")
    
    conn.close()
    logger.info(f"Total documents loaded: {len(data)}")
    return data


def index_corpus(model: BGEM3FlagModel, client: QdrantClient, data: List[Tuple[str, str]]):
    """Index corpus into Qdrant."""
    logger.info("Starting indexing process...")
    total_docs = len(data)
    
    for batch_start in range(0, total_docs, BATCH_SIZE):
        batch_end = min(batch_start + BATCH_SIZE, total_docs)
        batch = data[batch_start:batch_end]
        
        # Extract texts
        texts = [text for _, text in batch]
        doc_ids = [doc_id for doc_id, _ in batch]
        
        # Encode texts
        # Encode texts
        embeddings = model.encode(
            texts, 
            batch_size=BATCH_SIZE,  # Force BGE-M3 to use your 256 batch size!
            max_length=512,         # Stop the tokenizer from padding to 8192
            return_dense=True
        )
        dense_vecs = embeddings["dense_vecs"]
        
        # Create points for Qdrant (without storing text)
        points = []
        for idx, (doc_id, dense_vec) in enumerate(zip(doc_ids, dense_vecs)):
            # Convert to float32 if needed
            dense_vec = np.array(dense_vec, dtype=np.float32)
            
            point = PointStruct(
                id=int(batch_start + idx),  # Sequential ID
                vector=dense_vec.tolist(),
                payload={"doc_id": doc_id}  # Only store doc_id, not text
            )
            points.append(point)
        
        # Upsert to Qdrant
        client.upsert(
            collection_name=COLLECTION_NAME,
            points=points,
            wait=False  # Tell Python to fire-and-forget, don't wait for the SSD!
        )
        
        logger.info(f"Indexed batch {batch_end}/{total_docs}")
    
    logger.info("Indexing completed successfully")


def main():
    """Main execution function."""
    try:
        # Initialize
        model = load_model()
        client = init_qdrant()
        data = load_dataset()
        
        # Index
        index_corpus(model, client, data)
        
        # Verify
        collection_info = client.get_collection(COLLECTION_NAME)
        logger.info(f"Final collection info: {collection_info.points_count} points indexed")
        
    except Exception as e:
        logger.error(f"Error during indexing: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
