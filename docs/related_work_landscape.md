# Related-Work Landscape & Novelty Check — LR-1 (Phase 1)

**Session:** LR-1 · **Date:** 2026-09-30 · **Branch:** `feat/three-node-study`
**Concern:** map the published landscape for distributed / geo-distributed RAG serving and placement, and check our paper's claims by contradiction against the closest prior work.
**Method:** broad search across systems (OSDI/ISCA/SOSP/ASPLOS/HPDC), IR (SIGIR/EMNLP), and recent arXiv; each cited source run through the 4-stage anti-hallucination protocol (S1 resolve, S2 triangulate, S3 corroborate the specific claim, S4 verdict) and appended to `docs/literature_ledger.md` under the LR-1 section.

---

## RQ-1 — Who distributes RAG retrieval/serving across network locations, and where do they place stages?

The landscape splits into four distinct families. **None of them places the *hybrid-retrieval pipeline stages* (sparse / dense / fusion / hydrate) as a function of network regime** — that is the gap our paper fills.

| Family | Representative work | What is distributed | Where stages are placed | Network-regime-aware? |
|---|---|---|---|---|
| **Federated RAG** (privacy/data-silo driven) | `federatedrag2025` (arXiv:2505.18906, EMNLP Findings 2025) — first systematic mapping study of Federated RAG, 2020–2025 | Distributed *knowledge sources* across silos/clients; query broadcast to peers, each searches a local corpus, results fused | Retrieval stays **local to each data owner**; generation at a central orchestrator. Placement is driven by **data-residency/privacy**, not latency | **No** — no RTT/bandwidth sweep; the "distribution" is a privacy constraint, not a network-performance variable |
| **RAG systems characterization** (single-host) | `shen2024ragtradeoffs` (arXiv:2412.11854, Cornell/NVIDIA) | Nothing distributed — a **single-host** RAG stack (encode → retrieve → prefill → decode) | All stages colocated; the paper's contribution is a *taxonomy* of RAG design choices (retrieval algorithm, retrieval stride, batching) and their latency/throughput/memory trade-offs | **No** — measures TTFT/tail/throughput/memory on one host; retrieval is 41–47% of TTFT, but there is no network leg to place |
| **LLM phase disaggregation** (prefill/decode) | `patel2024splitwise` (ISCA 2024), `zhong2024distserve` (OSDI 2024), `janus2025` (EAI IoT 2025) | The **LLM inference phases** (compute-bound prefill vs memory-bound decode) across GPU nodes / multi-cloud / edge-adjacent fleets | Prefill on one pool, decode on another; **KV cache is the stateful payload** transferred across the link. `janus2025` explicitly treats KV-transport as a first-class scheduling variable across heterogeneous link bandwidths | **Partially** — `janus2025` and the split-inference line (`cunningham2026splitwan`) do optimize placement/transport as a function of link bandwidth/RTT, **but for the LLM's prefill/decode phases, not for RAG retrieval stages** |
| **DNN / model partitioning** (edge-cloud) | `kang2017neurosurgeon` (ASPLOS 2017), `teerapittayanon2017ddnn` (ICDCS 2017), `banitalebi2021autosplit` (KDD 2021) | A **single DNN/LLM** split at the layer level between device and server | Split point chosen by FLOPs/memory/bandwidth; the model is partitioned, not a multi-stage retrieval pipeline | **Partially** — some (e.g. adaptive layer-splitting under channel conditions) are channel-aware, but the object being placed is a monolithic model, not a hybrid-RAG pipeline |

**Takeaway for RQ-1:** the closest *placement* work (LLM phase disaggregation, DNN partitioning) places **model phases/layers**, and the closest *RAG* work (federated RAG, single-host characterization) either does not distribute across a network at all or distributes for **privacy**, not latency. **No published system places the hybrid-retrieval stages (sparse/dense/fusion/hydrate) of a RAG pipeline across network locations as a function of RTT/bandwidth.** That is the open space.

---

## RQ-2 — Does any prior work measure *where* hybrid-retrieval stages sit as a function of network regime, with retrieval quality measured **jointly** with systems metrics on a standard IR benchmark?

**No — this is the core novelty, and it survives the contradiction check.**

- The **single strongest candidate** is `shen2024ragtradeoffs` (arXiv:2412.11854). It is the only paper we found that measures **retrieval quality (recall) jointly with systems metrics (TTFT, tail latency, throughput, memory)** on a real RAG stack. **But it is single-host**: there is no network leg, no placement variable, and no RTT/bandwidth axis. It characterizes *how much* retrieval costs, not *where* to put it. Our paper's delta: we add the **placement dimension** (which stage on which node) and the **network-regime axis** (0/15/40/80 ms RTT), and we report retrieval quality (MRR@10, nDCG@10, Recall@100/1000 on MS MARCO dev) **jointly** with TTFT across those regimes.
- `federatedrag2025` measures retrieval quality across distributed sources but **does not vary the network regime** and does not place individual pipeline stages; its "distribution" is a data-silo/privacy constraint.
- The LLM-disaggregation line (`janus2025`, `cunningham2026splitwan`) measures placement-vs-regime but for **LLM phases**, and reports **no retrieval-quality metric at all** (they are serving-systems papers; quality is out of scope).

**Verdict for RQ-2: OPEN.** No prior work couples (a) hybrid-retrieval *stage placement*, (b) a *network-regime* independent variable, and (c) *retrieval quality jointly with systems metrics* on a standard IR benchmark. Our paper is the first to do all three.

---

## RQ-3 — Claim-by-claim novelty verdicts

Verdicts: **established** (prior work already makes this claim), **partial** (prior work is close but a real delta remains), **open** (no prior work makes the claim).

### H1 — Fusion placement is ~irrelevant
**Verdict: PARTIAL.**
- Closest work: `shen2024ragtradeoffs` (arXiv:2412.11854) establishes that **fusion/reranking is a small fraction of RAG latency** (retrieval and prefill dominate; fusion is negligible). The *magnitude* of our finding (fusion ≈ 0.02 ms) is consistent with the general "fusion is cheap" result.
- **Delta:** no prior work treats **the *location* of the fusion step** (which node runs RRF) as a placement variable and shows it is invariant. We show fusion placement is irrelevant *specifically because* the fused payload is identifier-sized, which is a placement-specific claim, not a latency-magnitude claim.

### H2 — Remote generation is nearly free under streaming
**Verdict: PARTIAL.**
- Closest work: `patel2024splitwise` (ISCA 2024) and `zhong2024distserve` (OSDI 2024) show that **moving the LLM across machines costs a KV-cache transfer** (pipelined to ~5–10 ms in Splitwise); `janus2025` and `cunningham2026splitwan` quantify that transfer as a function of link bandwidth/RTT. So "moving generation is not free" is well established **for the KV cache**.
- **Delta:** our P2 boundary ships only **identifier-sized payloads (104–144 B)**, not the KV cache, so the cross-link cost is sub-millisecond and the network regime is a second-order term (measured TTFT slope 0.73 ms/ms). No prior work makes the specific claim that a **RAG pipeline boundary carrying only doc-ids (not KV state) is network-regime-insensitive**. The disaggregation literature's "cost" is the KV handoff; ours is the *absence* of a stateful payload.

### H3 — Hybrid retrieval gives a WAN-free quality gain with the sparse index at the gateway
**Verdict: OPEN.**
- Closest work: the hybrid-retrieval literature (BM25 + dense + RRF; e.g. the BEIR-based results cited in `shen2024ragtradeoffs` and the RRF literature) establishes that **hybrid > single-leg on recall/precision**. `federatedrag2025` shows distributed retrieval can preserve quality.
- **Delta:** no prior work isolates the **WAN-free** property — that placing the *sparse* leg at the gateway (and the *dense* leg on the GPU node) yields a **quality gain that does not depend on the network regime**, measured on a standard IR benchmark (MS MARCO) with the two legs on different machines. The "WAN-free quality" framing (quality decoupled from the link) is new.

### H4 — Placement crossover: an RTT × bandwidth phase map
**Verdict: PARTIAL (closest), leaning OPEN for RAG stages.**
- Closest work: `janus2025` (EAI IoT 2025) is the **single closest** — it explicitly produces a **placement decision as a function of link bandwidth and RTT** (a "phase map" of where to run prefill/decode and how to transport KV) across a multi-cloud/edge fleet, with a closed-form layer-split optimum. `cunningham2026splitwan` and the adaptive-layer-splitting line do the same for DNN/LLM splits.
- **Delta:** all of these phase maps are for **LLM phases / DNN layers**. **No prior work produces an RTT × bandwidth placement phase map for the *hybrid-RAG pipeline stages* (sparse/dense/fusion/hydrate/generation).** Our crossover analysis (P2-SPHP optimal in every swept cell; P3's hydrated-text penalty appearing only under bandwidth starvation) is the first such map for RAG stages.

### SPHP — sparse-hint speculative prefill + reconciliation
**Verdict: OPEN (mechanism), with two close-but-different neighbors.**
- Closest work 1: **Speculative RAG** (`speculativerag2025`, Google Research, ICLR 2025) — a *smaller specialist LM drafts* candidate answers from document subsets, a *larger generalist LM verifies*. This is **model-level speculation** (draft model vs target model).
- Closest work 2: **Omnia** (`omnia2025`, HPDC 2025) — **chunk speculation + progressive prefilling**: after the first reranking group finishes, the top chunk begins *speculative prefilling* while later groups append sub-prefills, masking reranking latency. This is the **closest** to our "prefill before the full context is ready" idea.
- **Delta:** SPHP is distinct from both. (i) It speculates on a **provisional *sparse* hint** (the BM25 leg's top-k), not on a draft model's tokens (Speculative RAG) nor on a *reranked* chunk (Omnia). (ii) It **reconciles** the speculative prefill against the *fused* (sparse+dense) context on arrival via a **set-overlap hit test** (measured h=0.84), a reconciliation step neither neighbor has. (iii) It is a **pipeline-schedule** speculation (overlap prefill with the in-flight dense leg), orthogonal to token-level speculative decoding. No prior work combines sparse-hint prefill + fused-context reconciliation.

---

## The three closest works overall

1. **`shen2024ragtradeoffs`** — *Towards Understanding Systems Trade-offs in RAG Model Inference* (Shen, Umar, Maeng, Suh, Gupta; Cornell/NVIDIA/PSU; arXiv:2412.11854, Dec 2024). The only paper measuring **retrieval quality jointly with TTFT/tail/throughput/memory** on a real RAG stack. **Delta:** single-host, no placement, no network-regime axis.
2. **`janus2025`** — *Janus: Joint Prefill/Decode Disaggregation with KV-Cache-Aware Multi-Cloud Routing for Edge-Adjacent LLM Serving* (Aruna, Kaliraj, Sudha, Sureka; EAI Endorsed Trans. on IoT, 2025; DOI 10.4108/eetiot.13349). The closest **placement-as-a-function-of-(RTT, bandwidth)** work, with a closed-form layer-split optimum and provable guarantees. **Delta:** places LLM prefill/decode phases (KV-cache transport), not RAG retrieval stages; no retrieval-quality metric.
3. **`speculativerag2025`** — *Speculative RAG: Enhancing RAG through Drafting* (Google Research; ICLR 2025). The closest **speculative-RAG** work. **Delta:** model-level draft/verify, not sparse-hint prefill + fused-context reconciliation.

**Runner-up (mechanism-adjacent):** `omnia2025` (HPDC 2025) — chunk speculation + progressive prefilling; the closest to SPHP's "prefill before the full context arrives," but it speculates on reranked chunks, not a provisional sparse hint, and has no reconciliation.

---

## Bottom line

- **RQ-1:** four families exist (federated RAG, single-host RAG characterization, LLM phase disaggregation, DNN partitioning); **none places hybrid-RAG pipeline stages by network regime.**
- **RQ-2:** **OPEN** — no prior work couples stage placement + network regime + retrieval-quality-jointly-with-systems-metrics on a standard IR benchmark.
- **RQ-3:** H1 **partial**, H2 **partial**, H3 **open**, H4 **partial→open** (for RAG stages), SPHP **open**. The two claims most at risk of a reviewer's "this is just X" are **H4** (vs. `janus2025`) and **SPHP** (vs. `speculativerag2025`/`omnia2025`); both survive on the RAG-stage / sparse-hint-reconciliation delta, and the paper's related-work section should cite `janus2025`, `speculativerag2025`, and `omnia2025` explicitly to preempt that objection.
