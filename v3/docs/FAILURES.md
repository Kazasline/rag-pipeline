# FAILURES — failure memory (§94)

Format: FAILURE ID / DATE / COMPONENT / SYMPTOM / ROOT CAUSE / FIX / RESULT.

## Inherited from V1 (do not repeat — §94)

**F-V1-01 / 2026-06-26 / index storage / "database disk image is malformed"
(twice)** — index lived on the pCloud virtual drive; sync engine rewrote
files under open handles. FIX: index on a real local disk (`C:\RAGData`).
RESULT: no corruption since. V3 rule: never place `08_VECTOR_INDEX`/SQLite
on a cloud-synced drive (D-14).

**F-V1-02 / 2026-06-27 / Docling/RapidOCR / infinite hang on pathological
scans froze whole runs** — no internal timeout. FIX: attempt-log before
processing + watchdog restart + poison list; per-file process timeout.
RESULT: poison file costs one cycle, not an infinite loop. V3 keeps the
pattern for Docling passes.

**F-V1-03 / — / MCP gateway / index invisible under %LOCALAPPDATA%** —
copy-on-write overlay hid real files from child processes. FIX: index
outside the overlay path. V3 rule: verify readback from the serving process,
not just the writing one (§79).

**F-V1-04 / — / page rendering / 18MB PNGs from A0/A1 sheets timed out the
transport** — fixed DPI on huge sheets. FIX: pixel-budget cap. V3:
`page_image_max_px=1600` default.

## V3 build session (2026-08-16)

**F-V3-01 / 2026-08-16 / retrieval / sqlite3.ProgrammingError: objects
created in a thread…** — sparse leg runs in a ThreadPoolExecutor; SQLite
connections were thread-bound. FIX: `check_same_thread=False` on manifest/
sparse/graph connections (cross-thread use is read-only; sqlite3 serialized
mode). RESULT: full suite green; caught by `test_exact_id_query_fast_path`.

**F-V3-02 / 2026-08-16 / verifier / nonsense query returned PARTIAL with
irrelevant "evidence"** — dense retrieval always returns nearest neighbors;
sufficiency only counted items. FIX: relevance floor (D-12) — dense-only
evidence with zero lexical/term connection ⇒ INSUFFICIENT. RESULT: caught by
`test_insufficient_evidence_honest`, now green.

## First real E:\ run (2026-08-17) — 662,244 files, RTX 5080 / 16GB

Both of these were invisible on synthetic data. They are exactly why §92
requires a real Phase 0/1 pass before any production indexing.

**F-V3-03 / 2026-08-17 / safety verify / `pass: false` on a run where the RAG
touched nothing** — SYMPTOM: 9 files reported modified: hermes cron
heartbeats/locks, SCI_AI_LIBRARY keep-warm lock and logs, WhatsApp bridge
logs. ROOT CAUSE: `verify_snapshot` answered "did anything under the source
roots change?" — but the user's own live services rewrite their own files
continuously, and a 173-second scan straddles those writes. The check could
not distinguish "the RAG modified a document" (a real breach) from "another
process appended to its own log" (normal). FIX: cross-reference every changed
path against the write-audit journal (`written_paths()`), which is complete
because `guarded_write_path()` is the only write route and always journals.
`pass` now answers the §84 question (RAG-attributable change) while
`pass_strict` preserves the old any-change answer, and `external_modified`
lists third-party writers for review. Also added `hermes` / `sci_ai_library`
to `exclude_dirs`. RESULT: `test_verify_attributes_external_writers` and
`test_verify_still_fails_on_rag_attributable_change` — both green.
LESSON: an acceptance test that cannot attribute a change is not evidence.

**F-V3-04 / 2026-08-17 / inventory / 13,651 files mislabelled `MEMO`** —
SYMPTOM: pilot inventory reported MEMO as the dominant document type, plus
141 spurious `VO`. ROOT CAUSE: `infer_metadata` matched document-type hints
as plain substrings over the FULL PATH: the folder `AI MAIN MEMORY` contains
"memo", and two-letter tokens like `vo`/`bq`/`lai` match inside ordinary words
(`lain` is common Malay). This violates §4 — metadata must never be invented.
FIX: hints compiled to `\b`-anchored regexes, abbreviations (≤3 letters)
additionally require non-letter neighbours, and document type is matched
against the FILENAME only (a parent folder describes the project, not this
file's type); discipline still may come from folders. RESULT: four regression
tests in `test_regressions.py`, all green.

**VERIFIED ON REAL DATA (2026-08-17):** after `inventory --reinfer` over 45,645
manifest rows, `MEMO` fell from 13,647 to **66** and `VO` from 141 to **33**,
while genuine types resolved sensibly (SPECIFICATION 226, BQ 146,
CORRESPONDENCE 124, TENDER 116, PAYMENT 102, CPC 101, CLAIM 93, SUBMISSION 81,
LAI 62, RFI 26). 23,199 rows corrected in 1.8 s.
LESSON: substring matching over paths silently manufactures metadata at scale.

**F-V3-05 / 2026-08-17 / Phase 0 inspector / reported "Qwen3.8-27B does not
exist" when the model was in fact installed** — SYMPTOM: the inspection report
listed only Ollama-registered models, so a Qwen downloaded through Unsloth was
invisible and the config was drafted around the wrong model. ROOT CAUSE: the
inspector asked the *servers* what they had (Ollama API, LM Studio/vLLM ports)
and never looked at the *filesystem*. Weights pulled via Unsloth or the
HuggingFace cache belong to no server until one is started, so a server-only
probe under-reports what is installed. §3 explicitly lists Unsloth among the
things to inspect; it was omitted. FIX: added a bounded read-only weight scan
(`find_local_models`) over HF/Unsloth/LM Studio/Ollama caches and common model
directories, reporting path, size and quantization; added detection of the
`unsloth`/`llama_cpp`/`vllm` Python packages; added the `weights_without_server`
flag for the real state "model present, nothing serving it". RESULT: verified
against a synthetic model tree; awaiting re-run on the target machine.
LESSON: "not found by the API I happened to query" is not "not installed" —
inspect the disk, not just the daemons.

**F-V3-06 / 2026-08-17 / CLI / UnicodeEncodeError killed a query mid-answer** —
SYMPTOM: `alirag query "deep: ..."` crashed with `'charmap' codec can't encode
character '\u2192'` after retrieval and generation had already succeeded; the
answer was lost. ROOT CAUSE: the Windows console defaults to cp1252 and the CLI
printed JSON with `ensure_ascii=False`, so any arrow, box-drawing or CJK
character in retrieved document text was unencodable. V1 reconfigured stdout to
UTF-8 for exactly this reason; V3 did not carry the lesson over. FIX: reconfigure
stdout/stderr to UTF-8 with `errors="replace"` at CLI import, plus a fallback
re-encode in `_print`. RESULT: `test_cli_print_survives_non_cp1252_characters`.
LESSON: an answer that cannot be printed is an answer lost — carry V1's
platform lessons forward deliberately.

**F-V3-07 / 2026-08-17 / pilot scope / the pilot indexed the wrong corpus** —
SYMPTOM: after ingesting 300 files, every retrieved source was llama.cpp build
output and AI tooling notes (`compile_commands.json`, `qwen36-ali-system.txt`);
knowledge extensions were 5,139 `.md` and only 64 `.pdf`, and the user's actual
project documents under `E:\SITE CONCEPT INTERNATIONAL\` were never reached.
ROOT CAUSE: `scan()` walks source roots alphabetically and a capped pilot spends
its whole budget on whatever sorts first — here `E:\AI` and `E:\AI MAIN
MEMORY`. Ingestion then processes in discovery order, inheriting the same bias.
FIX: `inventory --root` scopes the walk; `ingest --project/--path` scopes
processing. RESULT: `test_scan_can_be_scoped_to_one_root`,
`test_ingest_can_be_scoped_by_path`.

**VERIFIED ON REAL DATA (2026-08-17):** scoping the scan to
`E:\SITE CONCEPT INTERNATIONAL` surfaced the actual working corpus —
25,042 files under that project, real PDFs rising from 64 to **4,170** and
drawings from 1,148 to **4,228** `.dwg`.
LESSON: a capped pass over a large drive samples the alphabet, not the corpus.

**F-V3-08 / 2026-08-17 / inventory / a metadata fix could not reach existing
rows** — SYMPTOM: after fixing F-V3-04, a re-scan still reported 13,647 MEMO
files. ROOT CAUSE: `scan()` skips files whose path+size+mtime are unchanged, so
corrected inference rules never touched the rows they had mislabelled — the bad
metadata was effectively frozen. FIX: `inventory --reinfer` re-applies the rules
to manifest rows without re-hashing, and rebuilds revision links. RESULT:
`test_reinfer_updates_existing_rows_without_rehash`, plus a verified real run:
45,645 rows examined, 23,199 updated, 369 revision links rebuilt, 1.8 s.
LESSON: incremental-by-content means rule changes need their own migration path.

**F-V3-09 / 2026-08-17 / LLM client / 22 seconds of generation produced an
empty answer reported as SUPPORTED** — SYMPTOM: `cepat: cari LANDSCAPE
SUBMISSION SUNGAI DUA` retrieved three correct PDFs with page numbers, then
returned `"answer": ""` with `ttft_ms: 0`, `generation: 22,482 ms`,
`evidence_status: SUPPORTED` and `confidence: 0.93`. ROOT CAUSE: Qwen3.x is a
reasoning model and streams chain-of-thought in a separate delta field
(`reasoning_content`/`reasoning`/`thinking`), emitting `content` only
afterwards. The client counted `content` alone, so a model that spent its
entire 400-token FAST budget thinking was indistinguishable from one that
answered nothing — and the answer layer passed the empty string through with
retrieval's confidence attached, violating §39/§79. FIX: count reasoning
deltas separately, capture `finish_reason`, and derive an `empty_reason`
explaining the failure; the answer layer now returns PARTIAL with confidence
0.0, the diagnostic, and the retrieved sources instead of an empty string.
Token budgets raised (FAST 400→1024, DEEP 1200→3000, FULLSWING 3000→8000) so
the budget covers thinking plus the answer. RESULT:
`test_empty_answer_is_reported_not_presented_as_supported`,
`test_reasoning_deltas_counted_separately`.
LESSON: silence from a model is a result to explain, not an answer to forward.
Also: retrieval succeeding is not the system succeeding.

**F-V3-10 / 2026-08-17 / exclusions / every ingest failure was build output** —
SYMPTOM: all 56 FAILED files were `.png` under
`E:\AI\llama.cpp-src\build\tools\ui\dist\_gzip\` — gzip-compressed PWA
icons that PIL cannot identify. ROOT CAUSE: build/dist directories were not
excluded, so web-app assets were queued as knowledge images. FIX: added
`llama.cpp`, `llama.cpp-src`, `dist`, `build` and `_gzip` to `exclude_dirs`
(§64: infrastructure is not corpus). RESULT: those files are inventoried and
skipped rather than attempted; a re-run should report 0 failures.
LESSON: read the failure list — a uniform failure signature usually means the
wrong files were queued, not that the parser is broken.
