# ALI RAG V3 — Agentic Hybrid Multimodal Graph RAG

Local, evidence-grounded RAG over the working drive (default `E:\`), with
automatic **FAST / DEEP / FULLSWING** reasoning modes, hybrid retrieval
(exact-ID + BM25 + dense + metadata + graph), strict data safety, and
measurement-first performance engineering.

V3 lives alongside the proven V1 pipeline (repo root) and reuses its lessons:
bge-m3 embeddings via Ollama, Docling parsing, memmap dense fallback,
index-never-on-cloud-drive, pixel-budgeted page renders.

```
USER ──▶ router (deterministic, no LLM) ──▶ FAST | DEEP | FULLSWING policy
              │
              ▼
      retrieval orchestrator
      ├─ exact-ID (normalized codes: LAI-003, L-201, KP-…)
      ├─ sparse  (SQLite FTS5 BM25)     ┐ run in
      ├─ dense   (Qdrant | memmap f32)  ┘ parallel
      ├─ metadata / project filter
      └─ graph   (provenance-carrying, k-hop)   [DEEP/FULLSWING]
              │
              ▼
      RRF fusion ──▶ rerank [DEEP+] ──▶ evidence verifier
              │        (project isolation, revision currency,
              ▼         conflict surfacing, relevance floor)
      evidence pack ──▶ local LLM (OpenAI-compatible endpoint)
              │
              ▼
      grounded answer + sources (file / page / sheet / revision / project)
```

## Status — read this first

**Code complete and tested off-target (49 tests green). All hardware-truth
steps still have to run on the actual Windows machine.** Nothing below the
line "on-machine phases" has been executed, and no latency/recall number is
claimed anywhere in this repo. See `docs/PROJECT_STATE.md` for the exact
next step.

## Install (Windows target machine)

```bat
cd rag-pipeline
python -m venv .venv          # reuse the existing V1 venv if present
.venv\Scripts\activate
pip install -r v3\requirements.txt
```

## Phase order (§92) — on-machine phases

```bat
cd v3
run_phase0.bat                     :: read-only machine inspection -> report JSON
:: review the report; set llm.model/base_url in E:\ALI_RAG\01_CONFIG\config.yaml
..\.venv\Scripts\python -m alirag.cli safety snapshot
run_phase1_inventory.bat           :: read-only E:\ inventory -> manifest
..\.venv\Scripts\python -m alirag.cli safety verify   :: must report pass:true
run_ingest.bat --limit 500        :: pilot ingestion, then full
..\.venv\Scripts\python -m alirag.cli query "cepat: find LAI-003"
..\.venv\Scripts\python -m alirag.cli bench make      :: draft questions, REVIEW them
..\.venv\Scripts\python -m alirag.cli bench run --label fast
..\.venv\Scripts\python -m alirag.cli bench compare   :: §86 layer comparison
..\.venv\Scripts\python -m alirag.cli review          :: evidence-gated audit
```

## Startup / shutdown

* **Start**: `run_serve.bat` (local API on `127.0.0.1:8642`; engine and model
  connections stay warm). Ollama (embeddings) and the LLM server (LM Studio /
  llama.cpp / vLLM) must be running first; `/health` reports both.
* **Shutdown**: close the serve window (Ctrl+C). All stores are WAL-mode
  SQLite + append-only files; there is no unsafe shutdown state.
* **Restart check**: `python -m alirag.cli restart-test` (§85) — verifies
  manifest/dense/graph identical across fresh handles and a query still runs.

## Backup / reindex

* `python -m alirag.cli backup` — copies config, manifest, graph, benchmark,
  skills to `18_BACKUP_CONFIG\<timestamp>\` (§66). Dense/sparse indexes are
  **derived artifacts**: rebuildable any time from originals + config +
  manifest (§67) via `inventory` + `ingest`.
* Reindex one file: `POST /reindex {file_id}` or re-run `ingest` after the
  file changes — the content hash triggers re-processing of just that file.

## Troubleshooting

| Symptom | Check |
|---|---|
| `LLM unavailable` in answers | LLM server running? `llm.base_url` right? `/health` |
| Embedding errors at ingest | `ollama list` shows `bge-m3`? Ollama on 11434? |
| Slow FAST queries | `alirag metrics --mode FAST` — find the stage; see §73 order |
| File shows FAILED | `alirag status`; note in manifest `index_note`; fix dep, re-`ingest` |
| Dense dim mismatch error | embed model changed — delete `08_VECTOR_INDEX` contents and re-ingest (originals untouched) |
| Qdrant absent | system auto-falls back to memmap; `/status` shows the active backend |

## Data safety (§1)

Originals are **never** deleted, moved, renamed or written. Enforced by
`safety.SafetyGuard` (read-only source opens; any write path inside a source
root raises; every write audited to `16_LOGS/safety_audit.jsonl`) and proven
per-run by `safety snapshot` / `safety verify` (§84). Duplicates and old
revisions are tagged in the manifest, never removed (§27–§28).

## Documentation map

| File | Purpose |
|---|---|
| `docs/PROJECT_STATE.md` | live state + exact next step (read first in a new session) |
| `docs/ARCHITECTURE.md` | component-by-component design & rationale |
| `docs/DECISIONS.md` | decision log with alternatives considered |
| `docs/TODO.md` | phase checklist (§92) with per-item status |
| `docs/BENCHMARK.md` | methodology + result log (real runs only) |
| `docs/REVIEWER.md` | reviewer role, checklist, veto workflow |
| `docs/CHANGELOG.md` / `docs/FAILURES.md` | history & failure memory (§94) |
| `skills/` | operational skills (§69–§70) |
