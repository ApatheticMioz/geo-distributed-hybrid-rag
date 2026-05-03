"""Benchmark Node C TTFT for sequential vs parallel mode.

Runs 50 dummy queries against a single Node C endpoint, alternating the query
mode so that 25 requests use sequential retrieval and 25 use parallel
retrieval. Optionally simulates WAN jitter by sending the
X-Simulate-WAN-Delay header.
"""

import asyncio
import logging
import statistics
import time
from argparse import ArgumentParser
from dataclasses import dataclass
from typing import Dict, List

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QuerySpec:
    query: str
    mode: str


def build_dummy_queries(count: int = 50) -> List[QuerySpec]:
    queries: List[QuerySpec] = []
    for index in range(count):
        mode = "sequential" if index % 2 == 0 else "parallel"
        queries.append(
            QuerySpec(
                query=f"dummy benchmark query {index + 1:02d}: what is retrieval case {index + 1}?",
                mode=mode,
            )
        )
    return queries


def percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]

    rank = (len(ordered) - 1) * (pct / 100.0)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


async def measure_ttft(
    client: httpx.AsyncClient,
    base_url: str,
    query: str,
    mode: str,
    top_k: int,
    simulate_wan_delay_ms: int,
) -> float:
    headers = {}
    if simulate_wan_delay_ms > 0:
        headers["X-Simulate-WAN-Delay"] = str(simulate_wan_delay_ms)

    start = time.perf_counter()
    async with client.stream(
        "POST",
        f"{base_url}/query",
        params={"mode": mode},
        json={"query": query, "top_k": top_k},
        headers=headers,
    ) as response:
        if response.status_code != 200:
            body = await response.aread()
            raise RuntimeError(f"HTTP {response.status_code}: {body.decode('utf-8', errors='ignore')}")

        async for chunk in response.aiter_text():
            if chunk:
                return (time.perf_counter() - start) * 1000

    raise RuntimeError("No streamed chunk received")


async def run_benchmark(
    base_url: str,
    queries: List[QuerySpec],
    top_k: int,
    simulate_wan_delay_ms: int,
) -> Dict[str, Dict[str, float]]:
    results: Dict[str, List[float]] = {"sequential": [], "parallel": []}
    errors = 0

    async with httpx.AsyncClient(timeout=120.0) as client:
        for index, spec in enumerate(queries, 1):
            try:
                ttft_ms = await measure_ttft(
                    client=client,
                    base_url=base_url,
                    query=spec.query,
                    mode=spec.mode,
                    top_k=top_k,
                    simulate_wan_delay_ms=simulate_wan_delay_ms,
                )
                results[spec.mode].append(ttft_ms)
                logger.info(
                    "[%02d/%02d] mode=%s ttft=%.2fms query='%s'",
                    index,
                    len(queries),
                    spec.mode,
                    ttft_ms,
                    spec.query[:60],
                )
            except Exception as exc:
                errors += 1
                logger.error("[%02d/%02d] mode=%s failed: %s", index, len(queries), spec.mode, exc)

    summary: Dict[str, Dict[str, float]] = {}
    for mode, values in results.items():
        summary[mode] = {
            "count": float(len(values)),
            "p50_ttft_ms": percentile(values, 50),
            "p95_ttft_ms": percentile(values, 95),
            "mean_ttft_ms": statistics.fmean(values) if values else 0.0,
        }

    summary["errors"] = {
        "count": float(errors),
        "p50_ttft_ms": 0.0,
        "p95_ttft_ms": 0.0,
        "mean_ttft_ms": 0.0,
    }
    return summary


def print_summary(summary: Dict[str, Dict[str, float]], total_queries: int, simulate_wan_delay_ms: int) -> None:
    print()
    print("=" * 74)
    print("Node C TTFT Benchmark")
    print("=" * 74)
    print(f"Total queries: {total_queries}")
    print(
        f"WAN delay header: {simulate_wan_delay_ms} ms"
        if simulate_wan_delay_ms > 0
        else "WAN delay header: disabled"
    )
    print()
    print(f"{'Mode':<12} {'Count':>7} {'P50 TTFT (ms)':>15} {'P95 TTFT (ms)':>15} {'Mean TTFT (ms)':>16}")
    print("-" * 74)
    for mode in ("sequential", "parallel"):
        stats = summary[mode]
        print(
            f"{mode:<12} {int(stats['count']):>7} {stats['p50_ttft_ms']:>15.2f} {stats['p95_ttft_ms']:>15.2f} {stats['mean_ttft_ms']:>16.2f}"
        )
    print("-" * 74)
    print(f"Errors: {int(summary['errors']['count'])}")


async def main() -> Dict[str, Dict[str, float]]:
    parser = ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000", help="Node C base URL")
    parser.add_argument("--top-k", type=int, default=10, help="top_k request value")
    parser.add_argument(
        "--simulate-wan-delay-ms",
        type=int,
        default=0,
        help="Send X-Simulate-WAN-Delay header in milliseconds",
    )
    args = parser.parse_args()

    queries = build_dummy_queries(50)
    summary = await run_benchmark(
        base_url=args.base_url,
        queries=queries,
        top_k=args.top_k,
        simulate_wan_delay_ms=args.simulate_wan_delay_ms,
    )
    print_summary(summary, len(queries), args.simulate_wan_delay_ms)
    return summary


if __name__ == "__main__":
    asyncio.run(main())