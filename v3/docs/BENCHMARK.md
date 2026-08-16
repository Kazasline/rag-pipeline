# BENCHMARK — methodology and result log

**No results exist yet. This file contains methodology only; every number
that ever lands here must carry the machine stamp + report path emitted by
`alirag bench run` (§79). Fabricated or extrapolated figures are forbidden.**

## Question set (§43)

`alirag bench make` drafts `15_BENCHMARK/questions.draft.jsonl` from real
indexed files (locally — nothing leaves the machine). A human rewrites each
`REVIEW-ME` placeholder into a genuine question and saves as
`questions.jsonl`. The harness refuses drafts and empty sets by design.

Cover all kinds: exact lookup, semantic, cross-document, revision comparison,
multi-hop, visual, table, quantity, timeline, contradiction, project
identification, wrong-project trap, duplicate-file trap.

## Metrics (§44–§45)

Retrieval: Recall@5, Recall@10, MRR, citation page accuracy,
wrong-project-in-top3 rate. Latency: p50/p95/p99 end-to-end plus per-stage
percentiles from `alirag metrics` (embedding, sparse, dense, graph, fusion,
prompt build, TTFT, generation, tokens/sec). Cold vs warm reported separately
(§72): first query after service start is cold; discard it from warm stats
but record it.

## Targets (§10 — objectives to measure against, not results)

FAST retrieval p95 ≤ 250 ms · FAST TTFT p50 < 1 s, p95 ≤ 2 s (after warm-up)
· thresholds for recall/MRR to be fixed from the first real run + reviewer
judgment (§44).

## Layer comparison (§86)

`alirag bench compare` runs the same questions as: dense-only baseline →
hybrid (exact+sparse+dense) → hybrid+graph. A layer that doesn't move
recall/MRR (or hurts latency beyond its gain) gets removed, per §89.

## Result log

| date | machine | label | questions | R@5 | R@10 | MRR | wrong-proj | p50 | p95 | report |
|---|---|---|---|---|---|---|---|---|---|---|
| _none yet — first entry must come from an on-machine run_ | | | | | | | | | | |
