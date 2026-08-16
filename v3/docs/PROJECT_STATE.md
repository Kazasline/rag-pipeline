# PROJECT_STATE — ALI RAG V3

_Last update: 2026-08-16 (build session, remote Linux container)_

## Where we are

**Phase 0–15 code: COMPLETE.  Phase 0–1 execution on the target machine: NOT
STARTED.  Phases 16–19 (benchmark/review on real hardware): NOT STARTED.**

The V3 system was designed and implemented in a cloud session that has access
to this repository but **not** to the user's Windows PC, GPU, `E:\` drive, or
local Qwen installation. Therefore:

* Everything testable off-target has been tested for real: **49 pytest tests
  green** covering router, chunking, sparse/dense/fusion, inventory safety,
  duplicate + revision detection, project isolation, verifier honesty,
  benchmark gating, cache invalidation, restart persistence.
* **No latency, recall, TTFT or hardware number is claimed anywhere.** The
  reviewer audit (`alirag review`) reports PENDING for every category that
  requires on-machine evidence, by design.

## Exact next step (do this first in the next session)

1. On the Windows PC: `cd rag-pipeline && git pull`, install
   `v3/requirements.txt` into the existing venv.
2. `v3\run_phase0.bat` → read the report in `E:\ALI_RAG\17_REPORTS\`.
   It detects the ACTUAL Qwen model/quantization/runtime and GPU/VRAM.
3. Write `E:\ALI_RAG\01_CONFIG\config.yaml` from the report: `llm.base_url`,
   `llm.model`, `llm.reasoning_param_style` (verify against the chosen
   backend's docs — §34).
4. `alirag safety snapshot` → `run_phase1_inventory.bat` → `alirag safety
   verify` (must pass) → review the organization report before any ingestion.
5. Pilot-ingest 500 files, spot-check retrieval, then full ingestion.

## Key facts a future session must not re-derive

* Primary answer model: the prompt names "Qwen3.8-27B"; the REAL installed
  model/quant must come from Phase 0 — config deliberately ships with
  `model: UNVERIFIED-set-after-phase0-inspection`.
* Embeddings: bge-m3 @1024 via local Ollama (V1-proven, EN+MS). `hash`
  provider exists for offline tests only.
* Dense backend: Qdrant if reachable, else memmap fallback (also the §86
  baseline). Backend visible in `/status`.
* V1 (repo root) stays untouched and in production for WhatsApp/OpenClaw
  queries; V3 is additive under `v3/`.
* V1 hard lessons already baked in: index must NOT live on a cloud-synced
  drive (pCloud corrupted it twice); page renders pixel-budgeted; poison-list
  pattern for hanging parsers; single-writer ingest.
* Workspace layout = numbered folders under `E:\ALI_RAG` (spec §5), created
  on demand by `Config.dir()`.

## Unresolved / waiting on hardware

* Qwen serving backend choice (LM Studio vs llama.cpp vs vLLM on Windows) — §33 benchmark.
* Whether Qdrant earns its service overhead vs memmap at real corpus scale — §18/§86.
* Reranker: cross-encoder candidates only if latency is paid back (§37); the
  shipped lexical-overlap rerank is a placeholder signal, not the final answer.
* Visual retrieval: page images are rendered at ingest (`--render`); ColPali/
  ColQwen-style visual embedding is an evaluation task (§20) — NOT shipped as
  a claim. `10_VISUAL_INDEX` reserved.
* FAST TTFT target (<1s p50) feasibility on the real GPU — measure, §10/§88.
