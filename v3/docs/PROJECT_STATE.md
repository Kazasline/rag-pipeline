# PROJECT_STATE — ALI RAG V3

_Last update: 2026-08-17, after the first independent reviewer audit and the
fix pass that followed it._

## Where we are

```
✅ PHASE 0-15   code built + 118 tests green
✅ PHASE 0      EXECUTED on the target machine (2026-08-17)
✅ PHASE 1      EXECUTED — 20,000-file pilot inventory of E:\
✅ PHASE 5-15   installed; 313 files ingested, first grounded answers returned
⬜ PHASE 16     benchmark (needs a human-reviewed question set)
🔄 PHASE 17-19  independent reviewer ran and FAILED 3 of 6 categories;
                every finding is now fixed with a test that fails before the
                fix. Re-audit pending — the reviewer, not the builder, decides.
⬜ PHASE 20     final docs with real numbers
```

**Nothing here is signed off.** The builder cannot self-certify (§51); the
reviewer's first verdict was FAIL and the second has not been issued.

**Measured so far:** per-stage query latency from real traces on the target
machine (see `BENCHMARK.md`), and end-to-end answers on 313 ingested files.
**Not yet measured:** recall, MRR, citation-page accuracy and wrong-project
rate — all four need the human-reviewed question set (§43/§44), and the
harness refuses to score an unreviewed one. `alirag review` reports PENDING for
every category lacking an evidence artifact.

## Measured facts about the target machine (Phase 0, 2026-08-17)

| | |
|---|---|
| OS / CPU / RAM | Windows 11, Intel 20-core, 68.3 GB |
| GPU | RTX 5080, **16.3 GB VRAM — only 4.7 GB free at inspection** |
| CUDA / driver | 13.3 / 591.86 |
| `E:\` | 1024 GB, 281 GB free, **662,244 files** |
| Serving | **Ollama only.** No LM Studio, llama.cpp, vLLM or SGLang running |
| Qdrant | not running → memmap dense backend auto-selected |
| Python | 3.14.6; torch and docling **not** installed |
| Parsers present | PyMuPDF, openpyxl, python-docx, python-pptx, pytesseract, PIL |

Inventory pilot (first 20,000 files): 173 s, 2,966 exact duplicates tagged,
76 revision families linked, 0 errors. Full inventory therefore ≈ 95 min.

### Corpus as actually inventoried (2026-08-17, after scoping + re-inference)

| project | files |
|---|---|
| SITE CONCEPT INTERNATIONAL | 25,042 |
| AI MAIN MEMORY | 15,431 |
| AI | 5,172 |

Knowledge: 5,140 `.md`, 4,267 `.json`, **4,170 `.pdf`**, 716 `.txt`,
322 `.xlsx`, 308 `.docx`, 83 `.pptx`. CAD: **4,228 `.dwg`**, 14 `.dxf`.

Document types after the F-V3-04 fix and `--reinfer`: DRAWING 332,
SPECIFICATION 226, BQ 146, CORRESPONDENCE 124, TENDER 116, PAYMENT 102,
CPC 101, CLAIM 93, SUBMISSION 81, MEMO 66, LAI 62, REPORT 58, EMAIL 52,
PHOTO 34, CONTRACT 34, VO 33, RFI 26, QUOTATION 24, CHECKLIST 13,
MINUTES 12, METHOD_STATEMENT 5, INVOICE 4, CATALOGUE 4, UNKNOWN 43,897.

The 4,228 `.dwg` files are the largest untapped source: DWG cannot be parsed
directly (§24) and needs a read-only export path to DXF/PDF before its content
is searchable. Only 14 `.dxf` exist today.

## Model decision

The brief's `Qwen3.8-27B` **does exist** as Ollama `qwen3.8:27b` (18 GB, 256K
context, text+image). It was absent from the machine at inspection; the user
is pulling it. Config `v3/config.kazasline.yaml` targets:

* FAST → `qwen3.5:9b` (fits VRAM, keeps `cepat:` fast)
* DEEP / FULLSWING / vision → `qwen3.8:27b`

**Hardware limit, stated not hidden (§88):** 18 GB weights exceed 16.3 GB VRAM
even when empty, so DEEP/FULLSWING offload to CPU and TTFT will run to
seconds. The §10 sub-second FAST objective is only reachable on the 9B path.
The per-mode split carries a model-swap cost that must be benchmarked before
being trusted.

## Exact next step

1. On the PC: `cd C:\rag-pipeline; git pull; & "C:\rag-pipeline\v3\GO_V3.bat"`
   — pulls the model, re-inspects, incremental inventory, **300-file pilot
   ingest**, then three real queries + safety verify + metrics.
2. Send the generated `ALIRAG_RUN_REPORT.txt` back.
3. Read from it: `safety verify.pass`, real FAST/DEEP latency, the
   `unsupported_extensions` breakdown, and `local_model_files` (what the
   Unsloth download actually is).
4. Then: full inventory + full ingest, `bench make` → human review → `bench
   run`, and finally the independent reviewer pass.

## Open questions waiting on data

* What are the 10,735 `unsupported` files in the pilot? (extension breakdown
  now in the organization report — review before full indexing, §64)
* What is holding 11.5 GB of VRAM? Freeing it materially changes DEEP latency.
* What format are the Unsloth weights, and is a second serving path (llama.cpp
  / vLLM under WSL2) worth benchmarking against Ollama (§33)?
* Do the per-mode models beat one resident model once swap cost is measured?

## Things a new session must not re-derive

* V1 at the repo root stays untouched and in production; V3 is additive.
* Never place the index on a cloud-synced drive (V1 corruption, F-V1-01).
* `hermes` and `sci_ai_library` on `E:\` are live services, not knowledge —
  they are excluded from indexing and their log writes are expected.
* Benchmarks refuse unreviewed question sets by design; the reviewer audit
  cannot emit PASS without artifacts. Do not weaken either to look finished.
