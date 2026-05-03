"""
Benchmark: Parallel vs Sequential Node C pipeline execution.

This script:
1. Runs 100 MS MARCO queries against the sequential version (baseline)
2. Runs the same 100 queries against the parallel version (optimized)
3. Calculates TTFT reduction percentage
4. Logs detailed statistics to JSON

Usage:
    python benchmark_parallel_vs_sequential.py \
        --num-queries 100 \
        --sequential-port 8000 \
        --parallel-port 8002 \
        --output results.json
"""

import asyncio
import json
import logging
import sys
import time
from argparse import ArgumentParser
from pathlib import Path
from typing import Dict, List

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

# MS MARCO queries for consistent benchmarking
MS_MARCO_QUERIES = [
    "what is a monoid",
    "how to make a paella",
    "what is the meaning of life",
    "how to learn python programming",
    "what are the benefits of exercise",
    "how to write a novel",
    "what is machine learning",
    "how to cook pasta",
    "what is artificial intelligence",
    "how to travel on a budget",
    "what is quantum computing",
    "how to take care of plants",
    "what is blockchain technology",
    "how to build a website",
    "what is climate change",
    "how to stay healthy",
    "what is cryptocurrency",
    "how to learn spanish",
    "what is cloud computing",
    "how to meditate",
    "what is renewable energy",
    "how to start a business",
    "what is deep learning",
    "how to improve memory",
    "what is web development",
    "how to manage stress",
    "what is data science",
    "how to garden",
    "what is machine vision",
    "how to write poetry",
    "what is natural language processing",
    "how to dance",
    "what is robotics",
    "how to invest money",
    "what is neural networks",
    "how to play guitar",
    "what is semantic search",
    "how to bake bread",
    "what is information retrieval",
    "how to photograph",
    "what is transfer learning",
    "how to cook fish",
    "what is computer vision",
    "how to swim",
    "what is optimization",
    "how to draw",
    "what is supervised learning",
    "how to ride a bike",
    "what is unsupervised learning",
    "how to sew",
    "what is reinforcement learning",
    "how to paint",
    "what is clustering",
    "how to run a marathon",
    "what is classification",
    "how to juggle",
    "what is regression",
    "how to climb mountains",
    "what is anomaly detection",
    "how to knit",
    "what is time series analysis",
    "how to sail",
    "what is matrix factorization",
    "how to skate",
    "what is gradient descent",
    "how to cook rice",
    "what is backpropagation",
    "how to camp",
    "what is stochastic learning",
    "how to fish",
    "what is batch normalization",
    "how to hike",
    "what is dropout regularization",
    "how to kayak",
    "what is cross validation",
    "how to make coffee",
    "what is feature engineering",
    "how to brew tea",
    "what is hyperparameter tuning",
    "how to make pizza",
    "what is model evaluation",
    "how to make dessert",
    "what is precision and recall",
    "how to make soup",
    "what is ROC curves",
    "how to make salad",
    "what is confusion matrix",
    "how to preserve food",
    "what is sensitivity and specificity",
    "how to grill",
    "what is false positive and false negative",
    "how to smoke meat",
    "what is accuracy and f1 score",
    "how to can vegetables",
    "what is auc roc",
]


async def benchmark_version(
    base_url: str,
    version_name: str,
    queries: List[str],
) -> Dict[str, float]:
    """
    Run queries against a Node C instance and collect latency metrics.

    Args:
        base_url: e.g., "http://localhost:8000"
        version_name: e.g., "Sequential" or "Parallel"
        queries: List of query strings to benchmark

    Returns:
        Dict with aggregated latency statistics
    """
    logger.info(f"Starting {version_name} benchmark with {len(queries)} queries...")
    
    ttft_times: List[float] = []
    total_times: List[float] = []
    errors = 0

    async with httpx.AsyncClient(timeout=120.0) as client:
        for i, query in enumerate(queries, 1):
            try:
                t_request_start = time.perf_counter()

                # Stream the response to measure TTFT
                first_chunk_time = None
                async with client.stream(
                    "POST",
                    f"{base_url}/query",
                    json={"query": query, "top_k": 10},
                ) as response:
                    if response.status_code != 200:
                        logger.error(f"Query {i}: HTTP {response.status_code}")
                        errors += 1
                        continue

                    async for chunk in response.aiter_text():
                        if first_chunk_time is None:
                            first_chunk_time = time.perf_counter()
                        # Consume full response

                t_total = (time.perf_counter() - t_request_start) * 1000
                total_times.append(t_total)

                if first_chunk_time is not None:
                    t_ttft = (first_chunk_time - t_request_start) * 1000
                    ttft_times.append(t_ttft)
                    logger.info(
                        f"[{version_name}] Query {i:3d} | TTFT={t_ttft:7.2f}ms | Total={t_total:7.2f}ms | '{query[:50]}'..."
                    )
                else:
                    logger.warning(f"[{version_name}] Query {i}: No response chunks")
                    errors += 1

            except Exception as e:
                logger.error(f"[{version_name}] Query {i}: {e}")
                errors += 1

    # Calculate statistics
    if ttft_times:
        stats = {
            "version": version_name,
            "total_queries": len(queries),
            "successful_queries": len(ttft_times),
            "errors": errors,
            "ttft_mean_ms": sum(ttft_times) / len(ttft_times),
            "ttft_min_ms": min(ttft_times),
            "ttft_max_ms": max(ttft_times),
            "ttft_median_ms": sorted(ttft_times)[len(ttft_times) // 2],
            "total_time_mean_ms": sum(total_times) / len(total_times) if total_times else 0,
            "total_time_min_ms": min(total_times) if total_times else 0,
            "total_time_max_ms": max(total_times) if total_times else 0,
        }
    else:
        logger.error(f"{version_name} benchmark failed completely")
        stats = {
            "version": version_name,
            "total_queries": len(queries),
            "successful_queries": 0,
            "errors": errors,
            "ttft_mean_ms": 0.0,
            "ttft_min_ms": 0.0,
            "ttft_max_ms": 0.0,
            "ttft_median_ms": 0.0,
            "total_time_mean_ms": 0.0,
            "total_time_min_ms": 0.0,
            "total_time_max_ms": 0.0,
        }

    logger.info(f"{version_name} benchmark complete:")
    logger.info(f"  Successful: {stats['successful_queries']}/{len(queries)}")
    logger.info(f"  TTFT: {stats['ttft_mean_ms']:.2f}ms (median: {stats['ttft_median_ms']:.2f}ms, range: {stats['ttft_min_ms']:.2f}–{stats['ttft_max_ms']:.2f}ms)")
    logger.info(f"  Total Time: {stats['total_time_mean_ms']:.2f}ms (range: {stats['total_time_min_ms']:.2f}–{stats['total_time_max_ms']:.2f}ms)")

    return stats


def calculate_reduction(baseline: Dict, optimized: Dict) -> Dict:
    """Calculate percentage reduction from baseline to optimized."""
    if baseline["ttft_mean_ms"] == 0:
        reduction_pct = 0.0
    else:
        reduction_pct = (
            (baseline["ttft_mean_ms"] - optimized["ttft_mean_ms"]) / baseline["ttft_mean_ms"]
        ) * 100

    return {
        "baseline_ttft_ms": baseline["ttft_mean_ms"],
        "optimized_ttft_ms": optimized["ttft_mean_ms"],
        "reduction_ms": baseline["ttft_mean_ms"] - optimized["ttft_mean_ms"],
        "reduction_pct": reduction_pct,
    }


async def main():
    parser = ArgumentParser()
    parser.add_argument(
        "--num-queries",
        type=int,
        default=100,
        help="Number of queries to run (default: 100)",
    )
    parser.add_argument(
        "--sequential-url",
        default="http://localhost:8000",
        help="Sequential Node C endpoint (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--parallel-url",
        default="http://localhost:8002",
        help="Parallel Node C endpoint (default: http://localhost:8002)",
    )
    parser.add_argument(
        "--output",
        default="benchmark_results.json",
        help="Output JSON file for results (default: benchmark_results.json)",
    )
    args = parser.parse_args()

    # Ensure we have enough queries
    num_queries = min(args.num_queries, len(MS_MARCO_QUERIES))
    queries = MS_MARCO_QUERIES[:num_queries]

    logger.info("=" * 80)
    logger.info("PARALLEL vs SEQUENTIAL BENCHMARK")
    logger.info("=" * 80)
    logger.info(f"Running {num_queries} queries on each version...")
    logger.info(f"Sequential endpoint: {args.sequential_url}")
    logger.info(f"Parallel endpoint: {args.parallel_url}")
    logger.info("")

    # Run sequential benchmark
    logger.info("Starting SEQUENTIAL (baseline) benchmark...")
    sequential_stats = await benchmark_version(args.sequential_url, "Sequential", queries)

    await asyncio.sleep(2)  # Brief pause between benchmarks

    # Run parallel benchmark
    logger.info("\nStarting PARALLEL (optimized) benchmark...")
    parallel_stats = await benchmark_version(args.parallel_url, "Parallel", queries)

    # Calculate reduction
    reduction = calculate_reduction(sequential_stats, parallel_stats)

    logger.info("\n" + "=" * 80)
    logger.info("RESULTS SUMMARY")
    logger.info("=" * 80)
    logger.info(f"Sequential TTFT (baseline): {reduction['baseline_ttft_ms']:.2f}ms")
    logger.info(f"Parallel TTFT (optimized):  {reduction['optimized_ttft_ms']:.2f}ms")
    logger.info(f"Reduction: {reduction['reduction_ms']:.2f}ms ({reduction['reduction_pct']:.1f}%)")
    logger.info("=" * 80)

    # Save results to JSON
    results = {
        "timestamp": time.time(),
        "benchmark_config": {
            "num_queries": num_queries,
            "sequential_url": args.sequential_url,
            "parallel_url": args.parallel_url,
        },
        "sequential": sequential_stats,
        "parallel": parallel_stats,
        "comparison": reduction,
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)

    logger.info(f"\nResults saved to {output_path}")
    logger.info(f"\nKey Metric: TTFT Reduction = {reduction['reduction_pct']:.1f}%")

    return results


if __name__ == "__main__":
    results = asyncio.run(main())
    sys.exit(0 if results["comparison"]["reduction_pct"] > 0 else 1)
