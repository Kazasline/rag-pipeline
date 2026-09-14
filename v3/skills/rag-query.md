# SKILL: rag-query

## 0. SKOP & RULE
Operating FAST/DEEP/FULLSWING queries. Corpus evidence outranks model memory
(§39). Explicit user mode always wins (§8). Never dump internals into user
answers unless asked (§41).

## 1. GOAL
Right mode, right evidence, correct citation (file + page/sheet/location),
honest INSUFFICIENT when the corpus lacks the answer.

## 2. PREFERENCE
Let the router decide; use `--mode` only to debug routing. Keep the serve
process warm — cold first query is expected to be slower (§72).

## 3. REFERENCE END FORMAT
§54 response object; sources per §40 (file / location / revision / project).

## 4. CHILD SKILL MAP
rag-benchmark (when answers look wrong at scale).

## 5. TOOLS
`alirag query "<text>" [--mode M] [--no-llm]` · `POST /query` · `POST
/explain` (full trace) · `alirag metrics --mode FAST`

## 6. PAST MISTAKE
F-V3-02: trusting retrieval count as sufficiency — nearest neighbors are not
evidence. The relevance floor now guards this; do not weaken it to make
demos look better.

## 7. SUCCESSFUL EXECUTE
2026-08-16 CI: "cepat: find LAI-003" → FAST, exact+sparse+dense agreement,
correct file cited, money-conflict + cross-project flags raised correctly.

## 8. DETAIL STEP (triage when an answer looks wrong)
1. `POST /explain` with the same text → check `route_reason`, `sources`,
   per-stage timings, `verifier_flags`.
2. Wrong mode → fix trigger lists in `router.py` (add the phrase, add a test).
3. Right mode, wrong source → check exact-ID harvest, then sparse vs dense
   legs separately (`--no-llm` shows raw evidence).
4. Right source, wrong page → inspect that file's chunks/locators in manifest.
5. Any fix → add the case to the benchmark question set.

## 9. REVIEWER SKILL
Sample 10 answers; verify each citation opens to a page/sheet that actually
supports the sentence citing it (§45).

## 10. REKOD LARIAN
| date | query | mode | correct? | note |
|---|---|---|---|---|
