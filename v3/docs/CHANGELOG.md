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


## 2026-08-17 (later still) — round-3 audit

**FAIL, 2 of 6 categories** (RETRIEVAL_QUALITY, GROUNDING/CITATIONS), with
DATA_SAFETY and TEST_COVERAGE conditional and DOCUMENTATION_HONESTY passing.
The reviewer re-ran its own revert matrix across 22 fixes and found 19 red,
confirmed no code path touches a source file, and accepted the D-20
unattributed-evidence deviation with three conditions.

Fixed: three routes past the relevance floor (body-mentioned codes,
per-set rather than per-item gating, and generic domain vocabulary);
cross-project consent narrowed from a greedy regex to anchored phrases;
volatile patterns bounded so the §84 escape hatch cannot excuse a document;
`PASS_WITH_EXCUSES` separated from `PASS`; the audit now cites the artifact it
graded; the project-label sync fails loudly instead of silently restoring an
isolation leak; the duplicate-question key normalizes punctuation; the third
private tokenizer removed. See FAILURES.md F-V3-26..30.

The relevance floor now weights shared terms by MEASURED document frequency
from the index rather than by a count, falling back to a word list only when
the corpus is too small for a frequency to mean anything — and labelling the
result when it does.

Test suite 139 → 179. All 17 round-3 fixes were individually reverted and
their tests observed to fail; two tests that did not bite on revert were
rewritten until they did.


## 2026-08-17 (round 4) — audit FAIL, three categories regressed

**FAIL, 4 of 6.** DATA_SAFETY, TEST_COVERAGE and DOCUMENTATION_HONESTY all
regressed from their round-3 results. The cause is uncomfortable and worth
recording: two round-3 fixes had been written to the two literal examples the
reviewer cited, not to the class of defect behind them.

* The relevance floor's document-frequency weighting REPLACED the boilerplate
  list rather than adding to it, so above the 200-chunk threshold the list was
  dead code and boilerplate words with low measured frequency were restored as
  discriminating. At production scale that re-opened the exact hole it was
  written to close. Measurement may now only make the floor stricter.
* The volatile-pattern check refused bare `*` and bare `*.pdf` — and accepted
  `*/Dawson/*`. It is now a positive rule: a declaration must name a directory
  already excluded from indexing. `reviewer.audit` requires `verdict == PASS`.
* No test ever executed the measured-DF path: the fixture was 6 chunks against
  a 200-chunk threshold, so disabling the plumbing entirely left the suite
  green. There is now a 260-document fixture and an end-to-end test, plus a
  guard test that the fixture stays large enough.
* DECISIONS.md D-20 is WITHDRAWN. Its justifying figure — "43,897 of ~45,000
  files carry project=UNKNOWN" — was the document_type unknown count. The true
  project-unknown share is ≈0%, so the strict rule the round-2 reviewer ordered
  costs almost nothing, and is now implemented.
* Document codes match as filename components (`L-201-RevB` was refused);
  naming a file no longer certifies arbitrary chunks of it; the project label is
  stripped as a phrase rather than term by term; `wrong_project_rate` excludes
  unattributed sources instead of scoring them correct; citations carry
  `project_source`.

See FAILURES.md F-V3-31..36. Test suite 179 → 205; all 13 fixes individually
reverted and observed red, including two tests that did not bite on the first
attempt and were rewritten until they did.
