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
| _pending_ | round 4 | — | — | round-3 fixes in; awaiting re-audit. **No sign-off has been issued.** |

Neither audit was run on the target machine — both were code-and-artifact
audits in a separate agent session. The on-machine acceptance run (§84–§86
evidence artifacts) is still outstanding, and no category can pass without it.
