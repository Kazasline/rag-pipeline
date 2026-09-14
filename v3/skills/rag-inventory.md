# SKILL: rag-inventory

## 0. SKOP & RULE
Read-only inventory of source roots into the manifest. NEVER writes, moves,
renames or deletes a source file — `SafetyGuard` enforces it; snapshot/verify
proves it per run.

## 1. GOAL
Every file hashed, classified, metadata-inferred (UNKNOWN when unsure),
duplicates tagged, revision families linked; organization report produced.

## 2. PREFERENCE
Run snapshot → inventory → verify as one sequence. Incremental re-runs are
cheap (unchanged path+size+mtime skips hashing).

## 3. REFERENCE END FORMAT
`17_REPORTS/inventory_<ts>.json` (counts + organization) and
`safety_verify_<ts>.json` with `"pass": true`.

## 4. CHILD SKILL MAP
None.

## 5. TOOLS
`alirag safety snapshot` · `alirag inventory [--max-files N]` ·
`alirag safety verify`

## 6. PAST MISTAKE
V1: enumerating a cloud-synced drive is slow and can false-trigger stall
watchdogs — heartbeat prints every 2000 files exist for this reason.

## 7. SUCCESSFUL EXECUTE
2026-08-16 (synthetic corpus, CI): 3 files → 3 CLASSIFIED, verify pass:true,
0 modified/deleted. First E:\ run: pending.

## 8. DETAIL STEP
1. `alirag safety snapshot`
2. `alirag inventory --max-files 5000` (pilot) → review organization report
3. Full `alirag inventory`
4. `alirag safety verify` → must pass; file the report
5. Check `unknown_project` and `unsupported` counts before ingestion

## 9. REVIEWER SKILL
Reviewer re-runs verify independently and spot-checks 10 random manifest
rows against the real files (path exists, hash reproducible).

## 10. REKOD LARIAN
| date | files | new/unchanged | duplicates | verify | report |
|---|---|---|---|---|---|
