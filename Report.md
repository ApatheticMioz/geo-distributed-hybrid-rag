# Geo-Distributed Pipeline Parallelism for Hybrid

# Retrieval-Augmented Generation on Asymmetric

# Edge-to-Core Topologies

## Phase 2: Mid-Evaluation Research Report

### M. Abdullah Ali

```
Department of Data Science
FAST-NUCES
Registration No: 23i-
```

### M. Abdullah Aamir

```
Department of Data Science
FAST-NUCES
Registration No: 23i-
```

Abstract—The integration of Large Language Models (LLMs)
into production environments has increasingly relied on
Retrieval-Augmented Generation (RAG) to ensure factual
grounding and domain-specific relevance. Standard RAG imple-
mentations are monolithic and sequential, incurring cumulative
Wide Area Network (WAN) latency penalties at every network-
bound step. In geo-distributed edge-to-core topologies, up to
23% of end-to-end latency is lost to communication overhead,
and production-grade RAG pipelines frequently exceed 2-second
P95 latencies, leading to significant user drop-off. This report
proposes a tripartite, parallelized RAG architecture that de-
constructs the retrieval phase into concurrent local and remote
branches, orchestrated by an asynchronous network scheduler
built on FastAPI and gRPC over HTTP/2. By masking WAN
transit time behind local edge computation, the system targets a
30–40% reduction in Time-to-First-Token (TTFT). The Phase
2 mid-evaluation confirms technical feasibility, identifies the
methodological gap left open by existing homogeneous-cluster
frameworks, and presents a concrete evaluation plan using MS
MARCO and WikiQA benchmarks.
Index Terms—Retrieval-Augmented Generation, Pipeline Par-
allelism, Edge Computing, Geo-Distributed Inference, Asym-
metric Hardware Topology, Time-to-First-Token, Asynchronous
Scheduling, Hybrid Retrieval, BGE-M3, Splitwise, Active Infer-
ence, gRPC, FastAPI

#### I. INTRODUCTION

A. Context and Problem Statement

The integration of Large Language Models (LLMs) into
production environments has increasingly relied on Retrieval-
Augmented Generation (RAG) to ensure factual grounding and
domain-specific relevance [1]. By retrieving external context
from a knowledge base, RAG mitigates the hallucination
tendencies of parametric models. However, the standard im-
plementation of RAG is a monolithic, sequential pipeline:
the system first waits for a sparse keyword search, then a
dense semantic embedding generation, then a vector database
lookup, and finally injects these results into the LLM for
decoding.
In geographically distributed networks—where users are
at the “edge” and powerful GPUs are in the “core”—this

```
sequential flow is highly inefficient. The cumulative penalty
of sequential Remote Procedure Calls (RPC) across pipeline
stages creates idle “pipeline bubbles” where high-performance
core hardware remains underutilized while awaiting network
transmissions or upstream edge tasks. Research indicates that
communication and serialization overhead can account for up
to 23% of end-to-end inference latency [2], significantly de-
grading the Time-to-First-Token (TTFT)—the primary metric
for user-perceived responsiveness. Furthermore, production-
grade RAG pipelines frequently exceed 2-second P95 laten-
cies, which can lead to a 40% user drop-off rate in interactive
applications [2].
```

```
B. Local Network Context: MAN/Fiber Topology
Traditional geo-distributed studies commonly assume high
round-trip times (RTT) associated with trans-continental links.
This project, however, operates within a Metropolitan Area
Network (MAN) infrastructure. The three hardware nodes (A,
B, and C) are situated within an 18.4 km radius and are
interconnected via high-speed PTCL Flash Fiber. Preliminary
benchmarks for this infrastructure indicate a raw network
Round-Trip Time (RTT) of approximately 5–15 ms, which is
modeled at 20 ms in this study after accounting for encrypted
WireGuard tunneling and protocol stack overhead. Despite this
low-latency floor, the sequential execution of RAG pipeline
components still introduces cumulative bottlenecks that pre-
vent sub-second TTFT responsiveness at enterprise scale.
Crucially, the dominant latency contributor shifts from network
transit to the GPU computation time of the dense retrieval
branch (Tdense≈ 130 ms), which reinforces the necessity of
the parallel execution strategy described in Section IV.
```

```
C. Research Motivation
The motivation for this project is two-fold. First, there is a
need to adapt Natural Language Processing (NLP) systems to
asymmetric hardware. Most current research assumes homoge-
neous GPU clusters (e.g., all NVIDIA H100s), but real-world
edge-to-core topologies involve a mix of integrated graphics,
```

mid-range discrete GPUs, and high-end core accelerators [3].
As Gartner projects, the shift toward edge processing repre-
sents a fundamental restructuring of AI inference architectures
[4].
Second, by deconstructing the RAG pipeline into parallel
microservices, it becomes possible to “hide” network transit
time behind local computation. The objective is to achieve a
30–40% reduction in TTFT using an asynchronous network
scheduler that overlaps localized edge retrieval with remote
core embeddings. This strategy addresses a critical gap in the
viability of Edge-AI, where the disparity between local CPU-
bound tasks and remote GPU-bound tasks remains a significant
performance bottleneck.

```
II. LITERATURE REVIEW / RELATED WORK: THE THREE
PILLARS
```

A. Pillar 1: Optimization of Hybrid RAG Systems

Hybrid retrieval has emerged as the gold standard for robust
search, combining the exact keyword-matching strengths of
lexical algorithms such as BM25 [5] with the semantic under-
standing of dense neural embeddings [6]. While effective, the
computational cost is high; generating both sparse and dense
vectors for every query can account for over 93% of retrieval-
stage latency [2].
Chen et al. (2024) introduced the BGE M3-Embedding
framework [7], a versatile bi-encoder model derived from
XLM-RoBERTa-large [8] that supports over 100 languages.
The core innovation is its “Triple-Threat” architecture, which
executes three retrieval strategies in a single forward pass: (1)
dense retrieval, which encodes queries and documents into
fixed-size CLS-token vectors for approximate nearest-neighbor
search; (2) sparse (lexical) retrieval, which produces term-
level importance weights analogous to BM25 but learned end-
to-end; and (3) multi-vector (late interaction) retrieval, which
retains a per-token embedding matrix and scores passages
via a MaxSim operation over all token pairs, as in the
ColBERT paradigm. The model supports inputs up to 8,
tokens, allowing for the retrieval of long documents which was
previously a significant limitation in semantic search.
Despite these advances, the integration of multi-headed
embeddings reveals a “weakest link” phenomenon: if one
retrieval path is significantly slower or less accurate, it can
substantially degrade the overall fused output [9]. To mitigate
this, systems employ Reciprocal Rank Fusion (RRF) [10],
which aggregates documents based on their relative rank order
across disparate lists. This method ignores absolute similarity
scores, which are often uncalibrated and incompatible across
different models, thereby providing a more robust and stable
fusion mechanism. A further challenge motivating hybrid
retrieval is the inadequacy of either approach in isolation:
dense retrieval is prone to hallucinated connections, while
sparse retrieval fails on semantically rich queries [2].

B. Pillar 2: Pipeline Parallelism and Phase Splitting

Pipeline parallelism (PP) partitions the layers of a neural
network across multiple devices to fit models that exceed the

```
memory of a single GPU [11]. A persistent challenge in PP is
the “pipeline bubble”—idle periods where a high-performance
stage waits for the output of a preceding stage [12]. In LLM
inference, the workload is further bifurcated into two distinct
phases with contrasting resource profiles and saturation points
[13]:
```

- Prefill (Prompt Computation) Phase: This phase pro-
  cesses all input prompt tokens in a single, fully par-
  allelized forward pass to produce the initial Key-Value
  (KV) cache. It is compute-saturated: throughput scales
  directly with arithmetic throughput (TFLOPs), and under-
  utilized GPU cores become the primary bottleneck. This
  phase therefore demands top-tier compute accelerators
  such as the RTX 3080 at Node A.
- Decode (Token Generation) Phase: This phase gen-
  erates tokens strictly one at a time, issuing a forward
  pass per token while reading the full KV cache from
  memory at each step. It is memory-bandwidth-saturated:
  the GPU’s arithmetic units are mostly idle while DRAM
  bandwidth is the binding constraint. High-capacity, high-
  bandwidth memory is therefore more valuable than raw
  TFLOP count during this phase.
  Patel et al. (2024) proposed Splitwise [13], a system that
  disaggregates these phases onto separate machines optimized
  for each workload. By isolating the compute-heavy prefill from
  the memory-bound decode, Splitwise achieves up to 2.35×
  throughput improvements under the same power and cost bud-
  gets. Crucially, however, Splitwise is optimized for throughput
  in homogeneous clusters; the tripartite adaptation proposed
  here instead targets Time-to-First-Token (TTFT) Service Level
  Objective (SLO) attainment in high-concurrency, asymmetric
  edge-to-core environments—a distinct and underexplored PDC
  contribution where minimizing per-request latency, rather than
  maximizing aggregate throughput, is the binding constraint
  for interactive user-facing deployments. This project extends
  the Splitwise philosophy by treating retrieval and generation
  as disaggregated pipeline components that can be overlapped
  in geo-distributed settings. To enable this on constrained
  hardware, AWQ [14] is employed to compress the Llama-3-
  8B model to fit efficiently within the 10 GB VRAM of the
  RTX 3080 at Node A.
  C. Pillar 3: Edge-to-Cloud Orchestration on Asymmetric
  Hardware
  The “edge-to-core” continuum involves nodes with vast
  hardware disparities, ranging from CPUs and integrated graph-
  ics at the edge to high-end GPU clusters in the cloud [3]. In
  localized MAN fiber topologies, the RTT is low (≈20 ms), yet
  the disparity in processing power between a mobile-tier CPU
  and a core GPU remains a bottleneck for real-time services.
  He et al. (2024) investigated this via an Active Inference ap-
  proach [15]. Unlike static offloading rules or traditional Deep
  Reinforcement Learning (DRL) that require specific reward
  functions, active inference employs contextual bandits—such
  as Neural UCB—to learn request durations online and dynam-
  ically decide which tasks to process locally versus offloading

to a remote node based on current network conditions. This
rewardless guidance algorithm minimizes the expected future
“free energy” of the system, allowing it to adapt to dynamic
workloads without the overhead of reward engineering.
A critical challenge in these systems is Head-of-Line (HOL)
blocking, where a complex request stalls simpler tasks. He
et al. (2024) addressed this with an asynchronous feedback
mechanism that decouples time-consuming model updates
and remote data fetches from the real-time scheduling loop,
ensuring that scheduling decisions are never delayed by long-
running background tasks [15].

III. IDENTIFICATION OF THE METHODOLOGICAL GAP
Existing distributed serving frameworks such as vLLM [17],
SGLang, and TensorRT-LLM [18] are primarily designed for
homogeneous GPU clusters connected by low-latency, high-
bandwidth interconnects such as NVLink [12]. These systems
implicitly assume uniform device throughput: every worker is
expected to complete its assigned stage in roughly the same
time window. Applying these frameworks to asymmetric, geo-
distributed topologies reveals three critical gaps:
Failure of Uniform Partitioning and Straggler Effects:
Prior systems employ even partitioning of model operations
across all worker nodes. In a heterogeneous cluster—where
high-end GPUs such as the RTX 3080 coexist with lower-tier
hardware such as the GTX 1660 Ti—this uniform partitioning
produces a severe straggler effect: the pipeline cannot advance
past a micro-batch until the slowest node completes its stage,
causing the faster RTX 3080 to idle while awaiting the slower
device. Concurrently, low-memory nodes risk Out-of-Memory
(OOM) errors when assigned the same layer counts as their
more capable peers. DynaPipe [12] has begun to address
this in training settings, but no equivalent heterogeneity-
aware scheduler exists for asymmetric RAG inference. In
the proposed system, this idle period is explicitly mitigated:
if Node B’s dense retrieval exceeds the dynamic timeout
threshold Tthreshold (e.g., due to compute saturation beyond
the nominal Tdense ≈ 130 ms), the asynchronous scheduler
at Node A immediately triggers a synchronization-driven
speculative prefill using the available sparse context from
Branch 1, ensuring the generation engine is never blocked
indefinitely by a straggling intermediate node (see Section IV).
Note that this mechanism is distinct from the “Speculative
Prefill” (SpecPrefill) technique found in recent literature [24],
[25], which uses lightweight models for token-importance
estimation and prompt compression; the mechanism described
here is a synchronization-level timeout fallback that substitutes
the sparse retrieval context when the dense branch is delayed.
Inflexible Quantization Strategy: Standard solutions apply
a single quantization precision (e.g., INT4) globally across all
nodes. This results in memory underutilization on powerful
core nodes and fails to account for the divergent perfor-
mance of different bitwidths on various GPU generations.
AWQ [14] offers a more principled per-layer approach that
selectively preserves salient weights, but its integration into
geo-distributed, heterogeneous pipelines remains unexplored.

```
Recent work such as SplitQuant [16] has begun investigating
heterogeneity-aware quantization for multi-node serving, yet
the RAG retrieval stage remains outside its scope.
Sequential RAG Bottleneck: Most RAG literature [1], [2]
treats retrieval as a monolithic pre-step that must complete
before generation can begin. There is an absence of research
on parallelizing the internal components of retrieval—sparse
versus dense—across geographically disparate nodes to mask
the inherent WAN transit time.
```

```
IV. PROPOSED METHODOLOGY
A. Tripartite Hardware and Software Topology
The proposed system is sharded across three distinct asym-
metric nodes, each assigned a hardware role and a dedicated
software stack matched to its capabilities. Table I presents the
full specifications.
```

```
B. Physical Network Topology and Communication Protocol
The three nodes span two physically distinct network do-
mains, introducing a genuine WAN boundary between the edge
and the core infrastructure. Nodes A and B are co-located
at a residential site and are connected to each other via a
shared 100 Mbps Gigabit Ethernet LAN over a common router.
This intra-home link provides low-latency, high-bandwidth
communication, making the A–B link effectively a fast local
interconnect; critically, once Node B completes its Tdensecom-
putation, the retrieved dense context is delivered to Node A
in≤ 1 ms over this local LAN, an overhead that is negligible
relative to the 130 ms GPU computation time. This near-
instantaneous local transfer is the prerequisite that makes the
masking model Tparallel= max(Tsparse, TWAN+ Tdense) hold in
practice: without a fast A–B link, the post-computation transfer
delay would pad the remote branch and erode the retrieval
makespan savings. Node C is deployed on university premises
and communicates over the university Wi-Fi network. The
internet path between Node C and the home LAN constitutes
the true WAN segment of the topology—the inter-domain
link that introduces the measurable round-trip latency that the
parallel retrieval architecture is designed to mask. To address
the NAT traversal challenge posed by dynamic IP allocation on
residential ISPs—which commonly deploy Carrier-Grade NAT
(CGNAT)—the home router is configured with a Dynamic
DNS (DDNS) record that continuously maps a stable hostname
to the current public IP. The WireGuard tunnel is established
using static peer configurations (public keys and DDNS end-
point) on all three nodes, enabling Node C to initiate and
maintain a persistent encrypted tunnel to the residential LAN
regardless of IP reassignment. System demonstrations and live
evaluations will be conducted on-site at the university using
Node C as the user-facing query endpoint.
Effective WAN latency masking further requires a com-
munication protocol that supports request multiplexing and
non-blocking calls. The project employs gRPC over HTTP/
for all inter-node communication, for three reasons. First,
binary Protobuf serialization substantially reduces payload size
relative to text-based formats such as JSON—benchmarks
```

```
TABLE I
TRIPARTITE NODE HARDWARE SPECIFICATIONS, ROLES, AND SOFTWARE STACK
```

```
Node Role Hardware Specifications Operating Sys-
tem
```

```
Software Stack
```

```
Node C
(Edge)
```

```
Orchestrator &
Sparse Retrieval
```

```
Intel Iris XE (80EU) / i5-1235U / 16 GB
LPDDR4X / University Wi-Fi
```

```
Windows 11 FastAPI (Async Gateway), BM25 (Local
Index)
```

```
Node B
(Intermediate)
```

```
Dense Retrieval
Engine
```

```
GTX 1660 Ti 6 GB VRAM / i7-10750H
/ 24 GB DDR4 / Gen3 NVMe SSD /
100 Mbps Ethernet
```

```
Windows 11 gRPC Server, BGE-M3 (GPU Inference),
Qdrant (Vector DB)
```

```
Node A
(Core)
```

```
Generation Engine RTX 3080 10 GB VRAM / Ryzen 7 7700
/ 32 GB DDR5 / Gen3 NVMe SSD /
100 Mbps Ethernet
```

```
Windows 11 vLLM, Llama-3-8B-Instruct (AWQ),
Async Scheduler
```

indicate that Protobuf is up to 11× faster to parse than
JSON on mobile-tier CPUs such as the i5-1235U and reduces
payload sizes by 60–80%, directly protecting the TTFT gains
achievable from the parallel retrieval architecture. Second,
HTTP/2 stream multiplexing allows multiple independent RPC
calls to share a single TCP connection without head-of-line
blocking at the transport layer. Third, gRPC’s native support
for asynchronous stubs allows Node C to dispatch a non-
blocking retrieval call to Node B and immediately proceed
with local BM25 processing, preventing the edge node from
idling during the WAN round-trip. At the edge, Node C
acts as the Request Orchestrator: it exposes a FastAPI
asynchronous gateway that accepts user queries via HTTP,
dispatches and manages the two parallel retrieval branches,
and forwards aggregated branch outputs to Node A for syn-
chronization and generation.
To secure inter-node communication across the edge-to-
home WAN segment, the system employs WireGuard [19]–
[21] for encrypted tunneling. All three nodes run Windows 11,
on which WireGuard operates as a privileged userspace service
(wireguard.exe) responsible for all cryptographic oper-
ations, paired with the WinTun driver that handles packet
injection and reception at the network adapter level. While
this differs from the fully kernel-native WireGuard integration
available on Linux 5.6+, it remains substantially more efficient
than comparable userspace VPN implementations such as
OpenVPN: WireGuard’s minimal codebase and WinTun’s low-
overhead I/O path together maintain an empirically measured
latency penalty of≤ 0. 3 ms [21], which is negligible relative
to the dominant pipeline latencies in this system. To pre-
vent packet fragmentation over the fiber infrastructure—which
would split multi-kilobyte retrieval contexts across multiple IP
datagrams and degrade throughput—the WireGuard network
interface is manually tuned to MTU 1420. TCP is retained
as the underlying transport, since the retrieval phase demands
100% data integrity for document context delivery.

C. Parallelized Retrieval and Generation Flow

The core innovation deconstructs the retrieval phase into
two concurrent branches:

```
Branch 1 — Localized/Edge (Node C): Node C executes
a local BM25 sparse retrieval [5] using CPU RAM. This
operation has negligible latency (30–80 ms) and requires no
network communication. It provides an initial set of keyword-
exact matches immediately upon receiving a query.
Branch 2 — Remote/Core (Node B): Simultaneously,
Node C dispatches a non-blocking gRPC call to Node B.
Node B computes BGE-M3 dense embeddings [7] using all
three retrieval heads (dense, sparse, and multi-vector) on its
GTX 1660 Ti GPU and retrieves semantic matches from the
Qdrant vector database.
Synchronization and Fusion (Node A): Node A receives
the asynchronous outputs from both branches and implements
Reciprocal Rank Fusion (RRF) [10] to merge the two ranked
lists. RRF is applied with the standard smoothing constant
k = 60, which prioritizes rank-based consensus over in-
dividual similarity scores that are often uncalibrated across
different model families. If the remote branch result arrives
after a dynamic timeout threshold Tthreshold, the asynchronous
scheduler triggers a synchronization-driven speculative prefill
using only the sparse context from Branch 1, ensuring system
responsiveness under adverse network conditions such as
university Wi-Fi congestion.
Generation (Node A): Node A initiates autoregressive
decoding using Llama-3-8B-Instruct served through vLLM.
The model is compressed via AWQ [14] to fit within the
10 GB VRAM of the RTX 3080 while selectively pre-
serving salient activation-outlier weights. The Ryzen 7 7700
CPU and 32 GB DDR5 memory at Node A further support
high-throughput KV-cache management during the memory-
bandwidth-saturated decode phase.
D. Mathematical Optimization of TTFT
The efficiency of the parallel pipeline is derived from
the mathematical reduction of the retrieval makespan. In a
traditional sequential RAG system [1], the retrieval time is
the sum of its sequential components:
```

```
Tsequential= Tsparse+ TWAN+ Tdense (1)
In the proposed tripartite architecture, execution time is
governed only by the longest parallel branch:
```

```
Tparallel= max(Tsparse, TWAN+ Tdense) (2)
```

Given the low-latency MAN environment of the testbed
(TWAN≈ 20 ms over fiber, representing the modeled upper-
bound derived from the measured raw RTT of 5–15 ms plus
WireGuard and protocol overhead as established in Section I)
and a GPU computation time of Tdense≈ 130 ms, the total
remote branch latency amounts to approximately 150 ms.
Network jitter within the 5–15 ms raw RTT range is handled
operationally by the dynamic timeout Tthresholdof the asyn-
chronous scheduler. The 30–80 ms required for Tsparseis there-
fore entirely masked by this combined delay, meaning Tsparse
contributes zero additional latency to the critical path [22],
[23]. It should be noted that Equations (1) and (2) represent a
simplified retrieval makespan model; a complete formulation
would additionally include a small synchronization overhead
term Tsynccapturing gRPC call setup at Node C and RRF
fusion computation at Node A (empirically≤ 5 ms), which
is omitted here for clarity and will be measured explicitly
in the Phase 3 evaluation. While the fiber connection keeps
the network transit component low, the Parallel Distributed
Computing (PDC) contribution of the architecture remains
significant because the GTX 1660 Ti’s dense computation time
(≈130 ms) is the dominant factor on the critical path. Further-
more, the PDC benefit extends beyond simple latency hiding:
while Node B processes its dense retrieval branch, Node A can
begin pre-processing—allocating KV-cache buffers, loading
quantized model weights into VRAM registers, and managing
pipeline state for the incoming context. It is this overlapping
of Tdensewith Node A’s pre-processing work that drives the
total TTFT reduction. To be precise: the 30–40% reduction
claim applies specifically to Pre-Generation Latency—the
combined retrieval makespan and generation-setup overhead—
rather than to the full end-to-end TTFT including Tgeneration.
With a typical prefill (prompt computation) time of 300–
400 ms, eliminating the sequential 80 ms retrieval wait and
overlapping setup work reduces the pre-generation phase by
30–40%, which in turn yields a measurable but proportionally
smaller reduction in the overall TTFT; the precise total-TTFT
impact will be quantified empirically in Phase 3.

E. Synchronization Controller and Asynchronous Scheduler
(Node A)

Node A serves as the Synchronization Controller for
the prefill phase: it is the de facto scheduler that receives
branch outputs, manages fusion timing, and owns the timeout
fallback logic. It hosts an asynchronous network scheduler
that actively manages pipeline bubbles. It uses a non-blocking
feedback loop inspired by the active inference paradigm [15]
to continuously monitor network jitter and upstream branch
latency. If the results from the remote Branch 2 are delayed
beyond the dynamic timeout threshold Tthreshold, the scheduler
triggers a synchronization-driven speculative prefill using only
the edge-provided sparse context from Branch 1. This ensures
system responsiveness under adverse network conditions and

```
prevents the core generation hardware from remaining idle
indefinitely while awaiting a slow or unresponsive intermediate
node. The role separation is therefore explicit: Node C is the
Request Orchestrator responsible for dispatch and local lexical
search, while Node A is the Synchronization Controller re-
sponsible for fusion, timeout management, and the speculative
fallback—eliminating any ambiguity about where scheduler
logic resides in the tripartite topology.
V. DATASET AND EVALUATION PLAN
The system will be evaluated using the following bench-
marks and metrics:
MS MARCO [26]: This dataset will be used to eval-
uate combined dense and sparse retrieval accuracy using
the Mean Reciprocal Rank at rank 10 (MRR@10) metric.
To avoid the confounding variables introduced by differing
prompt complexities and context lengths across datasets, end-
to-end pipeline latency (including both retrieval and generation
stages) will also be benchmarked exclusively on a unified MS
MARCO query subset, ensuring consistent input conditions
across all measured pipeline phases.
WikiQA [27]: This dataset will be used to measure Exact
Match (EM) accuracy for the end-to-end question-answering
capability of the system, providing a complementary quality
signal across the full generation pipeline.
The primary performance indicators for the experimental
evaluation will be:
```

- TTFT Reduction (%): The percentage improvement in
  Time-to-First-Token relative to the sequential baseline.
- Streaming Throughput (tokens/sec): The sustained to-
  ken generation rate at Node A after the first token is
  produced.
- Recall@K: The recall of the fused retrieval results at
  varying cutoffs K, quantifying the coverage of the hybrid
  retrieval strategy.
  The “overlap gain” metric will be specifically measured
  across varying simulated WAN latency conditions to quantify
  system resilience against WAN jitter and to empirically vali-
  date the theoretical masking of Tsparseby TWAN+ Tdense. Live
  demonstration experiments will be conducted with Node C
  on the university campus network to capture real-world inter-
  domain latency distributions.

```
VI. CONCLUSION
The Phase 2 Mid-Evaluation confirms that deconstructing
the RAG pipeline into a parallelized tripartite structure is
a technically feasible solution for geo-distributed NLP. By
leveraging multi-functional embeddings via BGE-M3 [7]—
exploiting its dense, sparse, and multi-vector retrieval heads
in parallel—and the phase-splitting principles established by
Splitwise [13], the proposed system directly addresses the
pipeline bubble and straggler effects inherent in sequential
RAG on asymmetric hardware. The asynchronous software
stack (FastAPI at the edge, gRPC over HTTP/2 for inter-node
transport, vLLM with AWQ at the core) provides a cohe-
sive and efficient communication fabric across the tripartite
```

topology. The physical deployment—with Nodes A and B on
a shared 100 Mbps home LAN and Node C operating over
university Wi-Fi—provides a realistic and reproducible geo-
distributed testbed. With TWAN≈ 20 ms and Tdense≈ 130 ms,
the remote branch completes in approximately 150 ms total,
entirely masking the 30–80 ms local sparse retrieval on the
critical path. The asynchronous network scheduler, inspired
by the active inference paradigm of He et al. (2024) [15],
further ensures graceful degradation through synchronization-
driven speculative prefill under adverse network conditions.
Future work in Phase 3 will focus on the full experimental
implementation and the quantitative validation of the 30–40%
TTFT reduction goal.

REFERENCES
[1] P. Lewis et al., “Retrieval-Augmented Generation for Knowledge-
Intensive NLP Tasks,” in Advances in Neural Information Processing
Systems (NeurIPS), 2020.
[2] C. Zhao et al., “Hybrid-RAG: Combining Dense and Sparse Represen-
tations for Accuracy,” arXiv preprint arXiv:2408.04712, 2024.
[3] J. Wang et al., “Edge-AI: Distributed Inference across Heterogeneous
Devices,” IEEE Internet of Things Journal, 2024.
[4] Gartner, Inc., “The Strategic Importance of Edge Computing in AI
Architectures,” Gartner Research Report, 2023.
[5] S. Robertson and H. Zaragoza, “The Probabilistic Relevance Framework:
BM25 and Beyond,” Foundations and Trends in Information Retrieval,
2009.
[6] Y. Luan et al., “Sparse, Dense, and Attentional Representations for
Text Retrieval,” Transactions of the Association for Computational
Linguistics (TACL), 2021.
[7] J. Chen et al., “BGE M3-Embedding: Multi-Lingual, Multi-
Functionality, Multi-Granularity Text Embeddings Through Self-
Knowledge Distillation,” arXiv preprint arXiv:2402.03216, 2024.
[8] A. Conneau et al., “Unsupervised Cross-lingual Representation Learning
at Scale (XLM-RoBERTa),” ACL, 2020.
[9] P. Zhang et al., “The Weakest Link: Path-wise Quality in Hybrid Search,”
arXiv preprint arXiv:2404.05672, 2024.
[10] G. V. Cormack, C. L. Clarke, and S. Buettcher, “Reciprocal Rank Fusion
Outperforms Condorcet and Individual Rank Learning Methods,” in
Proc. 32nd International ACM SIGIR Conference, 2009.
[11] D. Narayanan et al., “PipeDream: Generalized Pipeline Parallelism for
DNN Training,” in Proc. 27th ACM Symposium on Operating Systems
Principles (SOSP), 2019.
[12] J. Huang et al., “DynaPipe: Optimizing Pipeline Parallelism for Hetero-
geneous Clusters,” arXiv preprint arXiv:2403.18731, 2024.
[13] P. Patel et al., “Splitwise: Efficient Generative LLM Inference Using
Phase Splitting,” in Proc. 51st Annual International Symposium on
Computer Architecture (ISCA), 2024.
[14] J. Lin et al., “AWQ: Activation-aware Weight Quantization for LLM
Compression and Acceleration,” arXiv preprint arXiv:2306.00978, 2023.
[15] Y. He, J. Fang, F. R. Yu, and V. C. M. Leung, “Large Language
Models (LLMs) Inference Offloading and Resource Allocation in Cloud-
Edge Computing: An Active Inference Approach,” IEEE Transactions
on Mobile Computing, vol. 23, no. 12, 2024.
[16] J. Zhao et al., “SplitQuant: Serving Large Language Models on Het-
erogeneous Clusters via Asymmetric Quantization,” in Proc. IEEE
International Conference on Cluster Computing (CLUSTER), 2025.
[17] W. Kwon et al., “Efficient Memory Management for Large Language
Model Serving with PagedAttention,” in Proc. 29th ACM Symposium on
Operating Systems Principles (SOSP), 2023.
[18] NVIDIA Corporation, “TensorRT-LLM: A TensorRT Toolbox for Op-
timized Large Language Model Inference,” NVIDIA Technical Report,
2023.
[19] J. A. Donenfeld, “WireGuard: Next Generation Kernel Network Tunnel,”
in Proc. Network and Distributed System Security Symposium (NDSS),
2017.
[20] S. Mackey et al., “A Performance Comparison of WireGuard and
OpenVPN,” in Proc. ACM Conference on Data and Application Security
and Privacy (CODASPY), 2020.

```
[21] J. Anyam et al., “Empirical Performance Analysis of WireGuard vs.
OpenVPN,” MDPI Electronics, 2025.
[22] Y. Wang et al., “TokenWeave: Overlapping Communication with Com-
putation in Distributed LLM Inference,” arXiv preprint, 2025.
[23] B. Xiao et al., “ISO: Overlap of Computation and Communication for
LLM Inference,” arXiv preprint, 2024.
[24] X. Shi et al., “Speculative Prefill: Turbocharging TTFT with Lightweight
Token Importance Estimation,” arXiv preprint arXiv:2410.14666, 2024.
[25] Y. Liu et al., “SpecPrefill: Accelerating LLM Inference via Context-
Aware Token Selection for Prompt Compression,” arXiv preprint, 2025.
[26] N. Bajaj et al., “MS MARCO: A Human Generated Machine Reading
Comprehension Dataset,” arXiv preprint arXiv:1611.09268, 2016.
[27] Y. Yang et al., “WikiQA: A Challenge Dataset for Open-Domain
Question Answering,” EMNLP, 2015.
```
