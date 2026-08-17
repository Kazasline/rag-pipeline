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
