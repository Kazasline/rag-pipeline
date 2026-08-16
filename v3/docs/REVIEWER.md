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
| _none yet — no on-machine review has occurred_ | | | | |
