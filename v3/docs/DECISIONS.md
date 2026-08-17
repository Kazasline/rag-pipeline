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

**D-11 — `llm.model` ships as `UNVERIFIED-…`.** _(superseded by D-15 for this
machine; the default in the repo stays UNVERIFIED for any other machine.)_
Why: §0/§3 — the prompt's "Qwen3.8-27B" is unverified against the machine;
Phase 0 detects reality. Refusing a plausible default is deliberate: the
system fails loudly instead of silently querying the wrong model. Vindicated
twice: the model was genuinely absent at first inspection, and F-V3-05 showed
a server-only probe can also under-report what is installed.

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

## Decisions from the first on-machine run (2026-08-17)

**D-15 — Primary model = `qwen3.8:27b`; FAST split onto `qwen3.5:9b`.**
Alternatives: one resident model for all modes; a 9B everywhere; a 35B for
depth. Why: qwen3.8:27b is the brief's intended model and is real (18 GB, 256K
ctx, text+image), but it exceeds 16.3 GB VRAM so it cannot serve a sub-second
FAST path. Splitting keeps `cepat:` genuinely fast while giving depth queries
the better model. Revisit: measured swap cost — if mixed-mode usage pays more
in weight-swapping than it gains, collapse back to one model.

**D-16 — Vision reuses `qwen3.8:27b` rather than a separate VL model.**
Alternatives: keep `qwen3-vl:8b-instruct-q8_0` loaded for images. Why: qwen3.8
accepts image input, so page-image reasoning can use weights already resident
for DEEP instead of a second model competing for a card that is already
oversubscribed (§35). Revisit: if 27B offload makes visual queries unusably
slow, the 8B VL model is the fallback.

**D-17 — Phase 0 inspects the filesystem, not only running daemons.**
Why: F-V3-05 — a model pulled via Unsloth belongs to no server until one
starts, so a server-only probe reported it as absent and nearly drove the
config to the wrong model. Weight scanning is bounded (depth, count, ≥100 MB)
so it stays fast.

**D-18 — Safety verification accounts for every change; it does not merely
attribute the ones it recorded.** SUPERSEDED IN PART by the round-1 reviewer.
The original decision made `pass` mean "no change is attributable to us via the
write journal", with `pass_strict` retained as the any-change view. That is
unfalsifiable: with an empty journal nothing is attributable, so an untouched
system and a system whose journal failed to record look identical, and a
deletion by anything other than this code passed. The verdict now asks the
opposite question — can every difference be ACCOUNTED for? — and fails on any
change that is rag-attributable OR unexplained. Only patterns the operator
declared volatile in advance (`volatile_patterns`, echoed into the report) are
excused, and renames are reconciled by content hash rather than read as
deletions. `pass_strict` no longer exists. The live-service problem that
motivated the original decision is handled by the declaration, which is
reviewable, instead of by a blanket excuse, which was not.

**D-19 — Talk to Ollama through its native `/api/chat`, not the `/v1` shim.**
Alternatives: stay on the OpenAI path and raise token budgets; append a
`/no_think` prompt switch. Why: F-V3-11 — the shim drops `think`, so reasoning
could not be disabled at all and FAST paid a full chain-of-thought on every
query. Raising budgets only fed the thinking. The OpenAI path remains the
default and is still what LM Studio / llama.cpp / vLLM / SGLang use, so
`api_style` keeps both without forking the client. Revisit: if Ollama's shim
gains real `think` support, or if the backend shootout (§33) moves serving to
llama.cpp, where the OpenAI path applies again.
