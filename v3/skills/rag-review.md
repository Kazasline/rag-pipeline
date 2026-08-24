# SKILL: rag-review

## 0. SKOP & RULE
Independent review. The reviewer must run in a fresh session/agent, on the
target machine, and verify EVIDENCE, not builder claims (§47/§51). Veto
authority is real: a FAIL blocks "done".

## 1. GOAL
Countersigned verdict per §87 category with artifacts attached.

## 2. PREFERENCE
Re-run producing commands yourself; never accept a pasted result. Challenge
every component per §49 (necessity, latency, VRAM, corruption risk,
cross-project leakage, traceability).

## 3. REFERENCE END FORMAT
`17_REPORTS/reviewer_audit.md` + verdict row in `docs/REVIEWER.md`.

## 4. CHILD SKILL MAP
rag-inventory §9, rag-query §9, rag-benchmark §9 (per-skill reviewer steps).

## 5. TOOLS
`alirag review` · every producing command in `docs/REVIEWER.md`'s table ·
current official docs for Qwen serving / Qdrant / visual retrieval (§48).

## 6. PAST MISTAKE
None yet. Structural guard: audit cannot emit PASS without an artifact.

## 7. SUCCESSFUL EXECUTE
CI: audit correctly returned PENDING overall with only DATA_SAFETY and
RESTART passing (the two with real artifacts) — the gate works.

## 8. DETAIL STEP
1. Fresh session on the target machine; `git pull`; read PROJECT_STATE.md.
2. Re-run: inspect, safety verify, restart-test, bench run/compare.
3. `alirag review` → inspect every PENDING/FAIL.
4. Record failures in docs/FAILURES.md per §50 format; hand back to builder.
5. When all categories PASS with evidence → countersign docs/REVIEWER.md.

## 9. REVIEWER SKILL
(self)

## 10. REKOD LARIAN
See verdict table in `docs/REVIEWER.md`.
