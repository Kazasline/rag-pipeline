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
LESSON: substring matching over paths silently manufactures metadata at scale.
