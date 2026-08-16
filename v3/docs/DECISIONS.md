# DECISIONS — ALI RAG V3

Format: ID / decision / alternatives / why / revisit-when.

**D-01 — V3 is additive under `v3/`; V1 stays untouched.**
Alternatives: refactor V1 in place. Why: V1 is in production (WhatsApp/
OpenClaw); §1-grade caution applies to working software too. Revisit: after
V3 passes reviewer on-machine.

**D-02 — SQLite (WAL) for manifest, sparse, graph; no server DBs required.**
Alternatives: Postgres, Neo4j, dedicated graph stores. Why: zero services to
babysit on a Windows workstation, restart-safe, rebuildable (§67), FTS5 gives
BM25 for free. Revisit: corpus growth makes FTS5/graph queries the measured
bottleneck (§42 data, not vibes).

**D-03 — Dense backend = Qdrant if reachable, memmap fallback; `auto` mode.**
Alternatives: Qdrant-only, FAISS, LanceDB, Milvus. Why: §18 names Qdrant the
primary candidate but forbids choosing by name — memmap is the V1-proven
floor and doubles as the §86 baseline; the backend is a config swap, and
`/status` always tells the truth about which is active. Revisit: §86 layer
benchmark on the real corpus.

**D-04 — bge-m3 @1024 stays the embedding default.**
Alternatives: newer multilingual embedders. Why: §36/§97 — proven on THIS
bilingual corpus in V1; replacements must beat it on the local retrieval test
set, not a leaderboard. Revisit: `bench embed` results.

**D-05 — Router is deterministic (regex/keyword), never an LLM call.**
Alternatives: LLM classification. Why: §9 — FAST cannot afford a model
round-trip to decide to be fast; triggers are auditable and testable.
Revisit: only if measured misrouting exceeds ~5% on the benchmark's routing
column.

**D-06 — Exact-ID retrieval is a separate normalized index, not an FTS trick.**
Why: FTS tokenizers split `LAI-003` / `KP-980ASPEN-CS-LANDSCAPE-26`;
normalized-code equality is deterministic and §9 demands exact-first.

**D-07 — RRF (k=60) fusion with a weight boost for exact hits.**
Alternatives: score normalization, learned fusion. Why: §17 — RRF is the
robust default; weights are config. Revisit: fusion A/B in §86 runs.

**D-08 — Shipped reranker = cheap lexical-overlap; cross-encoder deferred.**
Why: §35/§37 — a cross-encoder competes with the 27B for VRAM; it enters only
if the on-machine benchmark shows the latency is repaid in nDCG.

**D-09 — Graph = SQLite + rule-based extraction with mandatory provenance.**
Alternatives: LightRAG/HippoRAG frameworks, LLM entity extraction. Why: §25
requires evidence-backed edges; regex extraction of codes/revisions/projects
is exact, free and local. LLM-assisted extraction can enrich later (DEEP
ingestion pass) — framework adoption is a §48 reviewer-gated upgrade.
Revisit: multi-hop benchmark shows recall ceiling.

**D-10 — LLM client speaks OpenAI-compatible chat only.**
Why: §33 — LM Studio, llama.cpp, vLLM, SGLang, Ollama all expose it, so the
backend shootout is a config change. `reasoning_param_style` isolates the
§34 per-backend thinking-mode syntax that must be verified from docs.

**D-11 — `llm.model` ships as `UNVERIFIED-…`.**
Why: §0/§3 — the prompt's "Qwen3.8-27B" is unverified against the machine;
Phase 0 detects reality. Refusing a plausible default is deliberate: the
system fails loudly instead of silently querying the wrong model.

**D-12 — Verifier includes a relevance floor (INSUFFICIENT when only dense
neighbors with zero lexical/term connection).**
Why: found during testing — dense retrieval always returns *something*; §39
requires refusing to dress neighbors up as evidence. Trade-off documented in
`verify.py`: cross-language paraphrases usually still share codes/names; DEEP
is the sanctioned escalation if the floor misfires.

**D-13 — Benchmarks refuse placeholders and empty sets; reviewer audit is
evidence-gated.**
Why: §43/§51/§79 — makes fabricated success structurally impossible, not just
discouraged.

**D-14 — Workspace may live on the source drive (`E:\ALI_RAG`) as an audited
carve-out; the index should live on the fastest LOCAL disk if E: is
cloud-synced.**
Why: §5 wants E:\ALI_RAG; V1's pCloud corruption (2026-06-26) proves synced
drives kill SQLite/memmap. Phase 0 reports drive types; if E: is synced,
point `workspace` (or at least `08_VECTOR_INDEX`) at a local disk — config
supports it.
