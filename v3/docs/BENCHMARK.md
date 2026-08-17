# BENCHMARK — methodology and result log

**No results exist yet. This file contains methodology only; every number
that ever lands here must carry the machine stamp + report path emitted by
`alirag bench run` (§79). Fabricated or extrapolated figures are forbidden.**

## Question set (§43)

`alirag bench make` drafts `15_BENCHMARK/questions.draft.jsonl` from real
indexed files (locally — nothing leaves the machine). A human rewrites each
`REVIEW-ME` placeholder into a genuine question and saves as
`questions.jsonl`. The harness refuses drafts and empty sets by design.

Cover all kinds: exact lookup, semantic, cross-document, revision comparison,
multi-hop, visual, table, quantity, timeline, contradiction, project
identification, wrong-project trap, duplicate-file trap.

## Metrics (§44–§45)

Retrieval: Recall@5, Recall@10, MRR, citation page accuracy,
wrong-project-in-top3 rate. Latency: p50/p95/p99 end-to-end plus per-stage
percentiles from `alirag metrics` (embedding, sparse, dense, graph, fusion,
prompt build, TTFT, generation, tokens/sec). Cold vs warm reported separately
(§72): first query after service start is cold; discard it from warm stats
but record it.

## Targets (§10 — objectives to measure against, not results)

FAST retrieval p95 ≤ 250 ms · FAST TTFT p50 < 1 s, p95 ≤ 2 s (after warm-up)
· thresholds for recall/MRR to be fixed from the first real run + reviewer
judgment (§44).

## Layer comparison (§86)

`alirag bench compare` runs the same questions as: dense-only baseline →
hybrid (exact+sparse+dense) → hybrid+graph. A layer that doesn't move
recall/MRR (or hurts latency beyond its gain) gets removed, per §89.

## Result log

| date | machine | label | questions | R@5 | R@10 | MRR | wrong-proj | p50 | p95 | report |
|---|---|---|---|---|---|---|---|---|---|---|
| _no scored run yet — needs a human-reviewed question set (§43)_ | | | | | | | | | | |

## First measured latencies (2026-08-17, smoke run — NOT a scored benchmark)

Machine: RTX 5080 16.3 GB (5.9 GB free), Intel 20-core, 68 GB RAM, Ollama.
Index at time of measurement: 16,782 chunks, memmap dense backend.
Source: 3 ad-hoc queries via `alirag metrics`. **n=3 — indicative only.**

### Retrieval stages — comfortably within the §10 objective

| stage | p50 | p95 |
|---|---|---|
| exact_search | 0.0 ms | 0.0 ms |
| sparse_search (FTS5 BM25) | 0.6 ms | 0.8 ms |
| dense_search (memmap) | 258 ms | 1813 ms |
| fusion (RRF) | 0.3 ms | 0.4 ms |
| graph_search | 0.7 ms | — |
| rerank | 0.2 ms | — |
| **retrieval-only total** | **262 ms** | — |

**Correction (reviewer, 2026-08-17):** an earlier version of this file said
"retrieval meets the §10 target (~250 ms p95)". That was wrong twice over: the
262 ms figure is a sum of per-stage **p50s**, and it was compared against a
**p95** objective. The measured `dense_search p95` in the same table is
**1813 ms**, so on this evidence retrieval does **not** meet a 250 ms p95
target. With n=3, p95 equals the maximum anyway. What can honestly be said:
sparse, fusion, graph and rerank are negligible (<15 ms combined), and the
dense leg is the only retrieval stage worth optimising.

### Generation — the real bottleneck is model loading, not decoding

| metric | value |
|---|---|
| TTFT p50 | **28,293 ms** |
| TTFT p95 | 74,714 ms |
| generation (decode) p50 | 462 ms |
| tokens/sec | **116.9** |

Decoding is fast (117 tok/s). TTFT is dominated by Ollama loading weights on
the first query and on every model switch. This is the swap cost D-15 warned
about, now measured: with per-mode models, each mode change pays tens of
seconds. **The §10 FAST objective (TTFT <1 s p50) is not met and cannot be met
while a cold load sits on the critical path.**

Candidate fixes to measure next, cheapest first:
1. `OLLAMA_KEEP_ALIVE=-1` (or a long TTL) so the FAST model stays resident.
2. Collapse to ONE resident model and drop the per-mode split.
3. Serve `Qwen3.8-27B-UD-Q3_K_XL.gguf` (13.4 GB, already on disk) through the
   local llama.cpp build — it fits in 16.3 GB VRAM where the 18 GB Ollama
   build does not, so it can stay resident instead of reloading.

### Second run (2026-08-17, after scoping to the real corpus)

Ingest of 300 real project files: **83 ok, 0 failed**, 33.4 s — versus 328 s
and 56 failures when the pilot was pointed at build output.

Retrieval on the real corpus answered `cepat: cari LANDSCAPE SUBMISSION SUNGAI
DUA` with three correct PDFs, each cited to a page (`Selgate 01.pdf` p.1,
`Fee Proposal.pdf` p.1, `Senarai Semak Sijil Siap Kerja Landskap.pdf` p.2),
all correctly attributed to project SITE CONCEPT INTERNATIONAL, with sparse
and dense legs agreeing. Retrieval stages held: sparse 1.1 ms, fusion 0.3 ms,
dense 429 ms.

Generation, however, returned **empty** after 22.5 s (F-V3-09) — the token
budget went entirely to chain-of-thought. That is fixed but **not yet
re-measured**, so no TTFT improvement is claimed here.

### Third run (2026-08-17) — first genuinely grounded answer

`cepat: berapa jumlah Cert Payment No.11 AVC?` produced a real, cited answer in
the language of the question:

> "Berdasarkan bukti [4], jumlah **AMOUNT DUE TO CONTRACTOR (EXCL GST)** untuk
> Claim No. 11 ialah **RM 97,923.07**. Nota: Previous Claim (Cert no.1-10)
> RM 1,594,215.33, jumlah keseluruhan RM 1,735,526.56."

Evidence spanned four sources including an `.xlsx` cited as
`Sheet 'Certified' rows 1+`, all correctly attributed to the right project.

| metric | value |
|---|---|
| reasoning_tokens | **0** — thinking genuinely disabled (F-V3-11 fixed) |
| finish_reason | `stop` |
| decode | 918 ms @ 115.4 tok/s |
| sparse / fusion | 12 ms / 1.3 ms |
| dense | 860 ms |
| **TTFT** | **20,550 ms — still the objective's blocker** |

TTFT minus decode leaves ~19.6 s of model loading (F-V3-13). `keep_alive` is
now sent per request; the effect is **not yet measured**, so the FAST objective
remains recorded as not met.

### Not measured yet

Recall@K, MRR, citation accuracy and wrong-project rate — all require the
human-reviewed question set (`bench make` → review → `bench run`). The pilot
index also contains the wrong corpus (AI tooling directories rather than
project documents), so any score from it would be meaningless.
