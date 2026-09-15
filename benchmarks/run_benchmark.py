import sys
import time
import json
import math
import urllib.request
import urllib.error
from typing import List, Dict, Any

GATEWAY_URL = "http://10.8.0.2:8000/query/benchmark"

QUERIES_50 = [
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
    "what is the function of the human hippocampus",
    "definition of market equilibrium in microeconomics",
    "how does crispr cas9 gene editing work",
    "causes of high blood pressure and prevention",
    "what is the speed of light in vacuum",
    "how does cellular respiration generate atp",
    "what were the main causes of world war 1",
    "explanation of newtons three laws of motion",
    "how do solar cells convert light into electricity",
    "what is the role of ribosomes in protein synthesis",
    "key differences between mitosis and meiosis",
    "what is the theory of plate tectonics",
    "how does public key cryptography work",
    "what is the central dogma of molecular biology",
    "explanation of supply and demand curves",
    "how does magnetic resonance imaging work",
    "what causes ocean tides on earth",
    "what is the function of the nephron in the kidney",
    "explanation of the greenhouse effect",
    "what is the difference between supervised and unsupervised learning",
    "how do vaccines generate immunity",
    "what are the properties of electromagnetic waves",
    "how does an internal combustion engine work",
    "what is the structure and function of hemoglobin",
    "how does the internet domain name system dns work",
    "what are the primary stages of the water cycle",
    "what is the role of insulin in glucose metabolism",
    "how do black holes form according to general relativity",
    "what is the mechanism of action of penicillin",
    "what are the fundamental forces of physics",
    "how does the central nervous system process pain",
    "what were the main outcomes of the industrial revolution",
    "how does nuclear fission generate electricity",
    "what is the function of the myelin sheath in neurons",
    "what are the differences between rna and dna",
    "how does optical fiber transmit data",
    "what is the purpose of fiscal policy in macroeconomics",
    "how does the human ear process sound waves",
    "what are the principles of natural selection",
    "how does dynamic random access memory dram operate"
]

def run_query(query: str, top_k: int = 10, wan_delay_ms: int = 0) -> Dict[str, Any]:
    payload = json.dumps({"query": query, "top_k": top_k}).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
    }
    if wan_delay_ms > 0:
        headers["X-Simulate-WAN-Delay"] = str(wan_delay_ms)
        
    req = urllib.request.Request(GATEWAY_URL, data=payload, headers=headers, method="POST")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            client_total_ms = (time.perf_counter() - t0) * 1000.0
            data["client_total_ms"] = round(client_total_ms, 2)
            return data
    except Exception as e:
        return {"error": str(e), "query": query}

def calc_stats(values: List[float]) -> Dict[str, float]:
    if not values:
        return {"mean": 0.0, "std": 0.0, "p50": 0.0, "p95": 0.0, "min": 0.0, "max": 0.0}
    s_vals = sorted(values)
    n = len(s_vals)
    mean_val = sum(s_vals) / n
    variance = sum((x - mean_val) ** 2 for x in s_vals) / n
    std_val = math.sqrt(variance)
    p50_idx = int(round(0.50 * (n - 1)))
    p95_idx = int(round(0.95 * (n - 1)))
    return {
        "mean": round(mean_val, 2),
        "std": round(std_val, 2),
        "p50": round(s_vals[p50_idx], 2),
        "p95": round(s_vals[p95_idx], 2),
        "min": round(s_vals[0], 2),
        "max": round(s_vals[-1], 2)
    }

def main():
    print("=" * 90)
    print("LARGE-SCALE DISTRIBUTED HYBRID RAG BENCHMARK (N = 50 QUERIES PER NETWORK CONDITION)")
    print("Node B: Laptop Gateway (Tantivy BM25 8.84M + BGE-M3 Dense on GTX 1660 Ti + RRF)")
    print("Node A: RTX 3090 Workstation (SQLite 8.84M O(1) Hydration + LLaMA-3 AWQ vLLM)")
    print("=" * 90)
    
    # Warmup queries
    print("\nWarming up pipeline caches...")
    for w_i in range(3):
        w_res = run_query(f"warmup query {w_i}", top_k=5, wan_delay_ms=0)
        t = w_res.get("timings", {})
        print(f"  Warmup [{w_i+1}/3]: BM25={t.get('sparse_ms', 0):.1f}ms | Dense={t.get('dense_ms', 0):.1f}ms | Total={t.get('total_ms', 0):.1f}ms")

    delay_conditions = [0, 15, 40, 80]
    all_results = {}

    for delay in delay_conditions:
        tag = f"+{delay}ms (WAN)" if delay > 0 else "LAN (0ms)"
        print(f"\n---> Starting Campaign Condition: {tag} [{len(QUERIES_50)} queries]")
        delay_runs = []
        for i, q in enumerate(QUERIES_50, 1):
            res = run_query(q, top_k=10, wan_delay_ms=delay)
            if "error" in res:
                print(f"  [{i:02d}/{len(QUERIES_50):02d}] ERROR on '{q[:35]}': {res['error']}")
                continue
            
            t = res.get("timings", {})
            print(f"  [{i:02d}/{len(QUERIES_50):02d}] BM25: {t.get('sparse_ms', 0):5.1f}ms | "
                  f"Dense(GPU): {t.get('dense_ms', 0):5.1f}ms | "
                  f"TTFT: {t.get('ttft_ms', 0):5.0f}ms | "
                  f"Total: {t.get('total_ms', 0):5.0f}ms | "
                  f"TPS: {res.get('decode_tps', 0):4.1f} | '{q[:32]}...'")
            delay_runs.append(res)
            time.sleep(0.1)

        all_results[f"delay_{delay}ms"] = delay_runs

    out_file = "benchmarks/results_matrix_gpu.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nAll benchmark telemetry saved to {out_file}")

    # Generate Statistical Analysis
    print("\n" + "=" * 100)
    print("STATISTICAL BENCHMARK SUMMARY (N = 50 queries per tier)")
    print("=" * 100)
    print(f"{'Condition':<14} | {'BM25 Mean (p50/p95)':<23} | {'Dense Mean (p50/p95)':<23} | {'TTFT Mean (p50/p95)':<23} | {'Decode TPS':<10}")
    print("-" * 100)

    for cond, runs in all_results.items():
        if not runs:
            continue
        sparse_stats = calc_stats([r["timings"]["sparse_ms"] for r in runs])
        dense_stats = calc_stats([r["timings"]["dense_ms"] for r in runs])
        ttft_stats = calc_stats([r["timings"]["ttft_ms"] for r in runs])
        tps_stats = calc_stats([r["decode_tps"] for r in runs])

        cond_label = cond.replace("delay_0ms", "LAN (0 ms)").replace("delay_15ms", "+15 ms (Edge)").replace("delay_40ms", "+40 ms (WAN)").replace("delay_80ms", "+80 ms (WAN)")
        
        sp_str = f"{sparse_stats['mean']:.1f} ({sparse_stats['p50']:.1f}/{sparse_stats['p95']:.1f}) ms"
        de_str = f"{dense_stats['mean']:.1f} ({dense_stats['p50']:.1f}/{dense_stats['p95']:.1f}) ms"
        tt_str = f"{ttft_stats['mean']:.1f} ({ttft_stats['p50']:.1f}/{ttft_stats['p95']:.1f}) ms"
        tp_str = f"{tps_stats['mean']:.1f} tps"

        print(f"{cond_label:<14} | {sp_str:<23} | {de_str:<23} | {tt_str:<23} | {tp_str:<10}")
    print("=" * 100)

if __name__ == "__main__":
    main()
