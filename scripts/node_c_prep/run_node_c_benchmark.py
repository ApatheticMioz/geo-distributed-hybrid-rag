#!/usr/bin/env python3
"""
run_node_c_benchmark.py — Node C cross-site benchmark client.

Stdlib-only (no third-party imports). From Node C (10.8.0.3) it queries the
Node B gateway ``POST /query/benchmark`` with the SPHP (Speculative
Progressive Hydration & Prefill) mode both disabled and enabled, and records
client-side RTT, TTFT, TPS, and total latency into
``benchmarks/results_node_c.json``.

The client measures what the *client* actually experiences:
  * client_rtt_ms  — a lightweight TCP round-trip probe to the gateway
  * ttft_ms        — time-to-first-token as reported by the gateway
  * tps            — decode throughput (tokens/second) as reported
  * total_ms       — end-to-end latency as reported by the gateway
  * client_total_ms— wall-clock time for the whole HTTP request, measured here

Usage examples
--------------
    # default: 10 built-in queries, both SPHP modes, 0 ms WAN delay
    python3 run_node_c_benchmark.py

    # full campaign shape: 50 queries, 40 ms one-way WAN delay, both modes
    python3 run_node_c_benchmark.py --queries experiments/bench/queries/latency_dev480_seed42.txt \
        --wan-delay 40 --both-modes

    # quick smoke test: 2 queries, SPHP enabled only
    python3 run_node_c_benchmark.py --n 2 --sphp-only
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Defaults (match the campaign topology)
# ---------------------------------------------------------------------------
DEFAULT_GATEWAY = "http://10.8.0.2:8000"
DEFAULT_OUT = "benchmarks/results_node_c.json"
DEFAULT_TOP_K = 10
DEFAULT_N = 10
DEFAULT_RTT_PROBES = 3

# A small built-in query set so the client runs with zero external files.
BUILTIN_QUERIES = [
    "what is the difference between weather and climate",
    "causes of the french revolution summary",
    "how does a transformer neural network work",
    "symptoms of acute appendicitis and diagnosis",
    "what is reciprocal rank fusion in information retrieval",
    "capital and largest city of australia",
    "how does photosynthesis produce glucose and oxygen",
    "history of the apollo space program",
    "difference between tcp and udp protocols",
    "how does quantum key distribution work",
]


# ---------------------------------------------------------------------------
# RTT probe
# ---------------------------------------------------------------------------
def measure_rtt(host: str, port: int, probes: int = 3) -> float:
    """Return the mean TCP connect round-trip time (ms) to host:port.

    A TCP connect is a single round trip (SYN -> SYN/ACK), so the connect
    time is a good lightweight RTT estimate that does not require a live
    HTTP server to be fully up. Returns ``float('nan')`` if unreachable.
    """
    samples: List[float] = []
    for _ in range(max(1, probes)):
        t0 = time.perf_counter()
        try:
            with socket.create_connection((host, port), timeout=3.0):
                pass
            samples.append((time.perf_counter() - t0) * 1000.0)
        except OSError:
            return float("nan")
    if not samples:
        return float("nan")
    return sum(samples) / len(samples)


# ---------------------------------------------------------------------------
# Query the gateway
# ---------------------------------------------------------------------------
def run_query(
    gateway: str,
    query: str,
    top_k: int,
    wan_delay_ms: int,
    sphp: bool,
    timeout: float = 120.0,
) -> Dict[str, Any]:
    """POST one query to ``/query/benchmark`` and return the parsed response.

    The SPHP mode is selected via the ``sphp`` field of the request body
    (the gateway only engages the speculative path when ``sphp`` is true and
    ``mode == "hybrid"``). The one-way WAN delay is injected through the
    ``X-Simulate-WAN-Delay`` header, matching the existing benchmark harness.
    """
    url = gateway.rstrip("/") + "/query/benchmark"
    body = json.dumps(
        {
            "query": query,
            "top_k": top_k,
            "mode": "hybrid",
            "sphp": sphp,
        }
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if wan_delay_ms > 0:
        headers["X-Simulate-WAN-Delay"] = str(wan_delay_ms)

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        client_total_ms = (time.perf_counter() - t0) * 1000.0
        payload["client_total_ms"] = round(client_total_ms, 2)
        return payload
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as e:
        return {"error": str(e), "query": query, "sphp": sphp}


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------
def _stats(values: List[float]) -> Dict[str, float]:
    if not values:
        return {"mean": 0.0, "std": 0.0, "p50": 0.0, "p95": 0.0,
                "min": 0.0, "max": 0.0}
    s = sorted(values)
    n = len(s)
    mean = sum(s) / n
    var = sum((x - mean) ** 2 for x in s) / n
    std = var ** 0.5
    p50 = s[int(round(0.50 * (n - 1)))]
    p95 = s[int(round(0.95 * (n - 1)))]
    return {
        "mean": round(mean, 2),
        "std": round(std, 2),
        "p50": round(p50, 2),
        "p95": round(p95, 2),
        "min": round(s[0], 2),
        "max": round(s[-1], 2),
    }


def _summarize(runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate per-query runs into mean/p50/p95 stats for the key metrics."""
    def field(r: Dict[str, Any], key: str) -> Optional[float]:
        t = r.get("timings", {})
        if key in t:
            return t[key]
        return r.get(key)

    ttft = [field(r, "ttft_ms") for r in runs if field(r, "ttft_ms") is not None]
    total = [field(r, "total_ms") for r in runs if field(r, "total_ms") is not None]
    tps = [r.get("decode_tps") for r in runs if r.get("decode_tps") is not None]
    client_total = [r.get("client_total_ms") for r in runs
                    if r.get("client_total_ms") is not None]
    return {
        "n": len(runs),
        "ttft_ms": _stats(ttft),
        "total_ms": _stats(total),
        "tps": _stats(tps),
        "client_total_ms": _stats(client_total),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Node C cross-site SPHP benchmark client (stdlib-only).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--gateway", default=DEFAULT_GATEWAY,
                   help="Node B base URL (host:port, no trailing path).")
    p.add_argument("--queries", default=None,
                   help="Path to a one-query-per-line file. "
                        "Defaults to the built-in 10-query set.")
    p.add_argument("--n", type=int, default=DEFAULT_N,
                   help="Number of queries to run per SPHP mode.")
    p.add_argument("--top-k", type=int, default=DEFAULT_TOP_K,
                   help="Retrieval depth (top_k).")
    p.add_argument("--wan-delay", type=int, default=0,
                   help="One-way WAN delay in ms (X-Simulate-WAN-Delay).")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--sphp-only", action="store_true",
                      help="Run only the SPHP-enabled mode.")
    mode.add_argument("--no-sphp", action="store_true",
                      help="Run only the SPHP-disabled mode.")
    mode.add_argument("--both-modes", action="store_true", default=True,
                      help="Run both SPHP-disabled and SPHP-enabled (default).")
    p.add_argument("--out", default=DEFAULT_OUT,
                   help="Output JSON path.")
    p.add_argument("--rtt-probes", type=int, default=DEFAULT_RTT_PROBES,
                   help="Number of RTT probe samples.")
    p.add_argument("--sleep", type=float, default=0.1,
                   help="Sleep between queries (s).")
    return p.parse_args(argv)


def load_queries(path: Optional[str]) -> List[str]:
    if path is None:
        return list(BUILTIN_QUERIES)
    with open(path, "r", encoding="utf-8") as f:
        return [line.rstrip("\n") for line in f if line.strip()]


def _host_port(gateway: str) -> (str, int):
    """Split a base URL like http://10.8.0.2:8000 into (host, port)."""
    url = gateway if "://" in gateway else "http://" + gateway
    netloc = url.split("://", 1)[1].split("/", 1)[0]
    if ":" in netloc:
        host, port = netloc.rsplit(":", 1)
        return host, int(port)
    return netloc, 80


def run_mode(
    gateway: str,
    queries: List[str],
    n: int,
    top_k: int,
    wan_delay: int,
    sphp: bool,
    sleep_s: float,
) -> List[Dict[str, Any]]:
    label = "SPHP-enabled" if sphp else "SPHP-disabled"
    print(f"\n--> {label}  ({min(n, len(queries))} queries, "
          f"wan_delay={wan_delay} ms, top_k={top_k})")
    runs: List[Dict[str, Any]] = []
    for i, q in enumerate(queries[:n], 1):
        res = run_query(gateway, q, top_k, wan_delay, sphp)
        if "error" in res:
            print(f"  [{i:02d}] ERROR: {res['error']}")
            runs.append(res)
            continue
        t = res.get("timings", {})
        hit = t.get("sphp_hit")
        hit_str = f" | hit={hit}" if hit is not None else ""
        print(f"  [{i:02d}] TTFT={t.get('ttft_ms', 0):7.1f}ms "
              f"TPS={res.get('decode_tps', 0):5.1f} "
              f"total={t.get('total_ms', 0):7.1f}ms{hit_str} "
              f"'{q[:32]}...'")
        runs.append(res)
        time.sleep(sleep_s)
    return runs


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    queries = load_queries(args.queries)
    if not queries:
        print("No queries available; aborting.", file=sys.stderr)
        return 2

    host, port = _host_port(args.gateway)
    rtt = measure_rtt(host, port, args.rtt_probes)
    rtt_str = f"{rtt:.2f} ms" if rtt == rtt else "unreachable"  # NaN check
    print("=" * 70)
    print("NODE C CROSS-SITE BENCHMARK (SPHP on/off)")
    print(f"  Gateway : {args.gateway}")
    print(f"  RTT     : {rtt_str} ({args.rtt_probes} TCP probes)")
    print(f"  Queries : {len(queries)} available, "
          f"running {min(args.n, len(queries))} per mode")
    print(f"  WAN     : {args.wan_delay} ms one-way simulated delay")
    print("=" * 70)

    modes: List[bool] = []
    if args.sphp_only:
        modes = [True]
    elif args.no_sphp:
        modes = [False]
    else:
        modes = [False, True]

    results: Dict[str, Any] = {
        "node": "C",
        "node_ip": "10.8.0.3",
        "gateway": args.gateway,
        "client_rtt_ms": (round(rtt, 2) if rtt == rtt else None),
        "wan_delay_ms": args.wan_delay,
        "top_k": args.top_k,
        "n_per_mode": min(args.n, len(queries)),
        "queries": queries[: min(args.n, len(queries))],
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "modes": {},
    }

    for sphp in modes:
        key = "sphp_enabled" if sphp else "sphp_disabled"
        runs = run_mode(args.gateway, queries, args.n, args.top_k,
                        args.wan_delay, sphp, args.sleep)
        results["modes"][key] = {
            "sphp": sphp,
            "runs": runs,
            "summary": _summarize(runs),
        }

    # Write output.
    out_dir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    # Summary table.
    print("\n" + "=" * 70)
    print("SUMMARY")
    print(f"{'mode':<16} | {'TTFT mean':>10} | {'TPS mean':>9} | "
          f"{'total mean':>11} | {'n':>3}")
    print("-" * 70)
    for key in ("sphp_disabled", "sphp_enabled"):
        m = results["modes"].get(key)
        if not m:
            continue
        s = m["summary"]
        label = "SPHP-disabled" if key == "sphp_disabled" else "SPHP-enabled"
        print(f"{label:<16} | {s['ttft_ms']['mean']:>9.1f} | "
              f"{s['tps']['mean']:>9.1f} | {s['total_ms']['mean']:>11.1f} | "
              f"{s['n']:>3}")
    print("=" * 70)
    print(f"Results written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
