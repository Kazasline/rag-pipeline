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
