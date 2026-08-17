# CHANGELOG — ALI RAG V3

## 2026-08-16 — v3.0.0 initial build (remote session)

* Full V3 package under `v3/alirag/`: config, safety guard, Phase 0
  inspector, Phase 1 inventory, manifest, extraction (PDF/DOCX/XLSX/PPTX/
  TXT/MD/CSV/JSON/EML/images/DXF-read-only), hierarchical chunking,
  embedding clients, sparse FTS5 + exact-ID index, dense (Qdrant/memmap),
  provenance graph, incremental ingestion with quarantine, deterministic
  FAST/DEEP/FULLSWING router (EN+MS), parallel hybrid retrieval + RRF,
  evidence verifier (project isolation, revision currency, conflicts,
  relevance floor), streaming LLM client with TTFT, §54 response object with
  §40 citations, query cache with fingerprint invalidation, instrumentation
  with percentile aggregation, fabrication-proof benchmark harness, evidence-
  gated reviewer audit, localhost FastAPI, operator CLI, Windows .bat
  wrappers, docs set (§93), skills (§69–§70), 49-test pytest suite.
* Bugs found and fixed during testing: SQLite cross-thread use in parallel
  retrieval legs (connections now `check_same_thread=False`, reads only);
  verifier lacked a relevance floor — nonsense queries returned PARTIAL from
  dense neighbors (now INSUFFICIENT, see D-12).
* Explicitly NOT done (no target hardware in this session): any on-machine
  run, any benchmark number, Qwen backend selection, visual-embedding
  retrieval evaluation.

## 2026-08-17 — first on-machine run and the fixes it forced

* Phase 0 and Phase 1 executed on the target Windows machine (RTX 5080/16 GB,
  68 GB RAM, 662,244 files on `E:\`, Ollama-only serving, no Qdrant).
* F-V3-03: `safety verify` now attributes each change (RAG vs external writer)
  via the write journal; `pass` answers the §84 question, `pass_strict` keeps
  the any-change view. Live-service directories excluded from indexing.
* F-V3-04: document-type inference moved to word-boundary regexes matched
  against the filename, ending 13,651 false `MEMO` labels and spurious `VO`.
* F-V3-05: Phase 0 now scans HuggingFace/Unsloth/LM Studio/Ollama caches and
  common model directories for weight files, detects the unsloth/llama_cpp/
  vllm packages, and flags "weights present, no server running".
* Organization report gained knowledge/CAD/unsupported extension breakdowns.
* Per-mode model overrides added; config targets qwen3.8:27b for DEEP/
  FULLSWING/vision and qwen3.5:9b for FAST, with the VRAM shortfall recorded.
* `SETUP_V3.bat` (bootstrap) and `GO_V3.bat` (pull → pilot ingest → real
  queries → metrics) added for one-command operation on Windows.
* Test suite 49 → 56.


## 2026-08-17 (later) — two independent reviewer audits and the fixes they forced

The independent reviewer (§47–§51) ran twice and FAILED both times. Neither
verdict is softened here; the findings are recorded in `FAILURES.md` as
F-V3-15..25 and the state of play is in `PROJECT_STATE.md`.

**Round 1 — FAIL, 3 of 6 categories.**
* `safety.py` rebuilt: the old model asked "can we prove WE didn't do it",
  which passed whenever it had no records. Snapshots now carry content hashes,
  every difference is classified rag-attributable / operator-declared /
  UNEXPLAINED, renames reconcile by hash, and the verdict fails on anything
  unaccounted for. `pass_strict` is gone.
* Relevance floor, project isolation (three separate leaks), dense filtering
  before top-k, graph ranking by hop distance instead of ingestion order, LIKE
  escaping, benchmark honesty gates, cache invalidation on code/model/prompt
  change.

**Round 2 — FAIL, 4 of 6 categories.** The round-1 fixes held under the
reviewer's revert matrix except where noted:
* Relevance floor was still bypassable — any sparse hit waived it, and the FTS
  query ORed every token including stopwords. One shared stopword list and
  tokenizer (`terms.py`) now serves both; only a *verified* exact-code match is
  a standalone pass (F-V3-24).
* Multi-project questions are asked, not merged; UNKNOWN-project evidence is
  disclosed and caps the status at PARTIAL rather than merging silently.
* FULLSWING's second pass is re-verified over the combined evidence — it used
  to extend the kept set after the verifier had already run.
* Graph leg scoped before fusion (the last leg filtering after selection).
* The HTTP API had never worked: deferred annotations made FastAPI treat the
  request body as a query parameter, so every POST returned 422. Found only
  because the reviewer failed TEST_COVERAGE for `api.py` having no tests.
* Revision families now link across `SUPERSEDED\` subfolders.
* Benchmark: duplicate questions rejected, minimum distinct-question count,
  minimum sample on the quality/citation gates, and artifact selection by
  recorded time and report kind rather than by filename.
* Volatile-path declarations reachable from config and echoed into the safety
  report, so the §84 gate can be satisfied honestly and the excuse reviewed.
* `PROJECT_STATE.md` claimed every finding had a failing-before test; three
  did not. Corrected, and the tests written (F-V3-25).

Test suite 56 → 139. Every fix above was re-run with the fix reverted to
confirm its test goes red.
