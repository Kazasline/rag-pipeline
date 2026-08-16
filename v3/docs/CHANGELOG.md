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
