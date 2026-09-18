import json
import math

with open("benchmarks/results_matrix_gpu.json") as f:
    d = json.load(f)

def calc_stats(values):
    s = sorted(values)
    n = len(s)
    mean = sum(s) / n
    var = sum((x - mean) ** 2 for x in s) / n
    std = math.sqrt(var)
    p50 = s[int(round(0.50 * (n - 1)))]
    p95 = s[int(round(0.95 * (n - 1)))]
    return {"mean": mean, "std": std, "p50": p50, "p95": p95, "min": s[0], "max": s[-1]}

labels = {
    "delay_0ms": "LAN (0 ms)",
    "delay_15ms": "+15 ms (Edge)",
    "delay_40ms": "+40 ms (WAN)",
    "delay_80ms": "+80 ms (WAN)"
}

print("=" * 115)
print(f"{'Network Condition':<16} | {'Sparse BM25 (p50/p95)':<22} | {'Dense Vector (p50/p95)':<23} | {'TTFT (p50/p95)':<22} | {'Total Latency (Mean/p50/p95)':<25}")
print("-" * 115)

for k in ["delay_0ms", "delay_15ms", "delay_40ms", "delay_80ms"]:
    runs = d[k]
    sp = calc_stats([r["timings"]["sparse_ms"] for r in runs])
    de = calc_stats([r["timings"]["dense_ms"] for r in runs])
    tt = calc_stats([r["timings"]["ttft_ms"] for r in runs])
    tot = calc_stats([r["timings"]["total_ms"] for r in runs])
    lbl = labels[k]

    sp_str = f"{sp['p50']:.1f} / {sp['p95']:.1f} ms"
    de_str = f"{de['p50']:.1f} / {de['p95']:.1f} ms"
    tt_str = f"{tt['p50']:.1f} / {tt['p95']:.1f} ms"
    tot_str = f"{tot['mean']:.1f} ({tot['p50']:.1f} / {tot['p95']:.1f}) ms"

    print(f"{lbl:<16} | {sp_str:<22} | {de_str:<23} | {tt_str:<22} | {tot_str:<25}")

print("=" * 115)
