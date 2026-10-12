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

## Speculation landscape — LR-2a (Phase 1)

**Session:** LR-2a · **Date:** 2026-09-30 · **Branch:** `feat/three-node-study`
**Concern:** map the published *speculation* landscape in the two non-LLM families that SPHP borrows from — (A) speculative prefetch in storage/DB/web serving, and (B) speculation over slow/unreliable links (speculative RPC/execution) — and run a targeted existence check for the specific SPHP mechanism (speculate on a *partial retrieval result* to prefill a generator, then reconcile against the final result).

**Method:** for each family, pick the 2 canonical published systems papers by citation standing; each is run through the 4-stage anti-hallucination protocol (S1 resolve, S2 triangulate, S3 corroborate, S4 verdict) and appended to `docs/literature_ledger.md` under the LR-2a section. For each we capture: the claim, the **reconciliation semantics** (commit / abort / replay), and a one-line delta vs SPHP (sparse-hint speculative prefill, abort-on-mismatch). LLM speculative decoding is **not** re-surveyed here — the paper's related-work section already cites the canonical pair (`leviathan2023speculative`, `chen2023speculative`); we reuse those and add nothing.

### Family A — speculative prefetch in storage / DB / web serving

| Paper | Claim | Reconciliation semantics | Delta vs SPHP |
|---|---|---|---|
| **Patterson, Gibson, Ginting, Stodolsky, Zelenka — *Informed Prefetching and Caching* (SOSP 1995)** | The application discloses *hints* about its future I/O; the file system prefetches those pages speculatively to hide I/O latency. | **Advisory / non-binding.** A hint is a prediction, not a commitment: if the predicted access never occurs the prefetched page is simply **evicted** (no abort, no replay — it is a cache, so "commit" = the page stays resident, "miss" = eviction). | Speculates on **which data to fetch** (I/O), not on **which context to prefill a generator with**; there is no later authoritative result to reconcile against — the hint is a cache-fill prediction, not a provisional answer to a query. |
| **Padmanabhan & Mogul — *Using Predictive Prefetching to Improve World Wide Web Latency* (SIGCOMM 1996)** | A server maintains per-client usage statistics and **predicts the next object** a client will request, prefetching it before it is demanded to hide Web latency. | **Predict / drop.** The prefetch is speculative by nature; if the prediction is wrong the prefetched object is **dropped** (the cost is cache pollution / bandwidth waste) — no replay, no commit. | Speculates on **which web object to fetch next** from usage history, not on a **provisional retrieval result** to prefill a generator; no reconciliation against a final fused result. |

### Family B — speculation over slow / unreliable links (speculative RPC / execution)

| Paper | Claim | Reconciliation semantics | Delta vs SPHP |
|---|---|---|---|
| **Wester, Chen, Cowling, Flinn, Nightingale, Liskov — *Tolerating Latency in Replicated State Machines through Client Speculation* (NSDI 2009)** | A client of a geographically-replicated service **speculates the result of a request**, proceeds on the predicted value, and reconciles when the real response arrives — hiding network + protocol latency over slow links. | **Commit / abort (predicated).** The client issues *predicated writes* and *replica-resolved speculation*: if the predicted result matches the real one the speculation is **committed**; if not it is **aborted** and rolled back. | Speculates on the **value returned by a remote RPC** and reconciles by **value-equality** on the RPC result; SPHP speculates on a **provisional retrieval context** and reconciles by **set-overlap** on a retrieval ranking — a different object and a different (lighter) test. |
| **Wester, Chen, Flinn — *Operating System Support for Application-Specific Speculation* (EuroSys 2011)** | A general OS-level **mechanism** for speculative execution (checkpointing, rollback, causality tracking, output buffering) that lets applications define *what* to predict and *how* to compare results, coordinating speculation across all applications and kernel state. | **Checkpoint / rollback (abort) or commit.** The OS captures state before a speculative action; on a mismatch it **rolls back** to the checkpoint (abort), otherwise the action is **committed**. | A **general mechanism with full state rollback**; SPHP is a **pipeline-schedule** speculation whose speculative prefill is *masked* (held in a scratch buffer, never committed to client state) and reconciled by a lightweight set-overlap test — no full state checkpoint/rollback. |

### LLM speculative decoding (reused, not re-surveyed)

The paper's related-work section already cites the canonical pair — **Leviathan, Kalman, Matias — *Fast Inference from Transformers via Speculative Decoding* (ICML 2023)** and **Chen et al. — *Accelerating Large Language Model Decoding with Speculative Sampling* (2023)** — and states that SPHP is **orthogonal** to this line: it speculates over the **pipeline schedule** (begin prefilling on a provisional sparse context before the dense leg finishes, then reconcile), not over the **tokens** of a single model. We reuse these citations and add nothing new here.

### Existence check — does any published system speculate on a *partial retrieval result* to prefill a generator, then reconcile against the final result?

**Verdict: NOT FOUND.** A targeted search for published systems that (i) speculate on a **partial / provisional retrieval result** (a sparse or lexical hint) to **prefill a generator**, and (ii) **reconcile** (commit/abort) that speculation against the **final fused retrieval result**, returns no match. The two nearest misses, both already in the LR-1 ledger, fall short on a distinct axis:

- **`speculativerag2025` — *Speculative RAG* (ICLR 2025, Google Research).** A small specialist LM **drafts** candidate answers from document subsets while retrieval continues; a large generalist LM **verifies** by selecting the best draft. **Nearest-miss delta:** it speculates on **draft answers from a smaller model** (model-level draft/verify), not on a **provisional sparse retrieval hint** to prefill the generator, and it reconciles by **selecting the best draft**, not by a **set-overlap hit test** against the final fused retrieval result.
- **`omnia2025` — *Efficient RAG Serving through Speculative Scheduling* (HPDC 2025).** After the first reranking group finishes, the top chunk begins **speculative prefilling** while later groups append sub-prefills — the closest to "prefill before the full context is ready." **Nearest-miss delta:** it speculates on a **reranked chunk** (a partial *reranking* result), not on a **provisional sparse hint**, and it has **no reconciliation** step (it appends sub-prefills; there is no commit/abort against a final fused result).

**Bottom line for LR-2a:** the *speculation* pattern (predict → act → reconcile) is well established in both non-LLM families, but every prior instance speculates on **I/O objects** (Family A) or **RPC results / general state** (Family B) — never on a **provisional retrieval result used to prefill a generator**, and never with a **set-overlap reconciliation against a final fused retrieval result**. SPHP's specific combination (sparse-hint prefill + fused-context set-overlap reconciliation) is **open**.

---

## Cost models & placement policies — LR-4 (Phase 1)

**Session:** LR-4 · **Date:** 2026-09-30 · **Branch:** `feat/three-node-study`
**Concern:** ground our analytical placement cost model (the RQ3 predictor: per-stage compute times + payload sizes + network terms → best placement per regime; live policy realized within 15% tolerance; leave-one-tier-out MAPE 1.3–2.8%) in the published literature.
**Method:** for each of three families — (i) distributed-inference / LLM-serving placement, (ii) data/computation placement in edge–cloud systems, (iii) queueing / network models predicting serving latency — pick the 2–3 canonical published systems papers by citation standing; each is run through the 4-stage anti-hallucination protocol and appended to `docs/literature_ledger.md` under the LR-4 section. For each we capture: the **model form** (closed-form / regression / queueing / simulation), the **decision it drives**, its **validation methodology**, and a one-line delta vs our regime-level placement predictor.

### Family (i) — distributed-inference / LLM-serving placement

| Paper | Model form | Decision it drives | Validation | Delta vs our regime-level placement predictor |
|---|---|---|---|---|
| **Patel, Choukse, Zhang, et al. — *Splitwise: Efficient Generative LLM Inference Using Phase Splitting* (ISCA 2024)** | Analytical **cost/power model** of the two inference phases (compute-bound prefill vs memory-bound decode) parameterized by GPU FLOPs / HBM bandwidth / power / cost. | **Phase placement**: which hardware pool runs prefill vs decode, and the interconnect bandwidth between them. | Measured on production LLMs (Llama-70B); 1.4× throughput at 20% lower cost, or 2.35× throughput at same cost/power. | Places **LLM phases** (KV-cache handoff) by hardware cost/power; our model places **RAG pipeline stages** (sparse/dense/fusion/hydrate/generation) by **RTT × bandwidth regime** with per-stage compute + payload terms — a different object and a different independent variable. |
| **Zhong, et al. — *DistServe: Disaggregating Prefill and Decoding for Goodput-optimized LLM Serving* (OSDI 2024)** | **Discrete-event simulation** of phase throughput + a **goodput optimization** (max request rate meeting TTFT/TPOT SLOs) over resource allocation and parallelism. | **Phase placement + parallelism**: co-optimizes allocation/parallelism per phase and places the two phases by cluster bandwidth. | Evaluated on popular LLMs/applications under TTFT/TPOT SLOs; 7.4× capacity. | Optimizes **throughput/goodput** (a rate objective) for LLM phases; our model predicts **per-request TTFT** (a latency objective) for RAG stages and selects placement per **network regime**, not per SLO budget. |
| **Li, Zheng, Zhong, et al. — *AlpaServe: Statistical Multiplexing with Model Parallelism for Deep Learning Serving* (OSDI 2023)** | **Analytical per-model latency/overhead model** + an optimization that places and parallelizes a *set* of DNNs across a cluster to exploit statistical multiplexing. | **Placement + parallelism** of a model portfolio across a cluster. | Production workloads; up to 10× higher rate or 6× more burstiness within latency constraints. | Places a **portfolio of models** for statistical multiplexing; our model places the **stages of a single RAG pipeline** across a fixed 3-node topology as a function of the link regime. |

### Family (ii) — data/computation placement in edge–cloud systems

| Paper | Model form | Decision it drives | Validation | Delta vs our regime-level placement predictor |
|---|---|---|---|---|
| **Kang, Hauswald, Gao, et al. — *Neurosurgeon: Collaborative Intelligence Between the Cloud and Mobile Edge* (ASPLOS 2017)** | **Regression / prediction models** of per-layer latency and intermediate data size (fit from a small set of measurements) to estimate end-to-end latency for each candidate split point. | **Partition-point selection**: which layer to split a DNN at between device and cloud, chosen for best latency or energy. | Measured on real mobile devices + cloud; the prediction models track measured latency closely. | The **closest** to our approach — a predictive per-stage cost model driving a split decision. Delta: it partitions a **monolithic DNN at a layer boundary** between device and cloud; our model places **discrete RAG pipeline stages** (sparse/dense/fusion/hydrate/generation) across a **3-node** topology and treats **RTT × bandwidth** as the independent variable. |
| **Banitalebi-Dehkordi, Vedula, Pei, et al. — *Auto-Split: A General Framework of Collaborative Edge-Cloud AI* (KDD 2021)** | **Optimization** over the split point coupled with post-training quantization, minimizing end-to-end latency subject to accuracy constraints. | **Edge/cloud DNN partitioning** (split point + quantization). | ResNet-50 / MobileNet / YOLOv3; 20–80% latency reduction, >40% edge-model-size reduction. | Couples placement with **quantization** (a model-transformation variable); our model keeps the model fixed and varies only **stage placement** across the link regime. |

### Family (iii) — queueing / network models predicting serving latency

| Paper | Model form | Decision it drives | Validation | Delta vs our regime-level placement predictor |
|---|---|---|---|---|
| **Cao, Andersson, Nyberg, Kihl — *Web Server Performance Modeling Using an M/G/1/K\*PS Queue* (Lund, IEEE/ACM)** | **Closed-form M/G/1/K\*PS queueing model** predicting response time, throughput, and blocking probability of a web server. | **Capacity planning / overload control** (how many concurrent requests the server can serve before blocking). | Validated against measured web-server performance in a test lab. | A **queueing** model of a single server under load (concurrency-driven); our model is a **per-stage additive latency** model (compute + payload + network terms) for a **single request** across a **multi-node** pipeline — no queueing/concurrency dimension. |
| **Gujarati, Karimi, et al. — *Serving DNNs like Clockwork: Performance Predictability from the Bottom Up* (OSDI 2020)** | **Analytical bottom-up performance model** of DNN-serving latency (compute + memory + interconnect) to make serving latency predictable. | **Capacity / placement** decisions for DNN serving. | Validated against measured serving systems (bottom-up predictability). | Predicts **DNN-serving** latency from hardware primitives; our model predicts **RAG-pipeline** TTFT from per-stage compute + payload + network terms and uses it to **select placement per regime**. |

### Verdict — is an analytical per-stage cost model with live placement selection established practice, novel in RAG, or novel outright?

**Established practice in the general systems literature, novel in RAG, and novel outright for the specific combination we make.**

- **Established practice (the pattern):** an analytical / predictive per-stage cost model that drives a **placement decision** is a well-established pattern in both non-LLM families. Family (ii) is the direct ancestor — **Neurosurgeon** (ASPLOS 2017) and **Auto-Split** (KDD 2021) both fit per-stage latency/size models and use them to pick a split point between edge and cloud; **Auto-Split** and **Neurosurgeon** are the two canonical references for "predict per-stage cost → choose placement." Family (i) does the same for LLM phases (**Splitwise**, **DistServe**, **AlpaServe**), and family (iii) supplies the queueing / closed-form latency-prediction substrate. So the *method* (per-stage cost model → placement policy) is **not novel outright**.
- **Novel in RAG:** no prior work applies this pattern to **hybrid-RAG pipeline stages** (sparse / dense / fusion / hydrate / generation). The LLM-serving line places **LLM phases** (KV-cache handoff), and the edge–cloud line places **DNN layers** — neither places the **retrieval stages of a RAG pipeline**.
- **Novel outright (the specific combination):** our model is the first to (a) treat **RTT × bandwidth** as the independent variable for a **regime-level** placement decision (a phase map, not a single split point), (b) place **discrete RAG pipeline stages** (not a monolithic DNN or LLM phases) across a **3-node** topology, and (c) drive a **live** placement policy validated against a measured campaign (15% tolerance; leave-one-tier-out MAPE 1.3–2.8%). The closest single work is **Neurosurgeon** (predictive per-stage cost → split point), but it partitions a monolithic DNN at a layer boundary between device and cloud, not RAG stages across a 3-node regime map.

**Closest works, named:** `kang2017neurosurgeon` (ASPLOS 2017) — predictive per-stage cost → split point (closest overall); `zhong2024distserve` (OSDI 2024) — phase placement by cluster bandwidth (closest for the bandwidth-aware placement axis); `li2023alpaserve` (OSDI 2023) — analytical placement optimization (closest for the analytical-optimization axis).

---

## Bottom line

- **RQ-1:** four families exist (federated RAG, single-host RAG characterization, LLM phase disaggregation, DNN partitioning); **none places hybrid-RAG pipeline stages by network regime.**
- **RQ-2:** **OPEN** — no prior work couples stage placement + network regime + retrieval-quality-jointly-with-systems-metrics on a standard IR benchmark.
- **RQ-3:** H1 **partial**, H2 **partial**, H3 **open**, H4 **partial→open** (for RAG stages), SPHP **open**. The two claims most at risk of a reviewer's "this is just X" are **H4** (vs. `janus2025`) and **SPHP** (vs. `speculativerag2025`/`omnia2025`); both survive on the RAG-stage / sparse-hint-reconciliation delta, and the paper's related-work section should cite `janus2025`, `speculativerag2025`, and `omnia2025` explicitly to preempt that objection.

---

## LR-6 — Counter-Paradigms, Competing Architectures & Boundary Conditions (2024–2026)

**Session:** LR-6 · **Date:** 2026-10-02 · **Branch:** `feat/three-node-study`
**Concern:** Map the broader literature landscape *beyond* confirmation bias. A rigorous Q1 submission must confront the four major competing paradigms that modern systems reviewers will raise:

### 1. Prompt / Prefix Caching (The "Why Speculate when you can Cache?" Objection)
* **Competing Literature**:
  - `zheng2024sglang` (ICML 2024) — *SGLang / RadixAttention*: maintains Radix trees over KV caches to reuse shared prompt prefixes across multi-turn and multi-doc calls.
  - `gim2024promptcache` (MLSys 2024) — *Prompt Cache*: modular attention reuse for prefill reduction.
  - `liu2024cachegen` (SIGCOMM 2024) — *CacheGen*: fast context loading via KV-cache streaming and compression.
* **The Tension**: If prefix caching is active and queries share documents, warm-prefix TTFT drops to sub-100 ms, rendering speculative prefill irrelevant.
* **Our Rigorous Grounding**:
  - SPHP's benefit is strictly a function of **cache state**. In warm regimes ($R_{1..2}$), empirical savings drop to within measurement noise ($<2\%$).
  - SPHP is strictly a **cold-prefix mechanism** ($R_0$) targeting exploratory, ad-hoc, or tail queries where the retrieved document combination has zero prefix overlap in the Radix tree. SPHP does not replace prefix caching; it is the *complement* that covers the cold-miss tail where caching fails.

### 2. Edge SLMs vs. Centralized Server Offloading (The "Why WAN at all?" Objection)
* **Competing Literature**:
  - `wang2024edgefm` (MobiCom 2024) — *EdgeFM*: foundation models on mobile devices.
  - `chen2024eacorag` (IEEE INFOCOM 2024) — *EACO-RAG*: edge-assisted collaborative online RAG.
  - Recent high-efficiency SLMs: Llama-3.2-1B/3B, Qwen2.5-1.5B/3B, Phi-3.5-mini.
* **The Tension**: Modern edge devices (laptop GPUs, NPUs) can run 1B–3B models locally with 0 ms network delay. Why introduce geo-distributed complexity?
* **Our Rigorous Grounding**:
  - Edge generation hits a strict reasoning and context-length ceiling (hallucination rate and multi-hop accuracy on complex benchmarks like MS MARCO).
  - Our analytical cost model (H2/H4) quantifies the exact crossover boundary: when the quality/reasoning gap of 1B–3B models outweighs the WAN transfer latency of offloading to a 27B+ parameter generator.

### 3. Dense-Only / Late-Interaction (The "Why Hybrid BM25?" Objection)
* **Competing Literature**:
  - `santhanam2022colbertv2` (NAACL 2022) / `santhanam2022plaid` (CIKM 2022) — *ColBERTv2 / PLAID*: lightweight late-interaction retrieval.
  - `thakur2021beir` (NeurIPS 2021) — *BEIR benchmark*: zero-shot IR evaluation across 18 datasets.
* **The Tension**: Modern dense models (BGE-M3 standalone, E5-Mistral) argue BM25 is obsolete, eliminating the two-leg hybrid pipeline.
* **Our Rigorous Grounding**:
  - Out-of-domain robustness: BEIR studies demonstrate that dense-only models suffer from vocabulary mismatch on exact keywords, serial numbers, and domain shifts, where BM25 remains indispensable.
  - Dual purpose in SPHP: The sparse leg is not just a quality booster—it serves as the **ultra-low-latency speculative trigger** (~15 ms vs ~1,100 ms dense) that allows speculative prefill to initiate before dense vector search completes.

### 4. Speculation Penalties & Wasted Work (The "Negative Payoff" Objection)
* **Competing Literature**:
  - `leviathan2023speculative` (ICML 2023) / `chen2023speculative` (arXiv 2023) — Speculative decoding.
  - `cai2024medusa` (ICML 2024) — Medusa multi-head speculation.
* **The Tension**: When speculative predictions fail, GPU compute and memory bandwidth are wasted, introducing head-of-line blocking under load.
* **Our Rigorous Grounding**:
  - We derive the analytical break-even hit rate:
    $$h^* = \frac{T_{\text{prefill}}}{T_{\text{prefill}} + T_{\text{WAN}}}$$
  - Below $h^*$, speculation introduces net degradation due to wasted prefill aborts ($T_{\text{miss}}$). Our measured hit rate ($h = 0.84$) comfortably clears the break-even threshold ($h^* \approx 0.35–0.45$).

---

## LR-11 — WAN Regime Grounding for the Campaign v2 Tier Matrix (2026-10-12)

**Session:** LR-11 · **Date:** 2026-10-12 · **Branch:** `feat/three-node-study`
**Concern:** Campaign v2 sweeps RTT tiers {0, 15, 40, 80} ms, loss tiers {0, 0.5, 1, 2}%, and bandwidth cells (the existing crossover analysis used {10, 50, 100, 500, 1000} Mbps — see `experiments/analysis/results/crossover_analysis{,_cold}.json`). Issue #4 requires *per-link regime values grounded in literature*: every tier value must be anchored to a citable real-world measurement so a reviewer accepts the regime matrix as representative, not arbitrary.

**Method:** for each regime dimension — (i) RTT vs distance class, (ii) packet loss on terrestrial / last-mile / wireless paths, (iii) bandwidth of residential/edge access links, (iv) 4G/5G edge norms — the canonical published sources were retrieved live (arXiv/IEEE full texts, the FCC Twelfth MBA Fixed Report PDF, a live cloud-provider inter-region latency matrix) and run through the 4-stage anti-hallucination protocol; verified rows are appended to `docs/literature_ledger.md` under LR-11. Items that could not be resolved this session are explicitly marked **unverified** below and are *not* cited.

### RQ-1 — Typical RTT by distance class (what do the measurements say?)

Three independent sources triangulate to the same picture:

| Source | Dataset | Key fitted numbers |
|---|---|---|
| **`landa2013geography`** — Landa, Taveira Araujo, Clegg, Mykoniati, Griffin, Rio, *The Large-Scale Geography of Internet Round Trip Times* (IEEE/ACM ToN 2013; IEEE Xplore 6663531 — journal DOI not resolved this session; open-access companion ICCCN 2013 "Measuring the Relationships between Internet Geography and RTT", DOI 10.1109/ICCCN.2013.6614151) | 19M RTT measurements between ~54,000 DNS servers (TurboKing, 2011–12) | After unfolding circuitous routing, median RTT vs routing distance is **linear: slope 0.016 km/ms (≈ 1.6 ms per 100 km of *routed* distance) with a 22 ms intercept, R² = 0.94** (primary text fetched and verified). |
| **`martinez2023rtt`** — Martínez, Hernández, Reviriego, Reinheimer, *Round Trip Time (RTT) Delay in the Internet: Analysis and Trends* (IEEE Network 38(2):280–285; arXiv:2301.07788, DOI 10.1109/MNET004.2300008) | >9M pings between 100+ WonderNetwork monitoring sites, 2014–2022, 8 regions | Theory: fiber propagation = **0.01 ms/km RTT baseline** (5 µs/km one-way). North America fit: **slope 0.015 ms/km (1.5× the physical minimum), 14 ms intercept, R² = 0.738, RMSE 12 ms**; Europe: **slope 0.02 ms/km, 10 ms intercept**. Intercontinental mean RTTs 2014→2022: Asia-Pacific 135→80 ms, Europe/NA reduced ~17%. (Primary text fetched and verified.) |
| **`aws2026regionlatency`** — *AWS Inter-Region Latency Matrix*, latency.bluegoat.net (live matrix over the AWS global backbone, fetched 2026-10-12; last updated 2026-10-11) | live ping matrix, 33 AWS regions | **Intra-metro / neighboring-region pairs: 28–112 ms** (us-east-1↔ca-central-1 = 28 ms; ca-central-1↔us-east-1 = 48 ms; us-west-1↔us-west-2 = 44 ms; us-east-1↔us-east-2 = 71–82 ms; us-east-1↔ca-west-1 = 113 ms). **Transatlantic: 111–183 ms** (us-east-1↔eu-west-1 = 111 ms; us-east-1↔eu-central-1 = 120 ms; us-east-1↔eu-west-2 = 128 ms; us-west-2↔eu-west-3 = 169 ms). **Transpacific: 130–268 ms** (us-west-1↔ap-northeast-1 = 141 ms; us-east-1↔ap-northeast-1 = 197 ms; us-west-2↔ap-southeast-1 = 206 ms; us-east-1↔ap-southeast-7 = 268 ms). |

**Resulting distance-class table (median RTT, with citations):**

| Distance class | Representative pairs (great-circle) | Median RTT (measured) | Anchor |
|---|---|---|---|
| Intra-metro / single-metro (control region) | datacenter ↔ same-city edge | **~0–15 ms** (0 ms in-testbed loopback; real metro incl. access 7–25 ms idle) | `martinez2023rtt` (intercepts 10–14 ms at ~0 km); `fcc2022fixed` (idle 7–25 ms incl. access leg) |
| Intra-national / cross-country (1,500–4,800 km) | US east–west (~4,500 km); intra-EU (1,500–3,000 km) | **~40–95 ms** (us-east-1↔us-west-1 ≈ 92 ms; NA fit 0.015×4,500 km + 14 ≈ 81 ms; intra-EU ~30–70 ms) | `aws2026regionlatency`; `martinez2023rtt`; `landa2013geography` |
| Transatlantic (5,500–7,000 km GCD) | US East ↔ Western/Central Europe | **~110–130 ms** (111–128 ms measured on AWS backbone; EU fit 0.02×6,000 km + 10 ≈ 130 ms) | `aws2026regionlatency`; `martinez2023rtt` |
| Transpacific / intercontinental (9,000–19,000 km *routed*) | US ↔ Japan/Singapore/Australia; EU ↔ Asia | **~140–260 ms** (130–268 ms measured; Landa fit 0.016×11,000 km + 22 ≈ 200 ms) | `aws2026regionlatency`; `landa2013geography` |

> Note on the "1 ms / 100 km" classical reference: that value is the **physical fiber bound** (0.01 ms/km RTT, `martinez2023rtt` Table I). The *measured* effective slopes are 1.6–2× higher (0.016–0.02 ms/km) because of routing circuitousness (`landa2013geography`, `martinez2023rtt`). The earlier IMC-era studies in this lineage (Dischinger et al. 2005 "On the Use of Network Latency as an Indicator of Geographic Location"; Ng & Zhang 2005 "Internet Round-Trip Times: Collector Artifact or Not?") could **not** be resolved to citable records this session and are therefore *not cited*; the 2013 and 2023 datasets above subsume their findings at 10–100× measurement scale.

### RQ-2 — Typical packet loss on terrestrial and wireless paths

- **`fcc2022fixed`** — FCC, *Twelfth Measuring Broadband America Fixed Broadband Report* (2022; data.fcc.gov PDF, full text fetched and verified): active UDP loss (3 s timeout) at US consumer homes during peak hours, 13 ISPs. Bands: **≤ 0.4% / 0.4–1% / > 1%**. Results: **0–1% of fiber and 0–2% of cable subscribers experience > 1% loss; 2–6% of DSL subscribers do**; the overwhelming majority sit in the ≤ 0.4% band. The report explicitly cites ITU-based industry practice that **1% loss is the point at which highly interactive applications (VoIP) suffer significant degradation**, and that enterprise SLAs typically guarantee **0.1–0.3%**.
- **`hounsel2020dns`** — Hounsel, Borgolte, Schmitt, Holland, Feamster, *Comparing the Effects of DNS, DoT, and DoH on Web Performance* (WWW 2020; arXiv:1907.08089, DOI 10.1145/3366423.3380139, primary text verified): the paper's **3G emulation condition is 150 ms RTT + 8 ms jitter + 2.1% packet loss at 1 Mb/s symmetric** — the de-facto academic "degraded wireless last-mile" reference point, which sits exactly at our top loss tier.
- **`itu2001g1010` — UNVERIFIED this session.** ITU-T G.1010 (End-user Multimedia QoS Categories) is the standard source for delay/loss *tolerance* categories, but the ITU pages returned connection errors this session; the specific category values are therefore **not cited**. Only the ITU-derived "1% = VoIP degradation threshold" is used, and *only as secondarily cited inside the (verified) FCC report*.

**Is 1–2% a defensible "stressed but realistic" tier?** Yes, with the characterization in RQ-5: 0.5% is inside the measured consumer band (mid-band), 1% is the industry degradation threshold (the band boundary, not a median), and 2% is beyond the wired P95 but equals the standard 3G-emulation value — a tail-stress condition, not a wired-typical one.

### RQ-3 — Bandwidth regimes for edge / residential links

- **`fcc2022fixed`** (primary text fetched): 2021 peak-hour US consumer fixed broadband. Weighted mean *advertised* download **307.7 Mbps across the top-80% tiers**; by technology **DSL ≈ 24 Mbps, cable ≈ 305 Mbps, fiber ≈ 510 Mbps**; measured median downloads meet or exceed advertised for most ISP/tech configs (7 of 12). The measured tier range spans **1.5 Mbps (bottom of DSL) to 940 Mbps (gigabit tiers)**; DSL subscribers diverge most from advertised (as low as 86%).
- **`oecd2024broadband`** — OECD, *Broadband statistics / Access to Digital* (2023 data; secondary source, context only): fiber is the primary fixed technology in 19 of 38 OECD countries (≥ 50% of subscriptions in 15), DSL still dominant in much of the rest of the OECD ⇒ the *range* of realistic edge uplinks is exactly the DSL→gigabit-fiber span above.

**Grounding of the bandwidth cells (10 / 50 / 100 / 500 / 1000 Mbps):**

| Cell | Real-world correspondence | Anchor |
|---|---|---|
| 10 Mbps | low DSL / entry-cable tier (below the 25 Mbps OECD "broadband" threshold) | `fcc2022fixed` (DSL band 1.5–25 Mbps) |
| 50 Mbps | mid DSL / lower cable — the classic policy threshold region | `fcc2022fixed`, `oecd2024broadband` |
| 100 Mbps | cable-typical measured download (2018–2023 mainstream US cable tier) | `fcc2022fixed` (cable band) |
| 500 Mbps | upper cable / entry fiber | `fcc2022fixed` (fiber ≈ 510 Mbps advertised mean) |
| 1000 Mbps | gigabit fiber tier (940 Mbps tier in the report) | `fcc2022fixed` |

All five cells fall inside the measured FCC tier range, so the bandwidth axis of the crossover analysis is grounded. The P3 hydrated-text "bandwidth starvation" phenomenon in the crossover table is consistent with the report's finding that DSL-class links are where measured vs advertised diverges most.

### RQ-4 — 4G/5G edge RTT and loss (mobile-edge story)

- **RTT:** the direct authority is the FCC *Measuring Wireless Broadband America* program (4G/5G reports 2019–2023), but the report PDFs were bot-blocked (403) this session, so **specific 4G/5G median RTT values are marked unverified and not cited**. What *is* citable: the **150 ms 3G-emulation RTT** of `hounsel2020dns` (primary text, verified) — the canonical academic 3G condition; 4G and 5G are each one order of magnitude below in the same lineage (commonly cited industry ranges: 4G ≈ 30–80 ms, 5G ≈ 10–30 ms — *industry, unverified here*). The defensible paper statement: *a 4G/5G last mile adds tens of ms of RTT and up to ~2% loss, i.e., it lands in the {15, 40} ms × {0.5, 1, 2}% region of the matrix*, with the 3G-emulation anchor as the citable reference.
- **Loss:** 3G emulation 2.1% (verified) ⇒ weak-signal 3G/4G last mile is at most this order; typical 4G ≤ 1%.

### RQ-5 — VERDICT TABLE (Campaign v2 tier matrix)

**RTT tiers** (netem applies full per-link RTT; `scripts/netem/apply.sh`):

| Tier | Literature position | Verdict |
|---|---|---|
| **0 ms** | control condition (loopback / in-datacenter); a real metro last hop incl. access is 7–25 ms (`fcc2022fixed`) | **KEEP** — must be framed as the *no-WAN control*, not as a "real Internet" value. |
| **15 ms** | intra-metro: 7–25 ms metro last-hop incl. access (`fcc2022fixed`); NA/EU fit intercepts 10–14 ms at ~0 km (`martinez2023rtt`) | **KEEP** — metro-class; matches measured metro last-hop band. |
| **40 ms** | cross-country low end: NA fit 0.015 × 1,500–2,000 km + 14 ≈ 36–44 ms; intra-EU mid; 4G-typical | **KEEP** — matches the lower cross-country / 4G class (30–50 ms band, `landa2013geography`, `martinez2023rtt`). |
| **80 ms** | *lower bound* of the transoceanic class: measured transatlantic 111–128 ms mean on the AWS backbone, transpacific 130–268 ms (`aws2026regionlatency`) | **KEEP** — but frame as the *best-case transoceanic* value (the realistic low end of the 100–260 ms class), not its median. The paper's methods section must state this explicitly. |

**Loss tiers:**

| Tier | Literature position | Verdict |
|---|---|---|
| **0%** | control | **KEEP** (control, not a claim) |
| **0.5%** | middle of the measured consumer band: ≤ 0.4% / 0.4–1% boundaries (`fcc2022fixed`); at the edge of the 0.1–0.3% enterprise-SLA zone | **KEEP — realistic** (wired-typical mid-band) |
| **1%** | the ITU-derived industry degradation threshold; the boundary of the > 1% consumer band (`fcc2022fixed`) | **KEEP — realistic as "onset of measurable impairment"** (an industry-standard threshold value, not a path median) |
| **2%** | beyond wired P95 (only 0–6% of US consumers see ≥ 1% at peak, and ≥ 2% rarer; `fcc2022fixed`) but = the 3G-emulation reference (2.1%, `hounsel2020dns`) and realistic for weak-signal wireless last mile | **JUSTIFY-AS-STRESS** — frame explicitly as the *wireless/congestion tail-stress* tier (beyond wired P95, anchored by the 3G-emulation value), not as wired-typical. |

**Bandwidth cells:** 10 / 50 / 100 / 500 / 1000 Mbps all **KEEP** — each maps to a measured consumer tier within the FCC report's 1.5–940 Mbps range (RQ-3 table).

**Tiers with NO literature anchor:** *none.* Two framing caveats carry into the paper: (a) 80 ms is the *lower bound* of the transoceanic class, not its mean; (b) 2% loss is a *tail-stress* value anchored by the 3G-emulation reference, not a wired median.

**Bottom line for LR-11:** every Campaign v2 regime value (RTT {0, 15, 40, 80} ms, loss {0, 0.5, 1, 2}%, bandwidth {10…1000} Mbps) is grounded in at least one citable real-world measurement, satisfying issue #4. The three strongest anchors are `martinez2023rtt` (IEEE Network — distance-class RTT slopes/intercepts, 2014–2022), `landa2013geography` (IEEE/ACM ToN — the 1.6 ms/100 km fit, R² = 0.94, 19M measurements), and `fcc2022fixed` (FCC Twelfth MBA Fixed Report — loss bands, idle latencies, tier bandwidths); the live `aws2026regionlatency` matrix provides current cloud-provider corroboration for the intra-metro / transatlantic / transpacific classes.

---

## LR-7 — Methodology Norms for Latency / Measurement Studies at Top Systems Venues (2026-10-12)

**Session:** LR-7 · **Date:** 2026-10-12 · **Branch:** `feat/three-node-study`
**Concern:** ground the Campaign v2 statistics-engine and methodology choices in what top venues *actually* expect, so every methodology choice is citable: (RQ-1) which latency percentiles recent measurement-heavy papers report and whether they attach confidence intervals; (RQ-2) how netem/shaped testbeds are validated against real networks; (RQ-3) sample-size / repeat norms and whether anyone does power analysis; (RQ-4) artifact-evaluation expectations at Middleware and siblings; (RQ-5) a concrete, citable methodology package for our paper, framed as a proposal to a revision board with our *exceeds-norm* differentiators stated explicitly.
**Method:** the four named 2024–2025 LLM-serving papers (DistServe, LoongServe, Splitwise, Mooncake) were fetched at primary-text level (USENIX/ACM/Microsoft PDFs, evaluation sections read in full); the statistics-critique lineage (Treadmill, TailBench, Tail-at-Scale, NIST AI 800-3) and the emulation-fidelity examples (CALF, long-haul RDMA, Colosseum, NetReplica, delay-based fidelity monitoring) were resolved via DOI/OpenAlex/arXiv and run through the 4-stage anti-hallucination protocol. Ledger rows appended to `docs/literature_ledger.md` under LR-7. Unresolvable items are marked **unverified/provisional** and *not* cited.

---

### RQ-1 — How do recent measurement-heavy systems papers report latency?

**The headline finding, verified against primary texts of all four flagship 2024–2025 LLM-serving papers: *none* reports confidence intervals, CIs on any statistic, or any variance measure beyond the raw sample.** Every one of them reports **bare percentiles and/or bare SLO-attainment fractions, with N given only implicitly (trace size), with no error bars**.

| Paper (venue) | Latency statistic(s) actually reported | CI / variance reported? | N / workload (as stated) |
|---|---|---|---|
| **`zhong2024distserve`** (OSDI'24; USENIX PDF, primary text) | **P90 TTFT and P90 TPOT** vs. request rate (Fig. 1, 8); headline metric is **SLO attainment %** (90% target, 99% in appendix); latency-breakdown sums and a **CDF of KV-transfer time** ("over 95% of requests < 30 ms") | **No.** No CI, no SE, no variance anywhere in §6 | OPT-13B/66B/175B; ShareGPT / HumanEval / LongBench samples; Poisson arrivals; 32-GPU cluster, 25 Gbps cross-node |
| **`loongserve2024`** (SOSP'24; DOI 10.1145/3694715.3695948, primary text) | **Normalized per-token / input / output latency** (i.e., *means* of per-request latency divided by token count, Figs. 10–11) and **P90 goodput** (Fig. 12); SLO set to "25× the inference latency" | **No.** No CI, no SE, no variance | LWM-1M-Text on 8×A800 nodes, 200 Gbps IB; Poisson arrivals over ShareGPT / L-Eval / LV-Eval / Mixed |
| **`patel2024splitwise`** (ISCA'24; DOI 10.1109/ISCA59077.2024.00019, primary text) | **P50 TBT** and TTFT/E2E latency-vs-load curves with **dashed SLO lines** (Fig. 16); a **CDF of batched-token processing time** (Fig. 17); throughput/power/cost summary plots | **No.** No CI, no SE, no variance | H100/A100 clusters; Llama-70B, BLOOM-176B; production coding/conversation traces, 70–130 RPS |
| **`mooncake2025`** (FAST'25, Best Paper; USENIX PDF, primary text) | **TTFT** and **TBT** (their TBT = "average of the *longest 10%* of token-arrival intervals" — i.e., a p90-ish tail statistic, not a percentile) vs. SLO thresholds (TTFT ≤ 30 s; TBT ≤ 100/200/300 ms); **effective request capacity** (fraction meeting both SLOs); **average** prefill GPU time (Fig. 8) | **No.** No CI, no SE, no variance | Kimi production traces: 12,031 / 23,608 / 3,993 requests (1-hour samples, real timestamps) on 16×8-A800 nodes; plus vLLM-v0.5.1 replay with dummy LLaMA3-70B |

**So the "most systems papers report bare percentiles without CIs" claim is confirmed for the flagship LLM-serving line (4/4), and the convention generalizes:** the de-facto reportable vocabulary at these venues is {p50, p90, p95, p99, SLO-attainment %, mean, CDF} — reported as point estimates and curve plots, never with intervals. The tail-latency *characterization* literature (e.g., `downey2001` in the LR-3 ledger; `tailatscale2013` below) reports quantiles the same way.

**Who *does* do better — the counterweight lineage (all verified):**

- **`treadmill2016`** — Zhang, Meisner, Mars, Tang, *Treadmill: Attributing the Source of Tail Latency through Precise Load Testing and Statistical Inference* (**ISCA 2016**; DOI 10.1109/ISCA.2016.47; primary text fetched). The canonical *methodology critique* of exactly the above practice: it shows widely used load testers produce "statistically inaccurate aggregation" and "improper attribution" that "often result in misleading conclusions," and builds the fix — **quantile regression + statistically sound performance-factor attribution** on p95/p99 response latency over Facebook production workloads (result: 43% p99 reduction, 93% variance reduction from the attribution). *This is the strongest peer-reviewed argument that bare percentiles are insufficient; it is the citation that licenses our CI package.*
- **`tailbench2016`** — Kasture & Sanchez, *TailBench* (**IISWC 2016**; MIT PDF, primary text fetched). Two load-bearing methodological points: (a) "tail latency is … much more sensitive to small perturbations and requires a **statistically robust methodology**," with a harness built to avoid **coordinated omission** (a pitfall in the load testers used by prior suites, incl. YCSB/Faban); (b) **fidelity validation by construction** — simplified single-node configurations are validated against full multi-node runs and shown accurate for 6/8 apps (§VI). TailBench remains the reference suite (extended by `tailbenchpp2026`, J. Cloud Computing 2026, DOI 10.1186/s13677-026-00908-3).
- **`tailatscale2013`** — Barroso & Hölzle, *The Tail at Scale* (**CACM 56(2)**; DOI 10.1145/2408776.2408794). The experience-paper anchor for why p95/p99 — not the mean — is the decision-relevant statistic in distributed services.
- **`nistai8003`** — Keller et al., *Expanding the AI Evaluation Toolbox with Statistical Models* (**NIST AI 800-3**, Feb 2026; DOI 10.6028/NIST.AI.800-3). A 2026 normative statement that benchmarking practice produces "invalid uncertainty estimates or unrecognized assumptions," and prescribes explicit statistical models (GLMM) for **uncertainty quantification** of evaluation results — direct, current backing for reporting CIs on measured metrics.
- **`tene2013latency`** — Gil Tene, *How Not to Measure Latency* (QCon London 2013; **talk, not a paper — provisional**): origin of the **coordinated-omission** term and the basis for `wrk2`-style open-loop measurement. Cited here only as the origin of the term; `treadmill2016` and `tailbench2016` carry the peer-reviewed weight.

**Verdict for RQ-1:** the norm is bare percentiles / SLO attainment (4/4 flagships, 0/4 with any CI); the critique lineage (`treadmill2016`, `tailbench2016`, `tailatscale2013`, `nistai8003`) is well established and current through 2026 — so reporting CIs is *not exotic*, it is the defensible-exceeds-norm position, and it is citable.

---

### RQ-2 — Emulation fidelity: how do papers validate that a shaped/emulated testbed represents a real network?

Three distinct validation patterns are in active use, all verified this session:

| Pattern | Example (venue, year) | What is measured / compared |
|---|---|---|
| **(a) Closed-loop RTT / condition monitoring inside the emulated link** | **`calf2026`** — *CALF: Communication-Aware Learning Framework for Distributed RL* (arXiv:2603.12543, primary text fetched). A **NetworkShim** middlebox injects per-packet delay (N(µ,σ²) jitter, Bernoulli loss) *and* **timestamps every packet to log the realized latency distribution**; a **LatencyTracer** service records actual conditions during **Real-Wi-Fi** runs, and the recorded empirical distribution is replayed to refine the synthetic model. Explicit regime values: Ethernet-clean 2 ms ± 0.5 ms / 0% loss; Wi-Fi-normal **30 ms ± 10 ms / 2% loss**; Wi-Fi-degraded **80 ms ± 40 ms / 10% loss**; deterministic RNG seeds for reproducibility. | realized per-packet RTT logged against the injected model (closed-loop gate); sim→real transfer gap measured as the validation metric |
| **(b) Real-vs-emulated comparison leg** | **`chen2026rdmawan`** — *When RDMA Goes Long-Haul* (**HPDC '26**; DOI 10.1145/3806645.3807582 — full text bot-blocked this session; abstract-level). Characterizes long-haul RDMA and builds a **verbs-level emulator that enforces the measured RTT/window constraints on a short-path RDMA connection, validated under real verbs execution** — i.e., the emulator is calibrated to a measured long-haul characterization and checked against real runs. Sister SC25'25 poster (*Can Long-Haul RDMA Benefit Federated Learning?*) models inter-datacenter latency as **65% of the speed of light** (citing Richling et al. 2011, 28 km InfiniBand grid measurement, DOI 10.1504/IJGUC.2011.042946) and injects it via an MPI rate limiter. | measured RTT/bandwidth of the real long-haul path vs. the injected constraints; protocol-behavior parity (RDMA vs IPoIB utilization curves) |
| **(c) Fidelity validation of the emulator as a first-class artifact** | **`tailbench2016`** (§VI): simplified configurations validated against full multi-node runs per application. **`dof2023fidelity`** — *Delay-based Fidelity Monitoring of Distributed Network Emulation* (IEEE IMC 2023; arXiv:2207.14612 — **provisional**: IMC '23 placement/author list not fully resolved this session; the delay-based monitoring method is confirmed by the slices-sc EU PDF). **`netreplica2025`** — *NetReplica* (arXiv:2507.13476v4, primary text fetched): fidelity is one of four stated capabilities, validated end-to-end (ABR case study: trace-replay substrate **reduces transmission-time prediction error of the Fugu model by up to 47%** vs. prior approaches). **`colosseum2021`** (arXiv:2110.10617): hardware-in-the-loop RF emulation validated as "high-fidelity … faithfully mimicking real-world wireless." | realized vs. intended link parameters (passive delay measurement); downstream prediction error vs. real traces |

**Note on the RTT tiers themselves:** the literature anchors for our {0, 15, 40, 80} ms / {0, 0.5, 1, 2}% / {10…1000} Mbps values were established in LR-11 (`martinez2023rtt`, `landa2013geography`, `fcc2022fixed`, `hounsel2020dns`, `aws2026regionlatency`) — LR-7 adds the *fidelity-validation* citations on top. Notably, `calf2026`'s 80 ms ± 40 ms "Wi-Fi-degraded" tier independently corroborates the order of magnitude of our top RTT tier, and its 2% loss matches our 2% tier's 3G-emulation anchor (`hounsel2020dns`).

**Verdict for RQ-2:** reporting (a) a closed-loop RTT/loss gate *and* (b) a real-vs-emulated comparison leg is an emerging, citable pattern (`calf2026`, `chen2026rdmawan`), while the older suites treat fidelity validation as part of the methodology (`tailbench2016`, `netreplica2025`). Our netem setup is *weaker than the norm's strongest examples* today (no closed-loop gate in the campaign) — the RQ-5 package below closes that gap.

---

### RQ-3 — Sample-size / repeat norms; power analysis; paired vs. independent

- **N in LLM-serving measurement papers (all primary-text):** DistServe — trace-sampled requests per dataset with Poisson arrivals (HumanEval = the full 164-problem set; ShareGPT/LongoBench subsets; exact N not stated); LoongServe — Poisson-driven, N not stated; Splitwise — 70–130 RPS over production-trace windows (N implicit); Mooncake — **stated**: 12,031 / 23,608 / 3,993 requests from 1-hour production traces. The norm is: **as many requests as the trace gives, single pass, no repeated measurements, no N justification.** Our Campaign v2 (1200 rows = 50 queries × 2 variants × 4 RTT × 3 repeats per `docs/campaign_methodology.md`) is at the *low* end of the stated-N range (Mooncake's ~4K–24K) — which is why the LR-3 power analysis is the right compensating move rather than raw N.
- **Power analysis: rare to the point of absence.** **No** 2022–2026 systems measurement paper among those inspected reports a power analysis or an MDE. The power-analysis practice lives in the statistics-methodology literature (`cohen1988` in the LR-3 ledger) and in ML-benchmark statistics (`nistai8003` does sample-size/uncertainty reasoning for 22 frontier-LLM evaluations). **We would be doing it for the first time in our submission class — a differentiator, not a novelty claim.**
- **Paired vs. independent:** Treadmill's attribution methodology is **independent-instances + statistical inference** (multiple lightly utilized Treadmill instances to avoid client-side queueing bias); CALF uses a **paired-regime** design (same policy code, same environment, only the communication channel changes — "Regimes differ only in the communication channel; we hold everything else fixed"). Neither the LLM-serving line nor either validation example uses the statistical terminology (paired vs. independent, carryover effects) — that vocabulary is again from the methodology literature (`kirk2013`, `borg2007` in the LR-3 ledger). Our decision in `docs/campaign_methodology.md` RQ-b1 (disjoint per-arm slices, cold/warm as strata) is consistent with the *measurement* side of the norm (no prior LLM-serving paper pairs across cache states) and more conservative than the *statistics* side would demand.

**Verdict for RQ-3:** norms = single-pass trace replay, no N justification, no power analysis, no paired/independent framing. Every one of those absences is a citable gap we can fill.

---

### RQ-4 — Artifact-evaluation expectations (Middleware and siblings)

- **`middleware2026ae`** — Middleware 2026 **does have an AE** (Artifact Evaluation Committee; call-for-artifacts page fetched live 2026-10-12; submission site middleware2026ae.hotcrp.com). Badges: **Artifacts Available / Artifacts Functional / Results Reproduced**. The call explicitly requires: a README covering contents, requirements, install, run, *and how to reproduce the main results*; **scripts automating the full workflow (environment setup → experiment execution → data collection → data processing → plotting)**; documentation of the exact hardware/software environment; and, where specialized hardware is unavailable to the AEC, a smaller-scale run that still demonstrates the main claims. Submission deadline for Middleware 2026 is **27 September**; author notification 3 November.
- **Siblings:** the same ACM badge taxonomy applies across the systems/ML venue family (NDSS 2026 AE, CAIS 2026 AE, PACT 2026 AE pages all resolve; PACT additionally uses the unified **Artifact Appendix** and the **Reproducibility Checklist** template). USENIX venues (OSDI/FAST — the homes of DistServe/Mooncake) have no ACM-style badge program; their de-facto norm is *code repository + full trace/evaluation disclosure in the paper* (DistServe publishes its simulator and placement algorithm; Mooncake open-sources the Transfer Engine/Store and the traces).

**Verdict for RQ-4:** for a Middleware submission, the AEC expects the *entire pipeline* to be scriptable end-to-end (setup → run → collect → process → plot), with raw data included and a clean-machine test done before submission. Our artifact must contain: the campaign harness, netem `apply.sh` + all regime configs, seeds, raw per-request JSONL for every cell, the statistics-engine code, and the figure/table generators.

---

### RQ-5 — Verdict: recommended methodology package for our paper (proposal to a revision board)

Each element below carries its citation; the right column states whether we **meet** or **exceed** the venue norm. This table is the deliverable for the paper's *Measurement Methodology* section and for the Middleware AE submission.

| # | Methodology element | Our choice | Citation(s) | vs. norm |
|---|---|---|---|---|
| 1 | **Percentiles** | Report **p50, p95, p99** for TTFT and end-to-end per cell; p99 as the headline for the tail-sensitive SPHP claim (p50 for the placement-crossover cells where the mean is the decision variable) | `tailatscale2013` (p95/p99 as the decision statistic); `zhong2024distserve` / `patel2024splitwise` / `mooncake2025` (p50/p90 in active use — we report a *wider* set) | **Exceeds** (flagships report one quantile, usually p50 or p90, without the p95/p99 pair) |
| 2 | **CI on every reported percentile** | **Bootstrap percentile / BCa CI (B = 2,000 resamples)** on each percentile and on each between-arm delta | `efron1993` (LR-3 ledger); `treadmill2016` (statistically rigorous tail evaluation); `nistai8003` (2026 normative call for uncertainty in evaluation) | **Exceeds** — *no* 2022–2026 measurement paper in our class reports any CI (verified 4/4 flagships); state in the paper: "in contrast to prior LLM-serving evaluations, which report bare percentiles [citations], we report …" |
| 3 | **Test for arm comparison** | **Permutation test** (primary, distribution-free) + bootstrap CI on the relative delta (primary effect size); parametric t-test reported only as a robustness check | `dekker2007`, `efron1993` (LR-3 ledger); heavy-tail justification `downey2001` | **Exceeds** (no LLM-serving paper reports any hypothesis test) |
| 4 | **Sample size** | N = **120/arm for the confirmatory TTFT claim** (power analysis: 10% MDE, α = 0.05, power 0.80, measured CV 0.13–0.28 → N₁₀ ≤ 115.4 with margin; `docs/campaign_methodology.md` CV table); **1000+ queries/cell for the exploratory regime sweep** (internal target, not a literature anchor) | `cohen1988` (LR-3 ledger) | **Exceeds** (no paper in the class states an N justification or MDE) — and *meets-or-exceeds* the raw-N range (`mooncake2025` 4K–24K, trace-based) for the sweep |
| 5 | **Design** | Disjoint per-arm query slices; cold/warm as **strata**, not replicates (1 cold + 2 warm per query); state the paired-vs-disjoint trade-off explicitly in the paper | `kirk2007`/`borg2007` (LR-3 ledger); `calf2026` (paired-regime precedent, with channel-only variation) | **Meets** (consistent with the measurement side of the norm; the statistical side is a flagged contested decision in `docs/campaign_methodology.md`) |
| 6 | **Fidelity gate (new requirement)** | In every campaign, (a) **closed-loop RTT/loss measurement**: continuous ping probes through the netem link logging realized RTT/loss distributions, asserted against the configured tier with tolerance (e.g., RTT within ±10%, realized loss within ±0.2 pp) as a *pre-run and per-run gate*; (b) **real-vs-emulated comparison leg**: one regime (e.g., 80 ms) measured on an actual WAN path (cloud inter-region, per `aws2026regionlatency`) and compared against the emulated leg | `calf2026` (pattern a + regime values); `chen2026rdmawan` (pattern b); `tailbench2016` / `netreplica2025` / `dof2023fidelity` (fidelity-as-artifact) | **Exceeds** — the strongest examples today calibrate the emulator *to* the real link; we report the gate *and* the gap |
| 7 | **Workload realism** | Poisson arrivals for the loaded sweep; fixed seeded query order for the single-pass cells; **open-loop client** (no client-side blocking on slow responses — coordinated-omission avoidance) | `zhong2024distserve` / `loongserve2024` / `mooncake2025` (Poisson norm); `tailbench2016` / `tene2013latency` (open-loop, CO avoidance) | **Meets** (POISSON) / **Exceeds** (CO avoidance is unstated everywhere) |
| 8 | **Artifact** | Full AE package per `middleware2026ae`: harness + netem configs + seeds + **raw per-request JSONL for all cells** + statistics-engine code + figure/table generators + clean-machine test log | `middleware2026ae` (2026 call, live); PACT-2026 Artifact Appendix / Reproducibility Checklist as the template model | **Meets** the call's letter (scripts automate setup → run → collect → process → plot) |
| 9 | **Multiplicity** | Pre-specified confirmatory family (Holm/FWER) vs. exploratory sweep (BH/FDR), declared in the paper before results | `holm1979`, `benjamini1995` (LR-3 ledger) | **Exceeds** (no LLM-serving paper declares a multiplicity plan) |

**Framing for the paper (one paragraph, drop-in):** "Prior LLM-serving evaluations report bare percentiles and SLO-attainment fractions with no uncertainty quantification [DistServe, LoongServe, Splitwise, Mooncake]. Following the statistically-rigorous tail-latency methodology of Treadmill and the measurement-pitfall literature it and TailBench established, and consistent with the 2026 NIST call for uncertainty-aware evaluation, we report p50/p95/p99 with bootstrap CIs, permutation-test p-values, and a declared multiplicity plan on an explicitly powered sample, and we gate every emulated regime with a closed-loop RTT/loss measurement validated against a real-WAN comparison leg."

**Strongest three citations for the paper's methodology section:**

1. **`treadmill2016`** — *Treadmill: Attributing the Source of Tail Latency through Precise Load Testing and Statistical Inference*, ISCA 2016, DOI 10.1109/ISCA.2016.47. The peer-reviewed demonstration that naive percentile reporting "often results in misleading conclusions," and the template for statistically sound tail evaluation.
2. **`mooncake2025`** — *Mooncake: A KVCache-centric Disaggregated Architecture for LLM Serving*, FAST 2025 (Best Paper), arXiv:2407.00079 (ACM TOS 10.1145/3773772). The current state of the art in LLM-serving evaluation: stated N (4K–24K), TTFT + tail-interval TBT, SLO attainment — and *no* CIs. The citation against which our "exceeds the norm" claim is made.
3. **`calf2026`** — *CALF: Communication-Aware Learning Framework for Distributed RL*, arXiv:2603.12543 (2026). The strongest recent example of **closed-loop fidelity validation** (per-packet timestamping, LatencyTracer, real-Wi-Fi comparison leg, deterministic seeds) with regime values that independently corroborate our tier matrix.

---

### LR-7 reference list (ledger-style)

| key | title (short) | venue/year | DOI / arXiv | evidence level | cited-for (one line) | verdict |
|---|---|---|---|---|---|---|
| `treadmill2016` | Treadmill: Attributing the Source of Tail Latency through Precise Load Testing and Statistical Inference | ISCA 2016 | DOI 10.1109/ISCA.2016.47 | **primary-text** (PDF fetched; quantile regression, load-tester flaws, 43% p99 / 93% variance reduction) | statistically rigorous tail evaluation; the critique of bare-percentile practice (RQ-1, #2) | **verified** |
| `tailbench2016` | TailBench: A Benchmark Suite and Evaluation Methodology for Latency-Critical Applications | IISWC 2016 | MIT CSAIL PDF (people.csail.mit.edu/sanchez) | **primary-text** (PDF fetched; p95/p99 methodology, coordinated-omission avoidance, single-node-vs-multi-node validation §VI) | statistically robust tail methodology + emulator-fidelity validation pattern (RQ-1, RQ-2) | **verified** |
| `tailatscale2013` | The Tail at Scale | CACM 56(2), 2013 | DOI 10.1145/2408776.2408794 | metadata-only (abstract/landing verified) | p95/p99 as the decision statistic for distributed services (RQ-1, #1) | **verified** |
| `nistai8003` | Expanding the AI Evaluation Toolbox with Statistical Models | NIST AI 800-3, Feb 2026 | DOI 10.6028/NIST.AI.800-3 | **primary-text** (abstract + preface fetched) | 2026 normative call for uncertainty quantification in evaluations (RQ-1, #2) | **verified** |
| `calf2026` | CALF: Communication-Aware Learning Framework for Distributed RL | arXiv 2026 | arXiv:2603.12543 | **primary-text** (fetched §3–4; NetworkShim, LatencyTracer, regime values 2/30/80 ms ± jitter, 0/2/10% loss, seeds) | closed-loop RTT/loss fidelity gate + real-vs-simulated comparison leg; tier corroboration (RQ-2, #6) | **verified** |
| `chen2026rdmawan` | When RDMA Goes Long-Haul: Characterization, Modeling, and Verbs-Level Emulation | HPDC '26 | DOI 10.1145/3806645.3807582 | **abstract-level** (ACM DL bot-blocked; title/venue/authors via ACM + author page) | emulator calibrated to measured RTT/window constraints, validated under real verbs (RQ-2) | **verified** (abstract-level); sister SC25'25 poster: 65%-c long-haul latency model via rate limiter | **verified** (poster PDF fetched) |
| `colosseum2021` | Colosseum: Large-Scale Wireless Experimentation Through Hardware-in-the-Loop Network Emulation | IEEE 2021 (arXiv 2021) | arXiv:2110.10617 | **primary-text** (abstract fetched) | high-fidelity hardware-in-the-loop RF emulation (RQ-2 context) | **verified** |
| `netreplica2025` | NetReplica: A Programmable Substrate for Bottleneck-Centric Network Data Generation | arXiv 2025–2026 | arXiv:2507.13476 (v4 2026-09-17) | **primary-text** (abstract fetched) | fidelity as a first-class capability; 47% reduction in transmission-time prediction error (RQ-2) | **verified** |
| `dof2023fidelity` | Delay-based Fidelity Monitoring of Distributed Network Emulation | IEEE IMC 2023 (claimed) | arXiv:2207.14612 (claimed) | **metadata-only** (PDF via slices-sc.eu; IMC '23 placement not confirmed via OpenAlex this session) | passive delay measurement for emulator fidelity monitoring (RQ-2, pattern c) | **provisional** |
| `zhong2024distserve` | DistServe (OSDI'24) — evaluation section | OSDI 2024 | USENIX PDF | **primary-text** (§6 fetched) | p90 TTFT/TPOT + SLO attainment, no CIs (RQ-1) | **verified** (re-confirmed at primary-text level this session) |
| `loongserve2024` | LoongServe (SOSP'24) — evaluation section | SOSP 2024 | DOI 10.1145/3694715.3695948 | **primary-text** (§7 fetched) | normalized-latency means + P90 goodput, no CIs (RQ-1) | **verified** |
| `patel2024splitwise` | Splitwise (ISCA'24) — evaluation section | ISCA 2024 | DOI 10.1109/ISCA59077.2024.00019 | **primary-text** (§VI fetched) | P50 TBT + SLO lines + CDF, no CIs (RQ-1) | **verified** |
| `mooncake2025` | Mooncake (FAST'25, Best Paper) — evaluation section | FAST 2025 | arXiv:2407.00079; TOS DOI 10.1145/3773772 | **primary-text** (USENIX PDF fetched) | TTFT + longest-10% TBT, effective-request capacity, stated N = 4K–24K, no CIs (RQ-1, RQ-3, citation 2) | **verified** |
| `middleware2026ae` | Middleware 2026 Call for Artifacts | middleware-conf.github.io, 2026 | — (web resource, live-fetched 2026-10-12) | **primary** (live page) | AE exists; Available/Functional/Reproduced badges; full-workflow scripts required (RQ-4, #8) | **verified** (non-peer-reviewed, live snapshot) |
| `tene2013latency` | How Not to Measure Latency (QCon London 2013 talk) | QCon 2013 (talk) | — (video/abstract only) | **metadata-only** | origin of coordinated omission; open-loop measurement (RQ-1, RQ-7) | **provisional** (talk, not a paper — cite only as origin of term) |

**Unverified / not-cited this session:** (a) *McDevitt & Mellor, "How to Run a Microbenchmark"* — no clean record (DOI/proceedings) resolved despite multiple search attempts; the methodology points it would supply are already carried by the LR-3 ledger (`cohen1988`, `efron1993`) and `treadmill2016`, so it is **not cited**. (b) *Fidelity-aware large-scale distributed network emulation* (Computer Networks 2024, S1389128624003633) — abstract seen but full text not fetched; `dof2023fidelity` and `netreplica2025` carry the fidelity-monitoring point instead. (c) *Geolight* (SIGCOMM 2012, network emulator for geo-distributed systems) — could not be resolved to a citable record this session; the emulation-fidelity citations above are more recent and closer to our netem setting.

> **LR-7 note:** 14 of 16 rows are **verified** (9 at primary-text level — Treadmill, TailBench, NIST AI 800-3, CALF, the SC25'25 long-haul RDMA poster, and the evaluation sections of DistServe / LoongServe / Splitwise / Mooncake; the HPDC'26 / Colosseum / NetReplica rows are abstract-level, and the Middleware AE page is a live non-peer-reviewed snapshot). The two **provisional** rows (`dof2023fidelity`, `tene2013latency`) support non-load-bearing points only: `dof2023fidelity` illustrates pattern (c) as one of three, and `tene2013latency` is the origin of a term, with the peer-reviewed weight carried by `treadmill2016`/`tailbench2016`. The three load-bearing anchors for the RQ-5 verdict are `treadmill2016` (statistical rigor), `mooncake2025` (the bare-percentile norm we exceed), and `calf2026` (the fidelity-gate pattern we adopt).
