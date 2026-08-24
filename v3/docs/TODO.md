# TODO — phase checklist (§92)

Legend: [x] done+verified · [~] code done, on-machine execution pending · [ ] open

- [x] PHASE 0 code — read-only machine inspector (`inspect_machine.py`)
- [x] PHASE 0 run — executed 2026-08-17: RTX 5080/16GB, Ollama-only, 662k files
- [x] PHASE 1 code — read-only inventory/manifest
- [x] PHASE 1 run — 20k-file pilot inventory done; full pass (~95 min) pending
- [x] PHASE 2 — technical research folded into DECISIONS.md (verify currency
      of Qdrant/vLLM/LM Studio/ColPali on-machine before final choices, §48/§97)
- [x] PHASE 3 — architecture (ARCHITECTURE.md)
- [ ] PHASE 4 — Reviewer architecture review (independent session; veto power)
- [x] PHASE 5 — workspace builder (auto-created numbered layout)
- [x] PHASE 6 — ingestion pipeline (+ quarantine)
- [x] PHASE 7 — metadata/indexing (manifest, states, duplicates, revisions)
- [x] PHASE 8 — hybrid retrieval (exact/sparse/dense/fusion/policies)
- [x] PHASE 9 — LLM integration (OpenAI-compatible, TTFT, mode mapping)
- [x] PHASE 10 — FAST mode (deterministic routing, small evidence, honest bail-out)
- [x] PHASE 11 — DEEP mode (broader candidates, source expansion, verifier)
- [x] PHASE 12 — graph retrieval (provenance edges, k-hop, SUPERSEDES)
- [~] PHASE 13 — multimodal: page renders + OCR + DXF harvest shipped;
      visual-embedding retrieval = on-GPU evaluation task (§20)
- [x] PHASE 14 — FULLSWING (widest policy, 2-hop, second-pass retrieval)
- [x] PHASE 15 — evidence verifier (project/revision/conflict/relevance floor)
- [~] PHASE 16 — benchmark harness ready; REAL runs require machine + reviewed
      question set (`bench make` → human review → `bench run` / `bench compare`)
- [ ] PHASE 17 — independent Reviewer tests on-machine (`alirag review` + countersign)
- [ ] PHASE 18 — fix failures from Phase 17
- [ ] PHASE 19 — regression (re-run pytest + bench after fixes)
- [~] PHASE 20 — docs complete for current state; finalize with real numbers

## Non-phase items

- [~] Serving backend: Ollama in use; llama.cpp/vLLM-under-WSL2 still unbenchmarked (§33)
- [ ] Verify `ollama_think` reasoning param against the installed Ollama version (§34)
- [ ] Evaluate Qdrant vs memmap at real scale (§18/§86)
- [ ] Evaluate cross-encoder reranker (§37) and ColPali-style visual retrieval (§20)
- [ ] Tune retrieval policies (candidate counts) from benchmark (§9/§13)
- [ ] Multimodal §83 acceptance test with real drawings
- [ ] DWG→DXF/PDF read-only export path for CAD sheets (§24; needs AutoCAD/ODA on-machine)
- [ ] Optional: nightly incremental ingest task (mirror V1's watchdog pattern)

## Added after the first on-machine run (2026-08-17)

- [x] Inspector must scan disk for weights, not just query servers (F-V3-05)
- [x] Safety verify must attribute changes to RAG vs external writers (F-V3-03)
- [x] Document-type inference must use word boundaries (F-V3-04)
- [x] One-click bootstrap (`SETUP_V3.bat`) and run-through (`GO_V3.bat`)
- [ ] Pilot ingest + first real queries (script ready, awaiting run)
- [ ] Review the 10,735 `unsupported` files before full indexing (§64)
- [ ] Investigate the 11.5 GB of VRAM held at inspection time
- [ ] Wire page images into the vision path now that qwen3.8 accepts images (§21)


## Deferred with conditions (round-2 reviewer, accepted as recorded)

- [ ] **N10 — Qdrant payload sync. MUST be fixed before Qdrant is ever
      enabled.** `inventory.reinfer_metadata` syncs corrected project labels
      into the FTS index but not into Qdrant payloads. `dense.backend` defaults
      to `auto`, so merely starting a Qdrant server silently switches the
      backend, and its project filter would then run against pre-correction
      labels — the F3(c) defect, unfixed on the other backend. Either sync
      payloads on re-inference or refuse to auto-select Qdrant when the
      manifest has been re-inferred since the last full ingest.
- [x] N11 — `/explain` leaked the internal cache fingerprint; `/query` already
      stripped it. Fixed with a test.
