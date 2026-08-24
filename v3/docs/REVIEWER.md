# REVIEWER — role, workflow, veto (§47–§51, §87)

The Builder cannot self-certify. Completion requires an independent Reviewer
who inspects **evidence**, not explanations, and holds veto authority.

## How independence is achieved

* The Reviewer runs in a **separate session/agent** (fresh context, no stake
  in the Builder's narrative), on the target machine.
* `alirag review` mechanically audits the workspace for evidence artifacts
  and generates `17_REPORTS/reviewer_audit.md`. A category without a real
  artifact can only be PENDING/FAIL — PASS is unrepresentable without proof.
* The Reviewer countersigns (or vetoes) the generated report and records the
  verdict here.

## Reviewer checklist (§87 + §48–§49)

Per category: verify the artifact exists, re-run the producing command
yourself, and challenge necessity (§49: does the component earn its
latency/VRAM? can it corrupt data? can it leak another project's info?).

| Category | Evidence artifact | Producing command |
|---|---|---|
| ARCHITECTURE | `phase0_machine*.json` (on target machine) | `alirag inspect` |
| DATA_SAFETY | `safety_verify*.json` pass:true | `alirag safety snapshot` / `verify` |
| FAST/DEEP/FULLSWING | `report_<mode>*.json` | `alirag bench run --label <mode>` |
| MULTIMODAL | `multimodal_eval*.json` (§83 manual test) | manual + recorded |
| GRAPH | `layer_compare*.json` | `alirag bench compare` |
| CITATIONS | citation accuracy in any bench report | `alirag bench run` |
| RESTART | `restart_test*.json` pass:true | `alirag restart-test` |
| LATENCY | FAST report percentiles | `alirag bench run --label fast` |
| RETRIEVAL_QUALITY | recall/MRR vs agreed thresholds | `alirag bench run` |

Before approving, re-check current official docs for: Qwen local serving,
vLLM/SGLang/llama.cpp Windows support, Qdrant, ColPali-class visual
retrieval, embedding/reranker state of the art (§48/§97). Newer alone is not
sufficient — demand measurements (§97).

## FAIL protocol (§50)

Every FAIL records: failure, evidence, root cause, required correction,
retest result — in `docs/FAILURES.md`. Builder fixes, Reviewer re-runs.

## Verdicts

| date | reviewer | scope | verdict | notes |
|---|---|---|---|---|
| 2026-08-17 | round 1 (independent agent session) | **FAIL** | DATA_SAFETY, GROUNDING, TEST_COVERAGE | safety verification was unfalsifiable; relevance floor accepted any shared token; three §60 isolation leaks; benchmark gates bypassable |
| 2026-08-17 | round 2 (independent agent session) | **FAIL** | RETRIEVAL_QUALITY, GROUNDING/CITATIONS, TEST_COVERAGE, DOCUMENTATION_HONESTY | relevance floor still bypassable via the sparse leg; UNKNOWN-project evidence merged silently; second-pass evidence unverified; benchmark artifacts selectable by filename; PROJECT_STATE claimed tests that did not exist |
| 2026-08-17 | round 3 (independent agent session) | **FAIL** | RETRIEVAL_QUALITY, GROUNDING/CITATIONS | 19 of 22 reverts confirmed red; §1 independently re-verified clean; DOCUMENTATION_HONESTY and ARCHITECTURE passed; DATA_SAFETY and TEST_COVERAGE passed with conditions. Failed on three new routes past the relevance floor, cross-project consent inferred from a greedy regex, an unbounded volatile-pattern allowlist, and an audit citing an artifact it had not graded. Ruled on and ACCEPTED the builder's D-20 deviation, with three conditions (all now implemented). |
| 2026-08-17 | round 4 (independent agent session) | **FAIL** | DATA_SAFETY, RETRIEVAL_QUALITY, GROUNDING/CITATIONS, TEST_COVERAGE, DOCUMENTATION_HONESTY | Three categories REGRESSED. Two round-3 fixes had been written to the reviewer's literal examples rather than the underlying class: measured DF replaced the boilerplate list instead of adding to it (a loosening at production scale), and the volatile-pattern check still accepted a pattern naming the corpus. Also found the D-20 justification was a misread column, and that no test ever executed the measured-DF path. §1 re-verified clean for the third time. |
| 2026-08-17 | round 5 (independent agent session) | **FAIL** | DATA_SAFETY, RETRIEVAL_QUALITY, GROUNDING/CITATIONS, TEST_COVERAGE, DOCUMENTATION_HONESTY | §1 verified clean for the FOURTH time, including a live breach test (same-size edit with mtime restored, a delete, a rename — all caught). Found the round-4 N4-8 fix did not close N4-8: the tests called the helper directly, so cutting the wiring left 205 green. Also: the floor depended on the word order of the question; the exact-ID index never received the code-variant fix; the §84 gate was unsatisfiable on the shipped config; escalation still keyed on query vocabulary; re-inference forged provenance. Imposed a procedural condition — submit the revert matrix as a re-runnable artifact, and stop fixing the quoted examples instead of the class. |
| 2026-08-17 | round 6 (independent agent session) | **FAIL** | DATA_SAFETY, RETRIEVAL_QUALITY, GROUNDING/CITATIONS, TEST_COVERAGE | Reproduced the revert matrix independently (40/40, exit 0) and credited it as the first artifact in six rounds whose claim could be reproduced rather than re-derived — then wrote 15 mutations of its own, of which 6 came back GREEN. Found the round-5 F5-3 fix had made §84 report PASS while source documents were modified, deleted and renamed. Title-block-answers-money survived F5-1. F5-2 introduced bare-number exact hits. Ruled that no category can be signed off without on-machine evidence, and that the honest ceiling short of it is "no unresolved defects found", not PASS. |
| 2026-08-17 | round 7 (independent agent session) | **FAIL** | DATA_SAFETY, GROUNDING/CITATIONS, RETRIEVAL_QUALITY | Reproduced the matrix (53/53) and found no new instance of the masking habit — recorded as real progress. Then wrote 25 mutations of its own, 8 GREEN. Found the R6-2 test fixtures were not title blocks (no dates), so a dated title block still answered a money question; the excluded-dir escape hatch was gated on an allowlist of document extensions that omits .skp/.rvt/.xlsm under the shipped exclusions; and R6-3b was fixed in the verifier but not in retrieval. Ruled that a clean round 8 would establish only "the code review has converged". |
| _pending_ | round 8 | — | — | round-7 fixes in. **No sign-off has been issued.** |

Neither audit was run on the target machine — both were code-and-artifact
audits in a separate agent session. The on-machine acceptance run (§84–§86
evidence artifacts) is still outstanding, and no category can pass without it.
