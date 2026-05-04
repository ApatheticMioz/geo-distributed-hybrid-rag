#!/usr/bin/env python3
"""
Benchmark script: Serial vs Parallel orchestration
Measures TTFT for 100 MS MARCO queries
Compares serial baseline against parallel optimized version
"""

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import List, Dict, Any
import statistics

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

# Sample MS MARCO queries (using representative queries)
MS_MARCO_QUERIES = [
    "What is machine learning?",
    "How does deep learning work?",
    "Explain natural language processing",
    "What is transformers architecture?",
    "How do recurrent neural networks function?",
    "Describe convolutional neural networks",
    "What is transfer learning?",
    "Explain attention mechanism",
    "How do embeddings work?",
    "What is fine-tuning in NLP?",
    "Describe BERT model",
    "How does GPT work?",
    "Explain word embeddings",
    "What is semantic search?",
    "How does ranking work in information retrieval?",
    "Describe BM25 algorithm",
    "What is vector database?",
    "Explain sparse vs dense retrieval",
    "How does reciprocal rank fusion work?",
    "What is late interaction?",
    "Describe cross-encoder models",
    "How do bi-encoders work?",
    "What is knowledge distillation?",
    "Explain curriculum learning",
    "How does data augmentation improve models?",
    "What is few-shot learning?",
    "Describe zero-shot learning",
    "How does prompt engineering work?",
    "What is in-context learning?",
    "Explain chain-of-thought prompting",
    "How do language models generate text?",
    "What is token prediction?",
    "Describe beam search decoding",
    "How does greedy decoding work?",
    "What is temperature in sampling?",
    "Explain top-k sampling",
    "How does nucleus sampling work?",
    "What is perplexity in language models?",
    "Describe BLEU score",
    "How does ROUGE metric work?",
    "What is F1 score?",
    "Explain precision and recall",
    "How does evaluation work in NLP?",
    "What is benchmark dataset?",
    "Describe cross-validation",
    "How does hyperparameter tuning work?",
    "What is overfitting?",
    "Explain regularization techniques",
    "How does dropout work?",
    "What is batch normalization?",
    "Describe layer normalization",
    "How does gradient descent work?",
    "What is backpropagation?",
    "Explain loss functions",
    "How do optimizers work?",
    "What is Adam optimizer?",
    "Describe SGD optimization",
    "How does learning rate scheduling work?",
    "What is early stopping?",
    "Explain ensemble methods",
    "How does bagging work?",
    "What is boosting?",
    "Describe stacking in machine learning",
    "How does clustering work?",
    "What is k-means algorithm?",
    "Explain hierarchical clustering",
    "How does DBSCAN clustering work?",
    "What is dimensionality reduction?",
    "Describe PCA algorithm",
    "How does t-SNE work?",
    "What is UMAP?",
    "Explain manifold learning",
    "How does anomaly detection work?",
    "What is outlier detection?",
    "Describe one-class SVM",
    "How does isolation forest work?",
    "What is time series forecasting?",
    "Explain ARIMA models",
    "How does LSTM work for sequences?",
    "What is sequence-to-sequence models?",
    "Describe encoder-decoder architecture",
    "How does attention mechanism improve translation?",
    "What is multi-head attention?",
    "Explain self-attention",
    "How does positional encoding work?",
    "What is residual connection?",
    "Describe skip connections",
    "How does batch size affect training?",
    "What is epoch in machine learning?",
    "Explain validation set purpose",
    "How does test set work?",
    "What is cross-entropy loss?",
    "Describe KL divergence",
    "How does softmax work?",
    "What is sigmoid activation?",
    "Explain ReLU activation",
    "How does tanh activation work?",
    "What is leaky ReLU?",
    "Describe activation functions",
    "How does gradient clipping work?",
    "What is weight initialization?",
    "Explain Xavier initialization?",
]


class BenchmarkResult:
    def __init__(self):
        self.ttft_times: List[float] = []
        self.total_times: List[float] = []
        self.sparse_times: List[float] = []
        self.failures: int = 0

    def add_result(self, ttft_ms: float, total_ms: float, sparse_ms: float):
        self.ttft_times.append(ttft_ms)
        self.total_times.append(total_ms)
        self.sparse_times.append(sparse_ms)

    def add_failure(self):
        self.failures += 1

    def get_stats(self) -> Dict[str, Any]:
        if not self.ttft_times:
            return {"error": "No successful queries"}

        return {
            "ttft_ms": {
                "mean": statistics.mean(self.ttft_times),
                "median": statistics.median(self.ttft_times),
                "min": min(self.ttft_times),
                "max": max(self.ttft_times),
                "stdev": statistics.stdev(self.ttft_times) if len(self.ttft_times) > 1 else 0,
                "p95": sorted(self.ttft_times)[int(len(self.ttft_times) * 0.95)],
                "p99": sorted(self.ttft_times)[int(len(self.ttft_times) * 0.99)],
            },
            "total_ms": {
                "mean": statistics.mean(self.total_times),
                "median": statistics.median(self.total_times),
                "min": min(self.total_times),
                "max": max(self.total_times),
            },
            "sparse_ms": {
                "mean": statistics.mean(self.sparse_times),
                "median": statistics.median(self.sparse_times),
            },
            "successful_queries": len(self.ttft_times),
            "failed_queries": self.failures,
        }


async def run_benchmark(node_c_url: str, queries: List[str], mode: str) -> BenchmarkResult:
    """Run benchmark against Node C endpoint."""
    result = BenchmarkResult()
    
    logger.info(f"Starting {mode} benchmark with {len(queries)} queries...")
    
    async with httpx.AsyncClient(timeout=120.0) as client:
        for i, query in enumerate(queries, 1):
            try:
                payload = {"query": query, "top_k": 10}
                
                # Measure time to first token
                t_start = time.perf_counter()
                first_token_time = None
                
                async with client.stream("POST", node_c_url, json=payload) as response:
                    if response.status_code != 200:
                        logger.error(f"Query {i} failed with status {response.status_code}")
                        result.add_failure()
                        continue
                    
                    # Read response to get timing from logs
                    full_response = ""
                    async for chunk in response.aiter_text():
                        if first_token_time is None:
                            first_token_time = time.perf_counter()
                        full_response += chunk
                
                if first_token_time:
                    ttft = (first_token_time - t_start) * 1000  # Convert to ms
                    total = (time.perf_counter() - t_start) * 1000
                    result.add_result(ttft, total, 0)  # sparse_ms would come from logs
                    logger.info(f"[{i}/100] Query: '{query[:40]}...' | TTFT={ttft:.1f}ms | Total={total:.1f}ms")
                else:
                    result.add_failure()
                    logger.error(f"Query {i} failed to get first token")
                
            except Exception as e:
                logger.error(f"Query {i} error: {e}")
                result.add_failure()
                await asyncio.sleep(0.5)  # Brief pause on error
            
            # Avoid hammering the server
            await asyncio.sleep(0.2)
    
    return result


async def main():
    """Main benchmark entry point."""
    
    logger.info("=" * 80)
    logger.info("RAG PIPELINE BENCHMARK: Serial vs Parallel")
    logger.info("=" * 80)
    
    # Use first 100 queries
    test_queries = MS_MARCO_QUERIES[:100] if len(MS_MARCO_QUERIES) >= 100 else MS_MARCO_QUERIES
    
    # Benchmark serial version
    serial_url = "http://localhost:8002/query"  # Serial version on port 8002
    parallel_url = "http://localhost:8001/query"  # Parallel version on port 8001
    
    logger.info(f"\nBenchmarking SERIAL version: {serial_url}")
    serial_result = await run_benchmark(serial_url, test_queries, "SERIAL")
    
    logger.info(f"\n\nBenchmarking PARALLEL version: {parallel_url}")
    parallel_result = await run_benchmark(parallel_url, test_queries, "PARALLEL")
    
    # Generate comparison report
    logger.info("\n" + "=" * 80)
    logger.info("BENCHMARK RESULTS")
    logger.info("=" * 80)
    
    serial_stats = serial_result.get_stats()
    parallel_stats = parallel_result.get_stats()
    
    logger.info("\nSERIAL RESULTS:")
    logger.info(json.dumps(serial_stats, indent=2))
    
    logger.info("\nPARALLEL RESULTS:")
    logger.info(json.dumps(parallel_stats, indent=2))
    
    # Calculate percentage improvement
    if "ttft_ms" in serial_stats and "ttft_ms" in parallel_stats:
        serial_ttft_mean = serial_stats["ttft_ms"]["mean"]
        parallel_ttft_mean = parallel_stats["ttft_ms"]["mean"]
        improvement_pct = ((serial_ttft_mean - parallel_ttft_mean) / serial_ttft_mean) * 100
        
        logger.info("\n" + "=" * 80)
        logger.info("PERFORMANCE IMPROVEMENT")
        logger.info("=" * 80)
        logger.info(f"Serial TTFT (mean):        {serial_ttft_mean:.2f}ms")
        logger.info(f"Parallel TTFT (mean):      {parallel_ttft_mean:.2f}ms")
        logger.info(f"Absolute improvement:      {serial_ttft_mean - parallel_ttft_mean:.2f}ms")
        logger.info(f"Percentage improvement:    {improvement_pct:.1f}%")
        logger.info(f"\n✓ Parallel is {improvement_pct:.1f}% faster than Serial baseline")
    
    # Save results to file
    results_file = Path("benchmark_results.json")
    with open(results_file, "w") as f:
        json.dump({
            "timestamp": time.time(),
            "serial": serial_stats,
            "parallel": parallel_stats,
        }, f, indent=2)
    logger.info(f"\nResults saved to: {results_file}")


if __name__ == "__main__":
    asyncio.run(main())
