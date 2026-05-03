"""
gRPC Server for Node B - Dense Retrieval Engine
Handles dense retrieval queries using BGE-M3 embeddings and Qdrant
"""

import logging
import os
from concurrent import futures
from typing import Optional

import grpc
import numpy as np
from FlagEmbedding import BGEM3FlagModel
from qdrant_client import QdrantClient
from qdrant_client.models import PointIdList

# Import generated proto modules
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import retrieval_pb2
import retrieval_pb2_grpc

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
QDRANT_HOST = os.environ.get("QDRANT_HOST", "10.8.0.2")
QDRANT_PORT = int(os.environ.get("QDRANT_PORT", "6333"))
COLLECTION_NAME = "msmarco_passages"
TOP_K = 10
MODEL_NAME = "BAAI/bge-m3"
SERVER_PORT = 50051

# Global instances (loaded on startup)
model: Optional[BGEM3FlagModel] = None
qdrant_client: Optional[QdrantClient] = None


def initialize_globals():
    """Initialize global model and Qdrant client."""
    global model, qdrant_client
    
    try:
        logger.info(f"Loading {MODEL_NAME} with FP16 precision...")
        model = BGEM3FlagModel(MODEL_NAME, use_fp16=True)
        logger.info("Model loaded successfully")
        
        logger.info(f"Connecting to Qdrant at {QDRANT_HOST}:{QDRANT_PORT}...")
        qdrant_client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
        logger.info("Qdrant client connected successfully")
        
    except Exception as e:
        logger.error(f"Failed to initialize globals: {e}", exc_info=True)
        raise


class DenseRetrievalServicer(retrieval_pb2_grpc.DenseRetrievalServicer):
    """Implements the DenseRetrieval service."""
    
    def Retrieve(self, request: retrieval_pb2.QueryRequest, context: grpc.ServicerContext) -> retrieval_pb2.RetrievalResponse:
        """
        Retrieve top-k documents based on dense similarity.
        
        Args:
            request: QueryRequest containing the query string
            context: gRPC service context
            
        Returns:
            RetrievalResponse with doc_ids and scores
        """
        try:
            query = request.query
            logger.info(f"Received query: {query[:100]}...")
            
            if not query.strip():
                logger.warning("Received empty query")
                return retrieval_pb2.RetrievalResponse(doc_ids=[], scores=[])
            
            # Encode query
            logger.debug("Encoding query...")
            query_embedding = model.encode([query], return_dense=True)
            query_vector = np.array(query_embedding["dense_vecs"][0], dtype=np.float32)
            
            # Search Qdrant
            logger.debug(f"Searching for top-{TOP_K} matches in '{COLLECTION_NAME}'...")
            search_results = qdrant_client.search(
                collection_name=COLLECTION_NAME,
                query_vector=query_vector.tolist(),
                limit=TOP_K
            )
            
            # Extract doc_ids and scores
            doc_ids = []
            scores = []
            for result in search_results:
                doc_id = result.payload.get("doc_id")
                score = result.score
                doc_ids.append(doc_id)
                scores.append(score)
                logger.debug(f"  Doc: {doc_id}, Score: {score:.4f}")
            
            logger.info(f"Retrieved {len(doc_ids)} documents")
            
            return retrieval_pb2.RetrievalResponse(
                doc_ids=doc_ids,
                scores=scores
            )
            
        except Exception as e:
            logger.error(f"Error during retrieval: {e}", exc_info=True)
            context.set_details(str(e))
            context.set_code(grpc.StatusCode.INTERNAL)
            return retrieval_pb2.RetrievalResponse(doc_ids=[], scores=[])


def serve():
    """Start the gRPC server."""
    logger.info("Initializing server resources...")
    initialize_globals()
    
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    retrieval_pb2_grpc.add_DenseRetrievalServicer_to_server(
        DenseRetrievalServicer(), server
    )
    server.add_insecure_port(f"0.0.0.0:{SERVER_PORT}")
    
    logger.info(f"Starting gRPC server on 0.0.0.0:{SERVER_PORT}...")
    server.start()
    logger.info("Server started successfully. Listening for requests...")
    
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("Shutting down server...")
        server.stop(0)
        logger.info("Server stopped")


if __name__ == "__main__":
    serve()
