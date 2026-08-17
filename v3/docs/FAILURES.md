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

**F-V3-11 / 2026-08-17 / LLM client / `think: false` never reached the model** —
SYMPTOM: with FAST budgets raised to 1024, a query still returned no answer:
`reasoning_tokens: 921`, `finish_reason: "length"`. The model was thinking on
every FAST query despite the mode explicitly disabling it. ROOT CAUSE: requests
went to Ollama's OpenAI-compatible shim at `/v1/chat/completions`, which follows
the OpenAI schema and silently discards non-standard fields — so `think` was
dropped in transit and never applied. Only Ollama's native `/api/chat` honours
it. Raising the token budget treated the symptom: thinking always consumed
whatever budget it was given. FIX: added `api_style` (`openai` |
`ollama_native`); the native path posts to `/api/chat` (stripping a trailing
`/v1` from base_url), sets `think` per mode, and parses newline-delimited JSON
with `message.thinking` separate from `message.content`. The machine config now
uses it, and FAST budgets return to answer-sized (800) rather than inflated to
accommodate hidden reasoning. RESULT: `test_ollama_native_stream_parsing`,
`test_ollama_native_enables_thinking_for_deep`.
LESSON: a compatibility shim silently dropping a parameter looks exactly like
the parameter having no effect — verify the knob arrives, don't just set it.

**F-V3-12 / 2026-08-17 / query cache / a fixed bug kept being served from
cache** — SYMPTOM: after F-V3-09/F-V3-11 were fixed and deployed, the same
query still returned the old "model produced 921 reasoning tokens" failure,
with `"cached": true`. ROOT CAUSE: two compounding faults. (a) The empty-answer
path returns `PARTIAL`, and caching excluded only `INSUFFICIENT`, so a FAILED
generation was written to cache as if it were a result. (b) The cache
fingerprint covered only the index (file count + last indexed time), so fixing
the code or switching the model did not invalidate anything — the failure
outlived its cause and made a working fix look broken. FIX: never cache a
response carrying `generation_error`; fingerprint now includes the pipeline
version and the LLM configuration (api_style, models, FAST token budget), so a
code or model change invalidates prior answers. RESULT:
`test_failed_generation_is_never_cached`, `test_config_change_invalidates_cache`.
LESSON: cache validity must cover everything that shapes the answer, not just
the data — and a failure is not a result worth keeping.

**F-V3-13 / 2026-08-17 / serving / TTFT was model loading, not inference** —
SYMPTOM: a successful FAST answer measured `ttft_ms: 20,550` while
`generation: 918 ms` and decoding ran at 115 tok/s — the user waited 20 seconds
for a model that then answered in under a second. ROOT CAUSE: Ollama unloads
idle models, so each query paid a full load. `OLLAMA_KEEP_ALIVE` had been set
as an environment variable, but that depends on the service's environment
rather than the caller's and did not take effect. FIX: send `keep_alive`
(default `-1`, indefinite) with every native request, so residency is
requested by the client that needs it. RESULT:
`test_keep_alive_sent_on_native_requests`; end-to-end effect awaiting
re-measurement on the machine.
LESSON: separate "time to first token" from "time to load the model" before
concluding anything about inference speed.

**F-V3-14 / 2026-08-17 / LLM client / my own keep_alive fix broke every query
with HTTP 400** — SYMPTOM: immediately after shipping F-V3-13, all three FAST
queries failed with `(LLM unavailable: Ollama unreachable at
http://127.0.0.1:11434: HTTP Error 400: Bad Request)`. Retrieval still worked
and cited correct sources, but no answer was generated at all. ROOT CAUSE: two
faults, both mine. (a) `keep_alive` was sent as the STRING `"-1"`. Ollama
accepts a NUMBER of seconds (-1 = indefinitely) or a duration string carrying a
unit ("10m", "24h"); a bare "-1" parses as neither, so the whole request was
rejected. (b) The error handler treated every `OSError` as "unreachable", and
`HTTPError` subclasses `OSError` — so a request that was received and *refused*
was reported as a server that could not be reached, hiding the actual reason in
the discarded response body. FIX: `_keep_alive_value()` sends numeric-looking
values as numbers and passes duration strings through; `HTTPError` is caught
before `OSError` and reports status plus the server's response body. RESULT:
`test_keep_alive_is_sent_as_a_number_not_a_string`,
`test_native_request_keep_alive_is_numeric`,
`test_rejected_request_reports_the_server_reason`. The pre-existing test had
asserted the buggy string form, so it encoded the defect and was corrected.
LESSON: two — a fix shipped without exercising the real backend can be worse
than the bug it replaces; and "unreachable" must never be the label for
"refused", because it sends diagnosis to the wrong place entirely.

## Independent reviewer, Phase 4 (2026-08-17) — 3 of 6 categories FAILED

An independent reviewer with veto authority audited the code (not the docs) and
wrote probe harnesses to break the guarantees. DATA_SAFETY, GROUNDING_HONESTY
and TEST_COVERAGE all failed. Its central finding: the two things the operator
is asked to trust most were **unfalsifiable rather than proven**, and both were
asserted in module docstrings the code did not back.

**F-V3-15 / data safety / the §84 guarantee could not fail** — SYMPTOM: the
reviewer overwrote, deleted and renamed originals through ordinary `open()` /
`unlink()` / `rename()` and every case reported `pass: True`. ROOT CAUSE:
attribution asked "is this path in our write journal?", and
`guarded_write_path()` was called exactly ONCE in the package — by `snapshot()`
itself. Every real write (`dense.py`, `instrument.py`, `answer.py`, `bench.py`,
`cli.py`, `extract.py`, …) bypassed it, so `rag_modified` was empty by
construction and the check passed by default. Compounding faults: (a)
`"REFUSED_write".endswith("write")` was True, so a BLOCKED attempt counted as
our write and could fail an innocent run — attribution wrong in both
directions; (b) `rag_deleted` was mathematically always empty, there being no
guarded delete anywhere; (c) no move detection existed despite the docstring
claiming "0 moved"; (d) the snapshot stored only size+mtime, so an in-place
edit restoring both was invisible to every view; (e) the Builder's own passing
test produced a failure only by FORGING a journal entry no code path can
create — it tested the arithmetic, not the guarantee.
FIX: the model is inverted. Snapshots now carry content hashes; verification
classifies every difference as rag-attributable, operator-declared volatile, or
UNEXPLAINED, and `pass` fails on anything in the first or last category.
Deletions and renames are reconciled by hash. Nothing is excused by default —
excusing a live service requires `allow_volatile()`, an auditable declaration.
`guarded_open()` added as a real write helper; the false "every write goes
through the guard" claim deleted from `__init__.py` rather than left standing.
RESULT: `tests/test_safety_breach.py` — 8 adversarial tests, all of which
FAILED against the previous implementation and pass now.
LESSON: a check that asks "can we prove we did it?" passes whenever it has no
records. It must ask "can this be accounted for?" and fail when it cannot.

**F-V3-16 / grounding / the relevance floor never fired** — SYMPTOM: the
reviewer asked "What is the warranty period for the pump?" against a corpus
containing neither, and got SUPPORTED with four chunks about rain trees; the
query "the" also passed. ROOT CAUSE: the floor accepted any shared 3+ character
token with no stopword list, so ordinary function words satisfied it. §39
protection therefore rested entirely on the LLM obeying its system prompt —
exactly what a deterministic verifier was supposed to backstop. FIX: stopwords
(English + Malay) removed before matching, ≥2 distinct content terms required,
and a query with no content terms at all can no longer pass on the "nothing to
match" branch. RESULT: `test_relevance_floor_rejects_an_off_corpus_question`,
`test_relevance_floor_still_accepts_genuine_matches`.
LESSON: a guard whose threshold is "any token" is not a threshold.
CORRECTION (round-2 audit): this entry was WRONG to present the case as closed.
The reviewer reproduced the identical pump/rain-tree fabrication against the
"fixed" code. The floor was waived entirely whenever the sparse leg returned
rows, and the FTS expression ORs every token with no stopword removal, so a
document sharing only `the` produced a "lexical match". Both tests named above
used `sources=["dense"]`, and a third test explicitly asserted that a sparse
hit passes on its own — the fix was optimized against the tests, not the
defect. See F-V3-24. LESSON ON THE LESSON: a fix verified only through the
path you were thinking about is not verified.

**F-V3-17 / project isolation / three separate leaks (§60)** — (a)
`retrieve.py` ended the project filter with `or hydrated`, restoring the
UNFILTERED list whenever filtering emptied it — a deliberate fail-open on the
isolation boundary; now fails closed. (b) `sparse.search_ids` took no project
parameter, leaving the exact-ID leg — which carries the heaviest RRF weight
(2.0) — entirely unscoped; it now accepts `allowed_chunks`. (c)
`reinfer_metadata` corrected the manifest but not the denormalized `project`
column in FTS, so after any re-inference the sparse leg filtered on stale
labels; it now syncs them.
LESSON: an isolation boundary with a fallback is not a boundary.

**F-V3-18 / dense retrieval / project filter applied after top-k** — the
memmap backend selected the top-k by similarity and filtered afterwards. Over
662k files where one project is a small fraction, the top few hundred rows can
contain zero in-project chunks, so every project-scoped query would silently
return nothing from the dense leg — invisible on a 5-file fixture. Filtering
now happens before selection. RESULT: `test_dense_filter_applied_before_topk`.
LESSON: a defect that only appears at production scale needs a test that
simulates scale, not one that simulates the feature.

**F-V3-19 / docs / overclaim in BENCHMARK.md** — the file stated "retrieval
meets the §10 target (~250 ms p95)" while the same table reported
`dense_search p95 = 1813 ms`. The 262 ms figure was a sum of per-stage p50s
compared against a p95 objective, from n=3 where p95 equals the maximum. The
claim is now corrected in place rather than quietly removed.
LESSON: the one unguarded sentence in an otherwise careful document is the one
that misleads.

**F-V3-20 / GO_V3.bat / the one-click script never snapshotted** — it ran
`safety verify` against a baseline written once by `SETUP_V3.bat`, so every
repeat run verified against a snapshot from first install (covering days of
unrelated user activity) or crashed if setup had been skipped. The flagship
script did not perform the snapshot→work→verify sequence it advertised. Fixed:
it now snapshots immediately before ingest.

**F-V3-21 / revision families / superseded copies never linked** — SYMPTOM: on
the real corpus, revision chains were detected only when both revisions sat in
the same folder. ROOT CAUSE: `link_revision_families` keyed families on the
literal parent directory, but the near-universal filing practice is to MOVE the
previous sheet into a `SUPERSEDED\` (or `OLD\`, `ARCHIVE\`, `2024\`) subfolder.
R00 and R01 therefore became unrelated documents: no `superseded_by`, no
SUPERSEDES edge, and no §62 disclosure when the obsolete sheet was cited — the
system would answer from a voided drawing with no warning. FIX: `_family_dir()`
folds archival subfolders back into their parent before keying, and `project` is
now part of the key so two projects each owning a "Layout Plan R01" are not
linked to each other (§4 — never invent metadata). RESULT:
`test_revision_family_links_across_superseded_subfolder` (fails against the old
key), `test_family_dir_folds_archive_folders_but_not_real_ones`,
`test_same_named_revisions_in_different_projects_are_not_linked`.
LESSON: the filing convention IS part of the data model. Keying on raw path
structure encodes an assumption about how humans file that they do not hold.

**F-V3-22 / api.py / the entire HTTP API returned 422** — SYMPTOM: every
`POST /query` and `POST /explain` answered `422 Unprocessable Entity:
{"loc": ["query", "q"], "msg": "Field required"}`. The API — the interface §53
specifies and the UI layer is meant to talk to — did not work at all. ROOT
CAUSE: `api.py` carried `from __future__ import annotations`, so the handler's
`q: QueryIn` annotation was the STRING `"QueryIn"`; FastAPI resolves annotations
with `get_type_hints()` against module globals, and `QueryIn` is defined inside
`create_app()` (pydantic is imported lazily to keep fastapi optional, §52). The
name was unresolvable, so FastAPI stopped treating the parameter as a request
body and looked for a query-string parameter named `q` instead. FIX: drop the
future import — `X | None` is valid at runtime on the target Python 3.11 — with
the reason recorded at the import site so it is not "cleaned up" back in.
RESULT: `test_api_query_returns_cited_sources_without_internals`,
`test_api_source_endpoint_rejects_unknown_id`, `test_api_explain_bypasses_cache`
— all three fail against the previous file.
LESSON: this is precisely the defect the reviewer predicted when it failed
TEST_COVERAGE for "api.py has no tests". A module with no tests is not
low-risk because it is simple; it is unmeasured. Writing the tests found the
API had never worked.

**F-V3-23 / project isolation / multi-project questions were merged, not
asked** — SYMPTOM: "what is the final claim amount certified?" retrieved
RM50,569.30 (Dawson) and RM99,111.22 (Meridian), merged both into one evidence
pack, and appended a verifier note asking the reader to check the answer did
not mix projects. ROOT CAUSE: the verifier treated cross-project evidence as a
quality flag rather than as an unanswerable question. A single figure with a
caveat attached is a wrong-project answer (§60) wearing a disclaimer, and it
puts one client's numbers in front of a question about another (§2). FIX: a
new `AMBIGUOUS_PROJECT` verdict — the pipeline names the projects and the
evidence in each, and asks which one is meant, without calling the LLM and
without caching the clarification. An explicitly cross-project question
("across all projects", "semua projek"), detected by the router on the original
query text, still gets the merged answer it asked for. RESULT:
`test_multi_project_question_asks_instead_of_merging` (fails before the fix),
`test_explicit_cross_project_question_is_answered`,
`test_ambiguous_project_reply_is_not_cached`.
LESSON: when a question has two correct answers, returning one of them with a
warning is worse than returning neither. Ask.


**F-V3-24 / grounding / the relevance floor was waived by any sparse hit** —
SYMPTOM: the round-2 reviewer re-ran its round-1 attack, "What is the warranty
period for the pump?", against a corpus containing neither, and got rain-tree
and turf chunks back as supported evidence. ROOT CAUSE: two components
disagreed about what a word is. `verify()` treated `sources` containing
`sparse` as proof that a term had been matched, and `sparse._fts_query` built
an OR of EVERY token with no stopword list — so `"warranty" OR "period" OR
"for" OR "the" OR "pump"` matched documents whose only commonality was `the`,
and the verifier waived its own floor on the strength of it. FIX: one shared
stopword list and tokenizer (`terms.py`) used by both; the FTS expression drops
stopwords and returns no lexical hits at all for an all-stopword query; and the
verifier no longer accepts the retriever's label as evidence — only a
*verified* exact-code match (the query contains a document code and the
evidence carries that same normalized code) is a standalone pass, everything
else must meet the content-overlap floor. RESULT:
`test_relevance_floor_is_not_waived_by_a_sparse_hit` (asserts every leg
combination), `test_exact_leg_label_alone_does_not_pass_the_floor`,
`test_fts_query_drops_stopwords`; end-to-end the reviewer's query now returns
INSUFFICIENT.
LESSON: when two components must agree on a definition, give them ONE
definition. Testing each against its own private notion of a word is how both
pass while the system is wrong.

**F-V3-25 / honesty / PROJECT_STATE claimed tests that did not exist** —
SYMPTOM: the document stated "every finding is now fixed with a test that fails
before the fix". The reviewer reverted each fix individually; for the three
§60 project-isolation legs (F3a/b/c) the full suite stayed green. ROOT CAUSE:
the fixes were real, but the claim about their verification was written from
intent rather than from a revert run. This is §79 exactly — a claim from an
attempt — and the builder wrote it about the builder's own work, which is the
case §51 exists for. FIX: `tests/test_isolation_regressions.py` builds each
leak condition directly (the 5-file fixture cannot reach any of them), and the
revert matrix is now run per-fix before any such claim is written. The sentence
in PROJECT_STATE.md has been replaced with the correction rather than quietly
deleted.
LESSON: "I fixed it and the tests pass" and "the tests would have caught it"
are different claims. Only the second one needs the revert, and only the second
one was being made.


**F-V3-26 / grounding / three doors past the "fixed" relevance floor** —
SYMPTOM: the round-3 reviewer confirmed the round-1/2 attack query
("warranty period for the pump") was finally refused, then walked through
three doors it did not use. (a) `harvest_ids` treats `R01`, `D7`, `L2` as
document codes, and a verified code match was a STANDALONE floor pass, so
"what is the pump warranty period on Dawson drawing L-201?" was answered from
a tender spec that merely cross-refers to L-201. (b) The floor was per-SET,
not per-item: `best_overlap` was a max and the code check an `any(...)`, so one
qualifying chunk admitted every other chunk into the citations with full §40
provenance. (c) Two generic words cleared it — {locations, shown} and
{contractor, shall, supply} are vocabulary every construction document shares.
ROOT CAUSE: each is the same mistake at a smaller scale — treating a signal
that CO-OCCURS with relevance as if it WERE relevance. FIX: (a) a code must
look like a document identifier (`is_document_code`, excluding revision and
2–3 character tokens) AND appear in the evidence's FILENAME — a body mention is
a reference to a document, only the filename is its identity; (b) the floor
now FILTERS per item and drops what fails, reporting how many; (c) shared terms
are weighted by MEASURED document frequency from the index (`SparseIndex.
doc_freq`), falling back to a boilerplate list only when the corpus is too
small for a frequency to mean anything — and saying so in the flag when it
does. The project name and bare revision tokens are excluded from overlap
entirely: project scope is enforced by §60 isolation, and counting it as
topical evidence counted it twice. RESULT: eleven tests in
`test_reviewer_round3.py`; all six of the reviewer's attack queries return
INSUFFICIENT end-to-end while six legitimate queries still answer.
LESSON: the floor kept failing because every version measured "is this related
to the query?" with whatever was cheapest to compute. A term that co-occurs
with the answer is not the same as a term that identifies it, and the
difference only shows up in the queries you did not think to try.

**F-V3-27 / project isolation / consent to merge projects was a greedy regex**
— SYMPTOM: `CROSS_PROJECT_INTENT` contained `\bcompare\b.*\bprojects?\b`
and `\bany project\b`, so "compare the rain tree diameter with the turf spec
in this project" and "does any project document mention a defects liability
period?" both set `cross_project=True`. That single flag disables the
AMBIGUOUS_PROJECT guard, so Dawson's RM50,569.30 and Meridian's RM99,111.22
were merged into one evidence pack and labelled "as asked" — by a user who
asked nothing of the kind. ROOT CAUSE: a permission was inferred from loose
pattern matching. FIX: every pattern is now an anchored phrase a person can
only write deliberately; when in doubt the system asks, and that path already
exists. RESULT: `test_ordinary_questions_do_not_consent_to_cross_project_merging`
(4 queries), `test_explicit_cross_project_phrases_still_consent` (4 queries).
LESSON: consent inferred from a regex is not consent. A switch that disables a
top-severity guard should be as hard to trip accidentally as it is easy to
trip on purpose.

**F-V3-28 / §84 / the volatile-pattern escape hatch could excuse the corpus** —
SYMPTOM: with `volatile_patterns: ["*"]` the reviewer rewrote a source document
and deleted another, and `verify_snapshot` returned `pass: True`. `*.pdf` did
the same. ROOT CAUSE: the mechanism added in round 2 to stop live-service logs
failing the gate had no breadth limit, so it could excuse exactly what the gate
protects. FIX: declarations must be directory-anchored; bare `*` and bare
document-extension globs are refused outright (`VolatilePatternRejected`). The
verdict field now distinguishes `PASS` from `PASS_WITH_EXCUSES`, so a run
carried by an allowlist is not machine-readable as a clean one.
RESULT: `test_overbroad_volatile_patterns_are_refused` (6 patterns),
`test_excused_run_is_distinguishable_from_a_clean_one`.
LESSON: an escape hatch added to make a guard livable becomes the way around
the guard unless its breadth is bounded at the point of declaration.

**F-V3-29 / honesty / the audit cited an artifact it had not graded** —
SYMPTOM: with a recent FAST report (recall 1.0) and an older FULLSWING report
(recall 0.2), RETRIEVAL_QUALITY passed on the FAST numbers while naming the
FULLSWING file as its evidence. A reviewer opening the cited artifact would
find figures contradicting the verdict. ROOT CAUSE: introduced BY the round-2
N7 fix — the report was selected by recency, the path by "last of
fast/deep/fullswing present", and the two were never tied together. FIX: the
path and the report travel as one pair. RESULT:
`test_audit_cites_the_artifact_it_graded`.
LESSON: a fix aimed at evidence integrity broke evidence integrity in a new
way. Any change to how evidence is SELECTED needs a test that the verdict and
the citation refer to the same object.

**F-V3-30 / §60 / the project-label sync failed silently** —
SYMPTOM: injecting an I/O error into the FTS `UPDATE` left the operator with a
normal result dict (`sparse_rows_synced: 0`), no error and a zero exit — while
the project-scoped sparse leg was dead and the F3(c) isolation leak was back.
ROOT CAUSE: a bare `except Exception: return n` on an isolation-critical write.
FIX: raises `SyncError` naming the failure and what to re-run. A partial sync
is worse than a refused one, because the operator believes the labels are
correct. RESULT: `test_project_label_sync_failure_is_loud`.
LESSON: swallowing an exception converts a loud failure into a silent wrong
answer. On an isolation boundary that trade is never worth making.


**F-V3-31 / honesty / a decision built on a misread column** — SYMPTOM:
DECISIONS.md D-20 overrode an explicit reviewer instruction, justified by
"43,897 of ~45,000 inventoried files carry project=UNKNOWN". The round-4
reviewer showed that figure is the DOCUMENT_TYPE unknown count from
PROJECT_STATE.md. The document-type rows sum to 1,748 + 43,897 = 45,645, and
the project rows sum to exactly 45,645 too, so essentially every file carries a
project label and the true unattributed share is ≈0%. ROOT CAUSE: I read a
number off a table that supported the conclusion I already preferred, without
checking which column it came from — and then used it to decline an
instruction. FIX: D-20 withdrawn in place (not deleted); the strict rule is
implemented for every question, plus for sensitive questions even when no
project is known; PROJECT_STATE now flags the trap in the table itself; and
`alirag status` reports `project_unknown_pct` so the claim can be checked
against the index rather than a remembered figure. RESULT:
`test_unattributed_evidence_is_never_merged_with_a_named_project`,
`test_fully_attributed_evidence_still_answers_normally`,
`test_all_unknown_evidence_escalates_for_a_figure`.
LESSON: the number that lets you keep your preferred design is the one to check
twice. §51 exists because the builder is the last person who will.

**F-V3-32 / grounding / measurement was applied as a REPLACEMENT, not a floor**
— SYMPTOM: the round-3 fix weighted shared terms by document frequency instead
of by a word list. The round-4 reviewer showed that above MIN_DOCS_FOR_DF the
list became dead code, so any boilerplate word whose measured frequency fell
under MAX_DF_RATIO was RESTORED as discriminating — and two are enough. On an
800-chunk index, "which contractor shall supply the pump and the generator?"
came back SUPPORTED (not PARTIAL) from a rain-tree chunk. The production index
is 16,782 chunks, so the measured path is always live and the list never
applied where it was needed. ROOT CAUSE: an upgrade from a weak instrument to a
better one was implemented as a swap, making the guard weaker in exactly the
regime it was strengthened for. FIX: the boilerplate list always applies;
measurement may only remove further terms, never add them back. RESULT:
`test_measurement_can_only_make_the_floor_stricter`,
`test_boilerplate_is_refused_at_scale_even_when_rare`, and both attack queries
re-tested against a 260-document fixture.
LESSON: when replacing a guard with a better one, union them. A "better"
instrument that is weaker on some inputs is not better.

**F-V3-33 / test coverage / no test ever ran the production instrument** —
SYMPTOM: replacing `Engine._term_stats` with `return None, 0` — the engine
never computing or passing document frequencies at all — left 179/179 tests
green. ROOT CAUSE: the fixture corpus is 6 chunks and MIN_DOCS_FOR_DF is 200,
so the measured path could not execute in any test; only two unit tests fed
hand-written dicts to the scoring function. The suite was calibrated entirely
against the fallback list while production ran on the other branch. My commit
claimed "all 17 fixes were individually reverted and their tests observed to
fail", which was false for this one. FIX: a 260-document fixture
(`big_ingested`) above the threshold, an end-to-end test that goes red when the
plumbing is disabled, and a guard test asserting the fixture is still large
enough — so this cannot rot back silently. RESULT:
`test_the_big_fixture_actually_exercises_measured_weighting`,
`test_measured_weighting_is_wired_end_to_end`,
`test_attack_queries_refused_at_scale`.
LESSON: a threshold that no test can cross means the code above it is
unexecuted, not merely untested. Check that fixtures can reach every branch
before claiming a revert matrix is complete.

**F-V3-34 / §84 / the allowlist check was written to the examples, not the
class** — SYMPTOM: R3-7 refused bare `*` and bare document-extension globs —
the two strings the round-3 reviewer had cited. `*/Dawson/*`, `*/Tender/*` and
`*/sources/*` were all accepted, and with one of them a tender was rewritten
and an instruction deleted while §84 reported `pass: True`. Separately,
`reviewer.audit` gated on `pass` and ignored `verdict`, so PASS_WITH_EXCUSES was
decorative at the only place that consumed it. ROOT CAUSE: a blocklist has to
anticipate every way of naming the corpus. FIX: inverted to a positive rule — a
volatile declaration must name a directory the operator has ALREADY excluded
from indexing (`ingest.exclude_dirs`), which cannot be widened without a
separate, visible declaration that the directory holds nothing worth indexing.
The audit now requires `verdict == "PASS"`. RESULT:
`test_volatile_patterns_naming_the_corpus_are_refused` (5 patterns),
`test_audit_refuses_to_certify_an_excused_safety_run`.
LESSON: fixing the reviewer's example is not fixing the finding. Ask what class
the example belongs to, and prefer a rule that enumerates what is ALLOWED.

**F-V3-35 / grounding / three smaller repeats of the same mistake** —
(a) `normalize_id("L-201-RevB")` is one greedy token, so `find L-201` returned
INSUFFICIENT while the file sat in the nearest-match list — and
revision-suffixed and prefix-qualified sheet names are the norm in this corpus,
so the §9 exact path was broken for most real drawings. Codes now match as
COMPONENTS. (b) A filename code match bypassed the content floor for every
chunk of that file, unflagged: a scaffolding invoice inside `L-201.pdf` was
returned SUPPORTED as evidence for a pump warranty. Naming a document now
justifies returning it, flagged, and can never read as SUPPORTED. (c) The
project label was subtracted term by term, so for project "Pump Warranty
Programme" the question "what is the pump warranty period" lost both content
words and a chunk stating the answer verbatim was refused; the label is now
removed as a phrase. RESULT: `test_document_codes_match_inside_longer_filenames`,
`test_naming_a_revision_suffixed_document_answers`,
`test_naming_a_file_does_not_certify_arbitrary_content`,
`test_project_label_is_excluded_as_a_phrase_not_term_by_term`.
LESSON: (a) and (c) are the same bug in opposite directions — a rule that was
too strict where it should have been structural. Tightening a guard can lose
real evidence just as silently as loosening it lets fabrication through.

**F-V3-36 / benchmark / the wrong-project metric excused its own population** —
SYMPTOM: `wrong_project_rate` treated `project in (expected, "UNKNOWN", None)`
as not-wrong, so unattributed sources scored as CORRECT and an all-UNKNOWN
corpus reported a perfect 0.0 — satisfying the reviewer's `< 0.2` gate without
measuring §60 at all. FIX: unattributed sources are excluded from the
denominator and counted separately; the rate is `None` when nothing was
measurable, and `None` cannot satisfy the gate. RESULT:
`test_wrong_project_rate_excludes_unattributed_from_the_denominator`.
LESSON: a metric that scores "unknown" as "correct" reports best when it knows
least.


**F-V3-37 / test coverage / the fix for "untested wiring" was itself untested
wiring** — SYMPTOM: round 4 closed N4-8 (no test executed the measured
document-frequency path) with a 260-document fixture and three tests named for
end-to-end coverage. The round-5 reviewer cut the actual call in
`Engine.query` — `df, ndocs = None, 0` — and all 205 tests stayed green. ROOT
CAUSE: all three tests called `Engine._term_stats()` directly, so they bit only
when the HELPER was broken, never when the WIRING was. I recreated the exact
defect I was closing, one layer up, and then asserted the matrix was complete
for the third consecutive round. FIX: a behavioural test — "girth" appears in
every document of the fixture and is absent from the hand-written boilerplate
list, so it can only be discarded by measurement; with the wiring cut the query
is answered, with it intact the floor refuses. And, because assertion has
failed three times, `v3/tools/revert_matrix.py` now applies every mutation,
runs the suite, and reports which fixes nothing would notice breaking. It exits
non-zero if any survive. RESULT:
`test_document_frequencies_actually_reach_the_verifier`, plus the matrix
artifact.
LESSON: a test that calls the helper proves the helper works. Only a test that
asserts a BEHAVIOUR proves the helper is being used. And a claim about test
coverage that cannot be re-run is not evidence — it is the same
claim-from-attempt §79 forbids, made about testing instead of about code.

**F-V3-38 / grounding / the floor depended on the word order of the question** —
SYMPTOM: "In the Dawson Meridian Towers, what is the final claim amount?" was
correctly refused; "what is the final claim amount for Meridian Towers Dawson?"
returned SUPPORTED, citing a drawing title block whose entire content was
"DAWSON MERIDIAN TOWERS / Drawing title block. Sheet 12 of 40. Scale 1:100."
ROOT CAUSE: two independently conservative mechanisms composed into a hole.
`_project_hint` claims a project only on a VERBATIM match, and the phrase-strip
in `verify()` ran only when a hint was set — so reordering the name left the
hint None, nothing stripped, and the project name counted as discriminating
evidence. Document frequency did not help: project names are rare corpus-wide.
FIX: terms belonging to ANY known project can never be the sole grounds for
admitting a chunk, whatever the hint or the word order. They are not subtracted
outright — that was N4-5, which deleted real evidence — they simply cannot
stand alone. RESULT: `test_project_name_alone_is_never_evidence_whatever_the_
word_order` (4 phrasings), `test_a_real_answer_is_not_lost_when_it_shares_the_
project_name`.
LESSON: two conservative mechanisms can compose into a permissive one when each
assumes the other fired. "Only when a hint is set" is a coupling, not a guard.

**F-V3-39 / §84 / my own tightening made the safety gate unsatisfiable** —
SYMPTOM: round 4 made `reviewer.audit` require `verdict == "PASS"`. The shipped
`config.kazasline.yaml` declares `*/hermes/*` and `*/sci_ai_library/*` volatile
because those services rewrite state continuously, and the safety walk covered
them — so every run produced PASS_WITH_EXCUSES and the audit refused it, while
removing the declarations produced FAIL. DATA_SAFETY could not be earned on the
shipped configuration by any means. ROOT CAUSE: I bounded the escape hatch
without noticing the legitimate case still had to pass through it. The config's
own comment — "a gate that can never legitimately go green is a gate the
operator learns to ignore" — had come to describe the gate I built. FIX
upstream: `_walk_sources()` honours `excluded_dirs`, so a directory declared
non-knowledge is out of the diff entirely and a genuinely clean PASS is
reachable with no excuse in force. One declaration in config now carries both
consequences, visibly. RESULT:
`test_shipped_config_can_produce_a_clean_safety_pass`,
`test_a_document_change_still_fails_with_exclusions_in_force`.
LESSON: when tightening a gate, check that the legitimate path still reaches
the other side. An unsatisfiable guard is not a strict guard; it is a guard
about to be disabled.

**F-V3-40 / §60 / escalation keyed on vocabulary, twice** — SYMPTOM: the same
unattributable payment chunk escalated for "what is the final claim amount" and
merely disclosed for "how much was billed", "what is the unpaid portion", and
seven other ordinary phrasings. ROOT CAUSE: the trigger was a keyword regex
over the QUERY, widened twice against the examples quoted at me while the class
stayed open. The verifier already knew the evidence was monetary — MONEY_RE
fires on it to build the conflict list. FIX: the trigger is now a property of
the EVIDENCE (a money or date match), which no rephrasing can evade; the query
regex remains only as an additional trigger for evidence carrying no figure.
RESULT: `test_escalation_keys_on_the_evidence_not_the_phrasing` (6 phrasings),
`test_non_monetary_unattributed_evidence_is_not_over_escalated`.
LESSON: when a guard can be evaded by rewording, it is keyed on the wrong
thing. Key on the material, not on how the question was asked.

**F-V3-41 / §4 / re-inference forged provenance** — SYMPTOM:
`reinfer_metadata`'s UPDATE set `project` but not `project_source`, so a
manually confirmed `content` attribution survived a re-infer that had just
replaced the project with a folder guess. The citation then asserted the guess
had been confirmed from document content. `reinfer` is the documented remedy
path for F-V3-04 and will be run. FIX: `project_source` moves with `project`.
RESULT: `test_reinference_does_not_forge_project_provenance`.
LESSON: §4 was violated by the very column added to satisfy §4. A provenance
field that is not updated everywhere its subject is updated is worse than none,
because it is believed.

**F-V3-42 / §9 / the exact-ID index was never given the fix** — SYMPTOM:
`find L-201` still could not reach `L-201-RevB.pdf` through the exact leg.
ROOT CAUSE: round 4 added `code_variants()` to the VERIFIER only; the ids table
still stored the greedy `L201REVB` and `search_ids` looked up `L201`. FAST was
left hoping BM25 ranked the right sheet into the top 20 across 662k files —
precisely what §9 exists to avoid. The certifying test passed for the wrong
reason: a one-document corpus where BM25 cannot miss. FIX: variants are indexed
at write time. RESULT: `test_exact_id_leg_finds_a_code_inside_a_longer_
filename`, `test_named_document_is_answered_via_the_exact_leg_at_scale`
(asserts `retrievers` contains `exact`, on a 260-document corpus).
LESSON: fixing the consumer is not fixing the producer. And a test at a scale
where every path succeeds certifies nothing about which path ran.


**F-V3-43 / the revert matrix immediately found two things I had missed** —
the first full run of `v3/tools/revert_matrix.py` reported 36 fixes guarded and
two not:

* `F5-4` — the "no excluded directories configured" refusal, which is the only
  thing protecting the three `SafetyGuard` constructions that do not pass
  `excluded_dirs=`. Disabling it changed no test, because the pattern check
  raises from a LATER branch too and my test matched the word "excluded" in
  either message. The messages are now distinguishable and the test asserts the
  specific one.
* `N4-3b` — a per-code `parts[:8]` window bound. Investigating why no test
  could catch it showed the line was UNREACHABLE: `ID_RE` permits at most six
  separator repetitions, so a harvested code has at most seven parts and the
  slice never fires. It was not an unguarded fix but dead code wearing the
  costume of one. Removed, and replaced with `MAX_CODE_VARIANTS`, a total cap
  that does real work — every variant becomes a row in the ids table at index
  time across 662k files — with a test that exercises it.

It also flagged two mutation definitions as STALE because I had edited the
code after writing them, which is the failure mode a hand-maintained matrix
has and is exactly why it prints that as a distinct result rather than
counting it as a pass.

LESSON: the artifact earned its place on its first run. Two rounds of my own
assertions had already covered this ground and missed both. "I checked" is a
claim; a script anyone can re-run is evidence — and the difference is not
diligence, it is that one of them can be wrong without anybody noticing.


**F-V3-44 / §84 / my own round-5 fix broke the guarantee it was protecting** —
SYMPTOM: the round-6 reviewer modified, deleted and renamed source documents
and `safety verify` reported `pass: True`, `verdict: PASS`, `modified: []`, and
`reviewer.audit` certified DATA_SAFETY. ROOT CAUSE: F5-3 made `_walk_sources()`
SKIP `ingest.exclude_dirs`. Excluded directories were not excused — they were
never examined. The shipped list contains generic names (`models`, `build`,
`dist`, `checkpoints`) and `_is_excluded_dir` matches ANY path component, so
`E:\Projects\Dawson\Models\` was outside §84 with no operator mistake
required; and excluding a directory became sufficient on its own to make its
contents unwatchable, routing around both N4-6 (patterns must name an excluded
dir) and N4-6c (the audit refuses PASS_WITH_EXCUSES) at once. The code comment
justifying it — "these directories hold no documents (that is what excluding
them from indexing asserts)" — is a non-sequitur: exclusion from indexing
asserts NOT WORTH INDEXING, while §1 protects the user's FILES. Those two sets
were identical before that change and were not after.
FIX: the walk covers every file again. Changes inside excluded directories are
CLASSIFIED — `excluded_dir_modified` / `excluded_dir_deleted`, reported,
counted, surfaced in the audit detail — rather than omitted, and do not fail
`pass`. A DOCUMENT changing inside an excluded directory DOES fail, because
that means the exclusion list overlaps the corpus. The report `note` said
"'pass' fails on ANY change to a source file", which had become false; it now
describes what the check actually does.
ALSO REMOVED: the per-path volatile allowlist, entirely. It was bounded in
round 2, re-bounded in round 4 after accepting `*` and then `*/Dawson/*`, and
in round 5 its own guard was found masked by a later branch. With full walk
coverage and excluded-dir classification it had no remaining purpose except as
a bypass surface. A mechanism whose only effect is to weaken a guarantee is
better deleted than bounded.
RESULT: `test_a_document_inside_an_excluded_dir_fails_the_verdict`,
`test_a_document_deleted_inside_an_excluded_dir_fails`,
`test_excluded_dir_match_is_by_component_not_leaf`,
`test_excluded_live_service_dirs_are_classified_not_skipped`, and the whole
`test_the_volatile_allowlist_is_gone` family.
LESSON: I made an unsatisfiable gate satisfiable by making it stop looking, and
wrote a justification for it that sounded structural. The reviewer's sentence
is the one to keep: "A gate that passes because it stopped looking is worse
than one that cannot pass, because the operator believes it."

**F-V3-45 / grounding / a title block answered a money question** — SYMPTOM:
evidence reading only "SKYPARK TOWERS — PODIUM LANDSCAPE GA — SHEET 3 OF 40 —
SCALE 1:200" was returned SUPPORTED, unflagged, for "what is the final claim
amount for the Skypark Towers podium?" ROOT CAUSE: F5-1 subtracted terms
belonging to MANIFEST PROJECT LABELS, and a title block is full of words that
are not labels — the development name, the drawing title, the client, the
consultant — so two of them cleared the floor. Round 5 had stated F5-1 as "a
title block returned SUPPORTED for a money question"; the word-order dependency
was genuinely fixed and the headline symptom was not. FIX: a question that asks
for a figure or a date cannot be answered from evidence containing neither —
keyed on the material, the same move as F5-5, using machinery that already
existed and was wired only to the unattributed path. Deliberately narrowed to
QUANTITATIVE_INTENT after the first version refused "what is the approval
status?", whose answer is legitimately a word: a guard that refuses correct
answers gets removed.
RESULT: `test_a_figure_question_is_not_answered_by_a_title_block` (6 cases),
`test_a_figure_question_is_answered_when_the_figure_is_there`,
`test_the_query_side_trigger_still_works_on_figureless_evidence` (pins the
boundary).
LESSON: fourth consecutive round in which the quoted example closed and the
class did not. The example is an illustration; the finding is the class.

**F-V3-46 / retrieval / my F5-2 fix put bare numbers in the exact-ID index** —
SYMPTOM: "A-1234 Planting Schedule.pdf" indexed the bare number `1234`, so
"what does grid 12-34 show?" scored an exact hit on it at RRF weight 2.0 — the
heaviest in the system — putting an unrelated document at the top of the fused
list, which without a project hint is a §60 wrong-project route. The same rule
also wrote pure words (`LANDSCAPE`, `CSLANDSCAPE`): 12 id rows per chunk that
no query can reach, since `harvest_ids` requires a digit. FIX: a window must
carry both a letter and a digit. Also R6-3b: `_query_doc_codes` now expands
through `code_variants` too — F5-2 had fixed only the index side, so pasting a
full sheet number from an email missed the short filename on disk while the
reverse direction worked.
RESULT: `test_bare_numbers_are_not_document_identifiers`,
`test_query_side_codes_are_expanded_to_variants`.
LESSON: a fix that broadens an index broadens what can match wrongly. I checked
that the intended lookups started working and not that the unintended ones
stayed broken.

**F-V3-47 / test coverage / the matrix I submitted overstated its own result** —
SYMPTOM: I reported "40 of 40 mutations caught" as evidence the fixes were
guarded. The reviewer wrote 15 mutations of its own against the newest code and
6 came back GREEN, including the dense leg's project scoping, the second-pass
`known_projects` argument, and the `DATE_RE` half of F5-5 — whose mutation
disabled three mechanisms at once and reported a single RED. When I then added
the reviewer's mutations plus ones for my own round-6 fixes, **10 of 14 came
back GREEN**, including four fixes I had written earlier in the same round with
no tests behind them at all. ROOT CAUSE: the mutation SET was drawn from what
previous reviewers had named. A self-authored test of my own claims inherits my
blind spots by construction. FIX: the reviewer's seven mutations added, F5-5
split into four (query regex / money / date / quantity), and the rule ONE
MUTATION, ONE MECHANISM written into the script's docstring so the next round
can be checked against it.
LESSON: the artifact was still a self-report. It made my claim checkable, which
is real progress, but "my checker found nothing" and "there is nothing" are
different statements, and only an adversary can close the gap. The exit code
means "no mutation I thought of survived".
