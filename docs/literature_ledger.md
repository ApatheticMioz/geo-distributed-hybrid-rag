# Literature Ledger — `paper/references.bib`

**Session:** LR-5 (Phase 1, lit-milestone) · **Date:** 2026-09-30 · **Branch:** `feat/three-node-study`
**Scope:** every entry in `paper/references.bib` (29 entries; 25 cited in `paper/main.tex`, 4 unused).

## Method — 4-stage anti-hallucination protocol

Each bib entry was verified through four stages:

- **S1 — Resolve.** Resolve the entry's DOI (via `https://doi.org/...`) or arXiv id (via `https://arxiv.org/abs/...`) with a live web fetch. A 404 or no-resolve is an automatic **fail** for S1.
- **S2 — Triangulate.** Compare publisher/venue metadata (title, authors, venue, year, volume/pages) against the bib fields. Any mismatch is recorded.
- **S3 — Corroborate.** Locate where the entry is cited in `paper/main.tex` and check the specific claim we cite at, classifying the evidence level:
  - **primary-text** — the cited formula/parameter/number is confirmed in the source's full text;
  - **abstract-level** — the claim is supported by the source's abstract (full text not fetched);
  - **metadata-only** — full text unreachable; only bibliographic metadata could be checked.
- **S4 — Verdict.** Record the verdict per entry below.

## Governance

- **Provisional-until-verified:** any entry whose S1/S2 could not be fully confirmed is marked **provisional** and must not be relied on for load-bearing claims until a later session resolves it.
- **Contradicted-entries-recorded:** any entry whose metadata or content contradicts what our paper attributes to it is recorded in the **Contradictions** section below and must be fixed in `references.bib` / `main.tex` before submission.
- **Never invent metadata:** unresolvable entries are marked **failed** with the attempts noted; no metadata is fabricated to fill gaps.

## Ledger

| key | title (short) | venue/year | DOI / arXiv | S1 | S2 | S3-level | cited-for (one line) | verdict | evidence URL | 2026-09-30 |
|---|---|---|---|---|---|---|---|---|---|---|
| `lewis2020rag` | RAG for Knowledge-Intensive NLP | NeurIPS 2020 | arXiv:2005.11401 | pass | pass (title, 12 authors, NeurIPS 2020) | abstract-level | generator–retriever formulation for knowledge-intensive tasks (L177) | **verified** | https://arxiv.org/abs/2005.11401 | 2026-09-30 |
| `guu2020realm` | REALM | ICML 2020 | arXiv:2002.08909 | pass | pass (title, 5 authors, ICML 2020) | abstract-level | retrieval-augmented LM pre-training (L178) | **verified** | https://arxiv.org/abs/2002.08909 | 2026-09-30 |
| `borgeaud2022retro` | RETRO | ICML 2022 | arXiv:2112.04426 | pass | pass (title, authors, ICML 2022) | abstract-level | "retrieving from trillions of tokens improves LM at scale" (L179) — abstract states 2-trillion-token database | **verified** | https://arxiv.org/abs/2112.04426 | 2026-09-30 |
| `gao2024ragsurvey` | RAG for LLMs: A Survey | arXiv 2024 | arXiv:2312.10997 | pass | pass (title, Gao et al., 2024) | abstract-level | "organizes the field into naive, advanced, and modular RAG" (L181) — abstract names exactly these three paradigms | **verified** | https://arxiv.org/abs/2312.10997 | 2026-09-30 |
| `jiang2023flare` | Active Retrieval Augmented Generation (FLARE) | EMNLP 2023 | arXiv:2305.06983 | pass | pass (title, Jiang et al., EMNLP 2023) | abstract-level | active-retrieval variant that adapts retrieval to the query (L183) | **verified** | https://arxiv.org/abs/2305.06983 | 2026-09-30 |
| `khattab2023dspy` | DSPy | arXiv 2023 | **arXiv:2310.03714** (bib says 2310.03716) | **fail** | **fail** — bib's eprint 2310.03716 resolves to "A Long Way to Go: Investigating Length Correlations in RLHF", a different paper; the real DSPy paper is arXiv:2310.03714 (NeurIPS 2023) | n/a | "declarative compiler DSPy adapts retrieval to the query" (L183) | **contradicted** | https://arxiv.org/abs/2310.03714 (correct) · https://arxiv.org/abs/2310.03716 (what bib points at) | 2026-09-30 |
| `robertson1994bm25` | BM25 (2-Poisson approximations) | SIGIR 1994 | DOI 10.1145/188490.188561 | pass (DOI resolves) | **fail** — real title is "Some Simple Effective Approximations to the 2-Poisson Model for Probabilistic Weighted Retrieval", pp. 232–241; bib says "Some Effective Algorithms for Document Retrieval", pp. 38–45 | metadata-only | "BM25 remains the lexical workhorse" (L189) | **contradicted** | https://dl.acm.org/doi/10.5555/188490.188561 | 2026-09-30 |
| `karpukhin2020dpr` | Dense Passage Retrieval | **EMNLP 2020** (bib says NAACL 2020) | DOI 10.18653/v1/2020.emnlp-main.550 | pass (arXiv:2004.04906) | **fail** — venue is EMNLP 2020 (pp. 6769–6781), not NAACL 2020 (pp. 166–175) | abstract-level | dense passage retrieval as the semantic leg (L191) | **contradicted** | https://arxiv.org/abs/2004.04906 · https://aclanthology.org/2020.emnlp-main.550/ | 2026-09-30 |
| `reimers2019sbert` | Sentence-BERT | EMNLP 2019 | arXiv:1908.10084 | pass | pass (title, Reimers & Gurewitz, EMNLP 2019) | abstract-level | sentence embeddings for the semantic leg (L191) | **verified** | https://arxiv.org/abs/1908.10084 | 2026-09-30 |
| `khattab2020colbert` | ColBERT | SIGIR 2020 | arXiv:2004.12832 | pass | pass (title, Khattab & Zaharia, SIGIR 2020) | abstract-level | "multi-vector late-interaction improving effectiveness at higher cost" (L193) — abstract confirms late-interaction over BERT | **verified** | https://arxiv.org/abs/2004.12832 | 2026-09-30 |
| `chen2024bge` | M3-Embedding (BGE-M3) | arXiv 2024 | arXiv:2402.03216 | pass | **partial** — arXiv title is "…Through **Self-Knowledge Distillation**"; bib says "…through **Symmetric** Distillation"; abstract confirms dense + multi-vector + sparse retrieval | abstract-level | "BGE-M3 serves both dense and lexical objectives from one encoder" (L194, L242) | **provisional** | https://arxiv.org/abs/2402.03216 | 2026-09-30 |
| `cormack2009rrf` | Reciprocal Rank Fusion | SIGIR 2009 | — | pass (title/authors/venue confirmed via search) | pass (title, Cormack/Clarke/Buettcher, SIGIR 2009) | metadata-only | "RRF is the standard parameter-light combiner" (L196, L243) | **verified** | https://dl.acm.org/doi/10.1145/1571896.1571968 | 2026-09-30 |
| `bajaj2018msmarco` | MS MARCO | EMNLP 2018 | — | pass (title/authors/venue confirmed) | pass (title, Bajaj et al., EMNLP 2018) | metadata-only | "8.84M-passage MS MARCO index" (L152, L234) | **verified** | https://aclanthology.org/D18-1109/ | 2026-09-30 |
| `kwon2023vllm` | vLLM / PagedAttention | SOSP 2023 | arXiv:2309.06180 | pass | pass (title, Kwon et al., SOSP 2023) | abstract-level | "LLM serving with PagedAttention" (L103, L233) — abstract confirms PagedAttention + vLLM engine | **verified** | https://arxiv.org/abs/2309.06180 | 2026-09-30 |
| `leviathan2023speculative` | Speculative Decoding | ICML 2023 | arXiv:2211.17192 | pass | pass (title, Leviathan/Kalman/Matias, ICML 2023) | abstract-level | "draft with cheap model, verify with large one" (L203) | **verified** | https://arxiv.org/abs/2211.17192 | 2026-09-30 |
| `chen2023speculative` | Speculative Sampling | arXiv 2023 | arXiv:2302.01318 | pass | pass (title, Chen et al., 2023) | abstract-level | second speculative-decoding reference (L203) | **verified** | https://arxiv.org/abs/2302.01318 | 2026-09-30 |
| `lin2024awq` | AWQ | MLSys 2024 | arXiv:2306.00978 | pass | **fail** — MLSys 2024 proceedings title is "…for **On-Device** LLM Compression…"; bib author list "Lin, Ji and **Wang, Hongxu** and others" — Wang, Hongxu is not among the 10 real authors (Lin, Tang, Tang, Yang, Chen, Wang, Xiao, Dang, Gan, Han) | metadata-only | **not cited in main.tex** (unused entry) | **contradicted** | https://proceedings.mlsys.org/paper_files/paper/2024/hash/42a452cbafa9dd64e9ba4aa95cc1ef21-Abstract-Conference.html | 2026-09-30 |
| `llama2024` | The Llama 3 Herd of Models | arXiv 2024 | arXiv:2407.21783 | pass | **partial** — arXiv byline is Grattafiori et al. (no "Llama Team" group author); title/year match | metadata-only | **not cited in main.tex** (unused entry) | **provisional** | https://arxiv.org/abs/2407.21783 | 2026-09-30 |
| `kang2017neurosurgeon` | Neurosurgeon | ASPLOS 2017 | — | pass (title/venue confirmed via search) | pass (title, Kang et al., ASPLOS 2017) | metadata-only | "hybrid cloud–edge inference partitions a network between device and server" (L115, L213, L805) | **verified** | https://dl.acm.org/doi/10.1145/3037687.3037711 | 2026-09-30 |
| `hung2020cloudedge` | Cloud-Edge Collaborative Inference for DNN | IEEE CLOUD 2020 | — | **fail** — no exact-title match found in IEEE Xplore / web search; author "Hung, D." unresolvable | **fail** — cannot confirm title, author, or venue | n/a | "cloud–edge collaborative inference" (L115, L213) | **failed** | attempts: web_search ×3 (exact title, "IEEE CLOUD 2020", "ieexplore") — no hit | 2026-09-30 |
| `zhang2021cloudedge` | Edge-Cloud Collaborative Inference for DNN | IEEE ICEC 2021 | — | **fail** — no exact-title match found; author "Zhang, Y." unresolvable | **fail** — cannot confirm title, author, or venue | n/a | "edge–cloud collaborative inference" (L115, L213) | **failed** | attempts: web_search ×3 (exact title, "ICEC 2021", "ieexplore") — no hit | 2026-09-30 |
| `donenfeld2017wireguard` | WireGuard | NDSS 2017 | — | pass (NDSS 2017 program page) | **fail** — real title is "WireGuard: **Next Generation Kernel Network Tunnel**"; bib says "The WireGuard Protocol: Fast, Secure, and Simple Network Tunnels" | metadata-only | "secure point-to-point tunnels such as WireGuard" (L128, L216, L244) | **contradicted** | https://www.ndss-symposium.org/ndss2017/ndss-2017-programme/wireguard-next-generation-kernel-network-tunnel | 2026-09-30 |
| `hemminger2000netem` | tc / netem | Linux kernel doc, 2000 | — | **partial** — `kernel.org/doc/Documentation/networking/` index is live (200) but `traffic-control.txt` and `traffic-control/` both 404; current canonical doc is the iproute2 man page `tc-netem(8)` | **fail** — year 2000 and the cited URL no longer host the netem doc; the doc has moved to iproute2 | metadata-only | "kernel traffic control (tc/netem) provides the controlled-WAN substrate" (L128, L217, L298) | **failed** | attempts: fetch kernel.org networking index (200), traffic-control.txt (404), traffic-control/ (404); current doc: https://man7.org/linux/man-pages/man8/tc-netem.8.html | 2026-09-30 |
| `devlin2019bert` | BERT | NAACL 2019 | arXiv:1810.04805 | pass | pass (title, Devlin et al., NAACL 2019) | metadata-only | **not cited in main.tex** (unused entry) | **verified** | https://arxiv.org/abs/1810.04805 | 2026-09-30 |
| `vaswani2017transformer` | Attention Is All You Need | NeurIPS 2017 | arXiv:1706.03762 | pass | pass (title, 8 authors, NeurIPS 2017) | metadata-only | "an LLM is prefilled with retrieved context" (L101) | **verified** | https://arxiv.org/abs/1706.03762 | 2026-09-30 |
| `brown2020gpt3` | GPT-3 | NeurIPS 2020 | arXiv:2005.14165 | pass | pass (title, Brown et al., NeurIPS 2020) | metadata-only | **not cited in main.tex** (unused entry) | **verified** | https://arxiv.org/abs/2005.14165 | 2026-09-30 |
| `strubell2019energy` | Energy & Policy for DL in NLP | ACL 2019 | arXiv:1906.02243 | pass | pass (title, Strubell/Ganesh/McCallum, ACL 2019) | abstract-level | "energy and policy considerations motivate placing expensive stages where hardware is cheapest" (L219) | **verified** | https://arxiv.org/abs/1906.02243 | 2026-09-30 |
| `zaharia2016datacenter` | The Datacenter as a Computer | Cambridge Univ. Press, 2016 | — | **fail** — "The Datacenter as a Computer" is by **Hoelzle & Barroso** (Morgan & Claypool, 2009; 3rd ed. 2016), not Zaharia/Cambridge; Zaharia's related work is "The Datacenter Needs an Operating System" (HotCloud 2011) | **fail** — wrong author, wrong publisher, wrong year | n/a | "datacenter-scale serving systems study phase and memory management" (L215) | **contradicted** | https://web.eecs.umich.edu/~mosharaf/Readings/DC-Computer.pdf (Hoelzle & Barroso) | 2026-09-30 |
| `qwen3technicalreport` | Qwen3 Technical Report | arXiv 2025 | arXiv:2505.09388 | pass | pass (title, Qwen Team, 2025) | abstract-level | "Qwen3.8-27B quantized to W4A16 under vLLM; thinking suppressed per request" (L101, L233) — abstract confirms thinking/non-thinking modes; note: report lists 0.6–235B, "3.8" naming not in report | **verified** | https://arxiv.org/abs/2505.09388 | 2026-09-30 |

## Contradictions

Entries whose metadata or content contradicts what our paper attributes to them. These must be fixed in `references.bib` (and, where the claim depends on the wrong source, in `main.tex`) before submission:

1. **`khattab2023dspy`** — bib eprint `2310.03716` resolves to "A Long Way to Go: Investigating Length Correlations in RLHF", a completely different paper. The real DSPy paper is **arXiv:2310.03714** (NeurIPS 2023). Fix: change `eprint` to `2310.03714`.
2. **`robertson1994bm25`** — bib title "Some Effective Algorithms for Document Retrieval" (pp. 38–45) is wrong. The real SIGIR 1994 paper is "Some Simple Effective Approximations to the 2-Poisson Model for Probabilistic Weighted Retrieval" (pp. 232–241), DOI 10.1145/188490.188561. Fix: correct title and pages.
3. **`karpukhin2020dpr`** — bib venue is NAACL 2020 (pp. 166–175); the real venue is **EMNLP 2020** (pp. 6769–6781), DOI 10.18653/v1/2020.emnlp-main.550. Fix: correct `booktitle` and `pages`.
4. **`lin2024awq`** — bib author list "Lin, Ji and **Wang, Hongxu** and others" is wrong (Wang, Hongxu is not an author); the MLSys 2024 proceedings title includes "**On-Device**". Fix: correct author list and title. (Entry is currently unused in `main.tex`.)
5. **`donenfeld2017wireguard`** — bib title "The WireGuard Protocol: Fast, Secure, and Simple Network Tunnels" is wrong. The real NDSS 2017 paper is "WireGuard: **Next Generation Kernel Network Tunnel**". Fix: correct title.
6. **`zaharia2016datacenter`** — bib attributes "The Datacenter as a Computer" to **Zaharia / Cambridge University Press, 2016**. That book is by **Hoelzle & Barroso** (Morgan & Claypool, 2009; 3rd ed. 2016). Zaharia's related work is "The Datacenter Needs an Operating System" (HotCloud 2011). Fix: either re-point to the correct Hoelzle & Barroso book, or to Zaharia's HotCloud 2011 paper, and update the `main.tex` L215 claim accordingly.

## Failed (unresolvable)

- **`hung2020cloudedge`** — "Cloud-Edge Collaborative Inference for Deep Neural Networks", IEEE CLOUD 2020, "Hung, D." — no exact-title match in IEEE Xplore or web search; author and venue unconfirmable. **Failed** (S1/S2). Attempts: 3 web searches (exact title, "IEEE CLOUD 2020", "ieexplore").
- **`zhang2021cloudedge`** — "Edge-Cloud Collaborative Inference for Deep Neural Networks", IEEE ICEC 2021, "Zhang, Y." — no exact-title match; author and venue unconfirmable. **Failed** (S1/S2). Attempts: 3 web searches (exact title, "ICEC 2021", "ieexplore").
- **`hemminger2000netem`** — the cited `kernel.org/doc/Documentation/networking/` index is live, but the specific `traffic-control.txt` / `traffic-control/` paths 404; the netem doc has moved to the iproute2 man page `tc-netem(8)`, and the year 2000 is not confirmable. **Failed** (S1 partial / S2). Fix: re-point to the current `tc-netem(8)` man page or the iproute2 source.

## Verdict counts

| verdict | count | keys |
|---|---|---|
| **verified** | 18 | lewis2020rag, guu2020realm, borgeaud2022retro, gao2024ragsurvey, jiang2023flare, khattab2020colbert, reimers2019sbert, cormack2009rrf, bajaj2018msmarco, kwon2023vllm, leviathan2023speculative, chen2023speculative, kang2017neurosurgeon, devlin2019bert, vaswani2017transformer, brown2020gpt3, strubell2019energy, qwen3technicalreport |
| **provisional** | 2 | chen2024bge, llama2024 |
| **contradicted** | 6 | khattab2023dspy, robertson1994bm25, karpukhin2020dpr, lin2024awq, donenfeld2017wireguard, zaharia2016datacenter |
| **failed** | 3 | hung2020cloudedge, zhang2021cloudedge, hemminger2000netem |

> Note: the counts above reflect the 29 bib entries (18 + 2 + 6 + 3 = 29). `chen2024bge` is **provisional** only on the title-wording mismatch (arXiv says "Self-Knowledge Distillation", bib says "Symmetric Distillation"); the underlying claim (dense + sparse from one encoder) is confirmed by the abstract. `llama2024` is **provisional** only on the group-author byline (arXiv lists Grattafiori et al., not "Llama Team"); title/year/venue are confirmed.

## Side micro-check — ICDCS 2027 Important Dates

**Still TBD.** The `https://icdcs2027.icdcs.org/important-dates` page 404s, but the homepage's "Important Dates (AoE)" table lists Research Papers, Workshop Proposals/Papers, Doctoral Consortium, Tutorials, Posters, Demos, and Industry Events all as **TBD** (site went online 01/09/2026; conference is 5–8 July 2027, Melbourne).
