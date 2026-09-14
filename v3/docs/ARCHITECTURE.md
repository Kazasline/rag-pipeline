# ARCHITECTURE — ALI RAG V3

One orchestrator + specialized retrieval tools + one independent reviewer
(§58). No chatty agent swarm; query-time control flow is deterministic
Python, the LLM is called once per answer.

## Module map (`v3/alirag/`)

| Module | Role | Spec |
|---|---|---|
| `config.py` | portable config; workspace layout; per-mode retrieval policies | §5, §9, §13 |
| `safety.py` | read-only source guard, write audit, §84 snapshot/verify | §1, §84 |
| `inspect_machine.py` | Phase 0 read-only hardware/runtime probe | §3 |
| `inventory.py` | Phase 1 read-only inventory, hashing, classification, revision families, duplicates | §4, §27–§28, §63–§64 |
| `manifest.py` | SQLite identity backbone: hash→file_id→derived data; §78 states | §4, §6, §78 |
| `extract.py` | provenance-preserving parsers (PDF/DOCX/XLSX/PPTX/TXT/MD/CSV/JSON/EML/images/DXF-read-only) + page renders | §19, §22–§24 |
| `chunk.py` | hierarchical chunking, parent/child ords, boundary-aware splits | §29 |
| `embed.py` | bge-m3 via Ollama (prod) / hash (tests) | §35–§36 |
| `sparse.py` | FTS5 BM25 + exact-ID table (normalized codes) | §9, §17 |
| `dense.py` | Qdrant (primary candidate) / memmap f32 (fallback + baseline) | §18, §86 |
| `graph.py` | SQLite graph, evidence-carrying edges, k-hop neighborhoods | §25–§27 |
| `ingest.py` | incremental pipeline, quarantine-on-failure | §31–§32, §46 |
| `router.py` | deterministic FAST/DEEP/FULLSWING routing, MS+EN triggers, exact-ID + project detection | §8–§16, §55–§57 |
| `retrieve.py` | orchestrator: parallel legs, RRF fusion, source expansion, policy-gated components | §7, §17, §74 |
| `verify.py` | project isolation, revision currency, conflict surfacing, relevance floor | §38–§39, §60–§62 |
| `llm.py` | OpenAI-compatible streaming client, TTFT measurement, mode→reasoning mapping | §11, §33–§34 |
| `answer.py` | engine: cache, evidence pack, honest insufficiency, §54 response object | §16, §30–§31, §39–§40, §54, §76 |
| `instrument.py` | per-stage traces, p50/p95/p99 aggregation | §41–§42 |
| `bench.py` | Recall@K/MRR/latency harness, layer comparison, fabrication-proof gates | §43–§45, §72, §86 |
| `reviewer.py` | evidence-gated §87 audit; PASS impossible without artifacts | §47–§51, §87 |
| `api.py` | localhost FastAPI: /query /ingest /reindex /status /health /metrics /source /explain | §52–§54 |
| `cli.py` | operator commands incl. safety, restart-test, backup | §77, §84–§85 |

## Mode policies (starting values — benchmark targets, §9/§13)

| | sparse_k | dense_k | fused_k | evidence_k | rerank | graph_hops | verify |
|---|---|---|---|---|---|---|---|
| FAST | 20 | 20 | 10 | 4 | no | 0 | deterministic checks only |
| DEEP | 60 | 60 | 30 | 8 | yes | 1 | yes |
| FULLSWING | 120 | 120 | 40 | 12 | yes | 2 | yes + second retrieval pass |

## Data flow

**Ingest** (never at query time, §11): inventory hashes → changed files only →
extract (provenance segments) → hierarchical chunks → FTS5 + ID harvest →
precomputed dense vectors → graph edges with chunk provenance → page images
(optional `--render`). One bad file → FAILED + note, run continues (§46).

**Query**: route (no LLM) → policy → [exact ∥ sparse ∥ dense (∥ graph)] →
RRF → hydrate provenance → verify → evidence pack (smallest context, §30) →
one streamed LLM call (TTFT measured) → §54 response with §40 citations.
Cache keyed on (mode, normalized query), invalidated by index fingerprint (§31).

## Multimodal position (honest)

Shipped now: page-image derivatives at ingest, OCR for scans/images,
locator-cited tables (sheet/rows), DXF read-only harvest for CAD. Visual
*embedding* retrieval (ColPali/ColQwen, Qdrant multivectors) is an
**evaluation task on the real GPU** (§20) with `10_VISUAL_INDEX` reserved and
the retrieval orchestrator ready to take a `visual` leg. It is not claimed as
working because it has not been benchmarked on real drawings (§79).

## Failure containment (§46)

Per-file try/except with quarantine states; WAL SQLite everywhere; append-only
vector file; attempt-before-process pattern available for Docling passes
(inherited from V1's poison-list design); restart safety proven by
`restart-test` and the persistence test.
