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

