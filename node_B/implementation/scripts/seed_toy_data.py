"""
Seed Toy Dataset for Testing Hybrid RAG (2-Node Architecture).

Generates ~100 mock documents covering NLP/AI/ML topics and:
  1. Saves corpus.sqlite into node_A/implementation/ for text hydration.
  2. Embeds passages with BGE-M3 and upserts into Qdrant on Node B.
  3. Builds a Tantivy BM25 index at node_b/implementation/data/tantivy_index.

Usage (from Node B's venv):
    cd node_B/implementation
    python scripts/seed_toy_data.py
"""

import hashlib
import json
import logging
import os
import sqlite3
import sys
import uuid
from pathlib import Path

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ============================================================================
# Paths
# ============================================================================

SCRIPT_DIR = Path(__file__).resolve().parent
IMPL_DIR = SCRIPT_DIR.parent  # node_B/implementation/
PROJECT_ROOT = IMPL_DIR.parent.parent  # Project_Laptop/ (grandparent of implementation/)

# Node A corpus DB (CRITICAL: Node A hydrates text from here)
NODE_A_CORPUS_DB = PROJECT_ROOT / "node_A" / "implementation" / "corpus.sqlite"

# BM25 index target
BM25_INDEX_DIR = IMPL_DIR / "data" / "tantivy_index"

# Qdrant config
QDRANT_HOST = os.environ.get("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.environ.get("QDRANT_PORT", "6333"))
COLLECTION_NAME = os.environ.get("QDRANT_COLLECTION", "toy_test")
VECTOR_SIZE = 1024  # BGE-M3 dense vector dimension


# ============================================================================
# Toy Document Generation
# ============================================================================

TOPICS = [
    {
        "category": "Natural Language Processing",
        "documents": [
            "Transformer architectures use self-attention mechanisms to process sequential data in parallel.",
            "BERT (Bidirectional Encoder Representations from Transformers) was pre-trained on masked language modeling tasks.",
            "GPT models generate text autoregressively by predicting the next token in a sequence.",
            "Tokenization is the process of splitting raw text into smaller units called tokens.",
            "Named Entity Recognition (NER) identifies and classifies entities like persons, organizations, and locations.",
            "Sentiment analysis determines the emotional tone behind a body of text.",
            "Word embeddings like Word2Vec and GloVe map words to dense vector spaces.",
            "Sequence-to-sequence models are used for machine translation and text summarization tasks.",
            "Attention mechanisms allow models to weigh the importance of different input positions.",
            "Fine-tuning involves adapting a pre-trained model to a specific downstream task.",
        ],
    },
    {
        "category": "Machine Learning Fundamentals",
        "documents": [
            "Supervised learning trains models on labeled data to predict outputs for new inputs.",
            "Unsupervised learning finds hidden patterns in data without explicit labels.",
            "Reinforcement learning agents learn optimal policies by interacting with environments.",
            "Gradient descent iteratively minimizes a loss function by following the negative gradient.",
            "Overfitting occurs when a model memorizes training data and fails to generalize.",
            "Cross-validation splits data into multiple folds to estimate model performance robustly.",
            "Regularization techniques like L1 and L2 prevent models from learning spurious patterns.",
            "Feature engineering transforms raw data into meaningful inputs for machine learning models.",
            "The bias-variance tradeoff balances underfitting and overfitting in model selection.",
            "Ensemble methods combine multiple models to improve prediction accuracy and robustness.",
        ],
    },
    {
        "category": "Deep Learning",
        "documents": [
            "Convolutional Neural Networks (CNNs) excel at processing grid-like data such as images.",
            "Recurrent Neural Networks (RNNs) maintain hidden states to model sequential dependencies.",
            "Long Short-Term Memory (LSTM) networks solve the vanishing gradient problem in RNNs.",
            "Batch normalization stabilizes training by normalizing layer inputs during each batch.",
            "Dropout randomly deactivates neurons during training to prevent co-adaptation.",
            "Transfer learning reuses knowledge from pre-trained models on related tasks.",
            "Generative Adversarial Networks (GANs) use a generator and discriminator in adversarial training.",
            "Variational Autoencoders (VAEs) learn latent representations by combining inference with generation.",
            "Graph Neural Networks operate on graph-structured data using message passing.",
            "Self-supervised learning creates training signals from unlabeled data through pretext tasks.",
        ],
    },
    {
        "category": "Information Retrieval",
        "documents": [
            "BM25 is a probabilistic ranking function that scores documents based on term frequency and inverse document frequency.",
            "Dense retrieval uses neural network embeddings to find semantically similar documents.",
            "Hybrid retrieval combines sparse and dense signals for more robust document ranking.",
            "Reciprocal Rank Fusion (RRF) merges ranked lists by summing inverse rank scores.",
            "Vector databases like Qdrant store and index high-dimensional embeddings for fast similarity search.",
            "Inverted indexes map terms to the documents containing them, enabling efficient keyword search.",
            "Query expansion reformulates user queries to improve retrieval recall.",
            "Re-ranking models rescore initial retrieval results using more expensive cross-encoders.",
            "Passage-level retrieval splits documents into smaller chunks for finer-grained matching.",
            "The k-nearest neighbors (k-NN) algorithm finds the most similar vectors in embedding space.",
        ],
    },
    {
        "category": "Computer Vision",
        "documents": [
            "Image classification assigns a single label to an input image from a fixed set of categories.",
            "Object detection localizes and classifies multiple objects within an image using bounding boxes.",
            "Semantic segmentation assigns a class label to every pixel in an image.",
            "ResNet architectures use residual connections to train very deep neural networks effectively.",
            "Data augmentation techniques like rotation, flipping, and cropping improve model generalization.",
            "Image captioning generates natural language descriptions of visual content.",
            "Visual Question Answering (VQA) systems answer questions about images using combined vision and language.",
            "Feature pyramids capture multi-scale representations for detection at different object sizes.",
            "Style transfer applies the artistic style of one image to the content of another.",
            "Neural radiance fields (NeRF) reconstruct 3D scenes from 2D image collections.",
        ],
    },
]


def generate_toy_documents(num_per_topic: int = 10) -> list[dict]:
    """Generate toy documents with deterministic IDs."""
    docs = []
    for topic in TOPICS:
        for i, text in enumerate(topic["documents"]):
            # Deterministic doc_id from content hash
            content_hash = hashlib.md5(text.encode()).hexdigest()[:12]
            doc_id = f"toy_{content_hash}"
            docs.append({
                "id": doc_id,
                "text": text,
                "category": topic["category"],
            })
    return docs


# ============================================================================
# SQLite Corpus (Node A)
# ============================================================================

def save_corpus_sqlite(docs: list[dict], db_path: Path) -> None:
    """Save documents to SQLite for Node A text hydration."""
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS passages (
            doc_id TEXT PRIMARY KEY,
            text TEXT
        )
    """)

    for doc in docs:
        cursor.execute(
            "INSERT OR REPLACE INTO passages (doc_id, text) VALUES (?, ?)",
            (doc["id"], doc["text"]),
        )

    conn.commit()
    conn.close()
    logger.info("Saved %d documents to SQLite at %s", len(docs), db_path)


# ============================================================================
# Qdrant Indexing (Node B - Dense)
# ============================================================================

def connect_qdrant() -> QdrantClient:
    """Connect to Qdrant with retries (REST only to avoid Docker Desktop gRPC issues)."""
    import time as _time
    for attempt in range(1, 11):
        try:
            # Force REST with generous timeout for Docker Desktop NAT latency on Windows
            client = QdrantClient(
                url=f"http://{QDRANT_HOST}:{QDRANT_PORT}",
                prefer_grpc=False,
                timeout=300,
            )
            client.get_collections()
            logger.info("Connected to Qdrant via REST on attempt %d", attempt)
            return client
        except Exception as exc:
            logger.warning("Qdrant connection attempt %d failed: %s", attempt, exc)
            if attempt < 10:
                _time.sleep(5)
    raise RuntimeError("Could not connect to Qdrant after 10 attempts")


def ensure_collection(client: QdrantClient) -> None:
    """Create the toy_test collection if it doesn't exist."""
    try:
        client.get_collection(COLLECTION_NAME)
        logger.info("Collection '%s' already exists", COLLECTION_NAME)
    except Exception:
        logger.info("Creating collection '%s'", COLLECTION_NAME)
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )


def embed_and_upsert(client: QdrantClient, docs: list[dict]) -> None:
    """
    Embed documents with BGE-M3 and upsert to Qdrant.
    Falls back to random vectors if GPU/model unavailable.
    """
    try:
        from FlagEmbedding import BGEM3FlagModel
        import torch

        logger.info("Loading BGE-M3 for embedding...")
        model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=torch.cuda.is_available())

        texts = [doc["text"] for doc in docs]
        embeddings = model.encode(texts, return_dense=True)["dense_vecs"]
        vectors = np.asarray(embeddings, dtype=np.float32)

    except Exception as exc:
        logger.warning(
            "BGE-M3 embedding failed (%s); falling back to random vectors for testing.", exc
        )
        vectors = np.random.rand(len(docs), VECTOR_SIZE).astype(np.float32)

    points = [
        PointStruct(
            id=int(uuid.uuid5(uuid.NAMESPACE_DNS, doc["id"]).int & 0xFFFFFFFFFFFFFFFF),
            vector=vectors[i].tolist(),
            payload={"doc_id": doc["id"]},
        )
        for i, doc in enumerate(docs)
    ]

    client.upsert(collection_name=COLLECTION_NAME, points=points, wait=True)
    logger.info("Upserted %d vectors to Qdrant collection '%s'", len(points), COLLECTION_NAME)


# ============================================================================
# Tantivy BM25 Index (Node B - Sparse)
# ============================================================================

def build_bm25_index(docs: list[dict], index_dir: Path) -> None:
    """Build a Tantivy BM25 index from documents."""
    try:
        import tantivy

        index_dir.mkdir(parents=True, exist_ok=True)

        # Tantivy 0.26+ API: SchemaBuilder + Index(schema, path=...)
        schema_builder = tantivy.SchemaBuilder()
        schema_builder.add_text_field("doc_id", stored=True)
        schema_builder.add_text_field("body", stored=True)
        schema = schema_builder.build()

        index = tantivy.Index(schema, path=str(index_dir))

        writer = index.writer()
        for doc in docs:
            document = tantivy.Document(doc_id=doc["id"], body=doc["text"])
            writer.add_document(document)
        writer.commit()
        index.reload()  # Required in tantivy 0.26+ to see committed data

        searcher = index.searcher()
        logger.info(
            "Built Tantivy BM25 index at %s with %d docs",
            index_dir,
            searcher.num_docs,
        )

    except ImportError:
        logger.warning(
            "Tantivy not installed; BM25 index not built. "
            "Install with: pip install tantivy"
        )


# ============================================================================
# Main
# ============================================================================

def main() -> None:
    print("=" * 60)
    print("Toy Dataset Seeding for 2-Node Hybrid RAG")
    print("=" * 60)

    # Step 1: Generate documents
    docs = generate_toy_documents()
    logger.info("Generated %d toy documents", len(docs))

    # Step 2: Save SQLite corpus (for Node A text hydration)
    save_corpus_sqlite(docs, NODE_A_CORPUS_DB)

    # Step 3: Index into Qdrant (for Node B dense retrieval)
    logger.info("Connecting to Qdrant at %s:%d", QDRANT_HOST, QDRANT_PORT)
    client = connect_qdrant()
    ensure_collection(client)
    embed_and_upsert(client, docs)

    # Step 4: Build Tantivy BM25 index (for Node B sparse retrieval)
    build_bm25_index(docs, BM25_INDEX_DIR)

    # Summary
    print()
    print("=" * 60)
    print("Seeding Complete!")
    print("=" * 60)
    print(f"  Documents:     {len(docs)}")
    print(f"  SQLite corpus: {NODE_A_CORPUS_DB}")
    print(f"  Qdrant coll:   {COLLECTION_NAME} @ {QDRANT_HOST}:{QDRANT_PORT}")
    print(f"  BM25 index:    {BM25_INDEX_DIR}")


if __name__ == "__main__":
    main()