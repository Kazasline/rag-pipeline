# TODO — phase checklist (§92)

Legend: [x] done+verified · [~] code done, on-machine execution pending · [ ] open

- [x] PHASE 0 code — read-only machine inspector (`inspect_machine.py`)
- [~] PHASE 0 run — execute on the Windows PC; record Qwen model/quant/runtime
- [x] PHASE 1 code — read-only inventory/manifest
- [~] PHASE 1 run — E:\ inventory + organization report + safety verify
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

- [ ] Decide Qwen serving backend from §33 measurements
- [ ] Verify `reasoning_param_style` against chosen backend docs (§34)
- [ ] Evaluate Qdrant vs memmap at real scale (§18/§86)
- [ ] Evaluate cross-encoder reranker (§37) and ColPali-style visual retrieval (§20)
- [ ] Tune retrieval policies (candidate counts) from benchmark (§9/§13)
- [ ] Multimodal §83 acceptance test with real drawings
- [ ] DWG→DXF/PDF read-only export path for CAD sheets (§24; needs AutoCAD/ODA on-machine)
- [ ] Optional: nightly incremental ingest task (mirror V1's watchdog pattern)
