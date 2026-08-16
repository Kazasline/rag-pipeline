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
