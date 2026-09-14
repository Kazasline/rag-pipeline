# SKILL: rag-benchmark

## 0. SKOP & RULE
All performance/quality numbers come from `bench run` on the target machine.
No number may be quoted from memory, another machine, or a draft question
set — the harness enforces this; do not bypass it (§79).

## 1. GOAL
Recall@5/10, MRR, citation accuracy, wrong-project rate, p50/p95/p99 — plus
§86 proof that each retrieval layer earns its place.

## 2. PREFERENCE
Small validated set (≥20 questions across all §43 kinds) beats a large
unreviewed one. Warm the service; report cold separately (§72).

## 3. REFERENCE END FORMAT
`15_BENCHMARK/report_<label>_<ts>.json` (machine-stamped) + a row appended to
`docs/BENCHMARK.md`.

## 4. CHILD SKILL MAP
rag-review consumes these reports.

## 5. TOOLS
`alirag bench make` → human review → `alirag bench run --label <mode>
[--llm]` · `alirag bench compare`

## 6. PAST MISTAKE
None recorded yet on-machine. Known trap by design: scoring against
REVIEW-ME placeholders (harness refuses; leave it that way).

## 7. SUCCESSFUL EXECUTE
CI (synthetic corpus): R@5 = 1.0, MRR = 1.0 on a 3-question validated set —
proves the harness, NOT the corpus. First real result: pending.

## 8. DETAIL STEP
1. `bench make` → edit every line of the draft; delete nonsense; save as
   `questions.jsonl`.
2. Include traps: wrong-project, duplicate-file, superseded-revision.
3. `bench run --label fast`, `--label deep --llm`, `--label fullswing --llm`.
4. `bench compare` → if a layer adds no recall/MRR, raise it in DECISIONS.md
   for removal (§89).
5. Append results row to `docs/BENCHMARK.md` with report path.

## 9. REVIEWER SKILL
Reviewer re-runs `bench run` from a fresh shell and diffs the two reports;
inspects 5 per-question rows against reality.

## 10. REKOD LARIAN
See `docs/BENCHMARK.md` result log (single source of truth).
