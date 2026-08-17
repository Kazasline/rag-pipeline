r"""Independent Reviewer framework (spec §47–§51, §87).

The Builder cannot self-certify (§51). This module does two things:

1. Encodes the Reviewer checklist (§87 categories) with, for each item, the
   EVIDENCE ARTIFACT that must exist and validate before the item can PASS.
   `audit()` inspects the workspace and produces a report where every item is
   PASS / FAIL / PENDING(no evidence) — an item without a real artifact can
   never show PASS, so "everything works" without proof is unrepresentable.

2. Generates REVIEWER.md from that audit for the human/independent-agent
   Reviewer to countersign. The Reviewer role itself must run in a SEPARATE
   session/agent from the Builder (workflow documented in v3/docs/REVIEWER.md)
   and holds veto authority; this tool only guarantees they review evidence,
   not claims.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .config import Config


def _latest(pattern: str, folder: Path) -> Path | None:
    """Most RECENT matching artifact.

    Round-2 reviewer N7: this used to be `sorted(glob)[-1]` — lexicographic by
    filename. `compare_layers` writes report_baseline_dense_*, report_hybrid_*
    and report_hybrid_graph_* into the same folder that `report_*.json` scans,
    so a poor recent run was masked by an older, alphabetically-later artifact
    and the audit certified evidence the operator never produced for it.

    Recency is taken from `generated_at` inside the JSON where present (the
    artifact's own claim about when it ran), falling back to mtime, and only
    then to the name.
    """
    hits = list(folder.glob(pattern))
    if not hits:
        return None

    def key(p: Path):
        rec = _load_json(p) or {}
        return (str(rec.get("generated_at") or ""), p.stat().st_mtime, p.name)

    return sorted(hits, key=key)[-1]


# A ratio over a handful of questions is not a measurement. The per-mode gates
# already require 5; the corpus-wide quality gates required none at all.
MIN_BENCH_QUESTIONS = 5


def _load_json(path: Path | None) -> dict | None:
    if not path or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def audit(cfg: Config) -> dict:
    """Inspect workspace artifacts and produce the §87 category report."""
    reports = cfg.dir("reports")
    bench_dir = cfg.dir("benchmark")
    items = {}

    def item(name: str, artifact: Path | None, ok: bool | None, detail: str):
        status = "PENDING" if artifact is None or ok is None else ("PASS" if ok else "FAIL")
        items[name] = {"status": status,
                       "evidence": str(artifact) if artifact else None,
                       "detail": detail}

    # ARCHITECTURE — phase0 report exists and was produced on the target machine
    p0 = _load_json(_latest("phase0_machine*.json", reports))
    item("ARCHITECTURE", _latest("phase0_machine*.json", reports),
         None if p0 is None else bool(p0.get("flags", {}).get("is_target_machine")),
         "Phase 0 inspection on the target machine (config decisions traceable to it)")

    # DATA SAFETY — snapshot verification with 0 deleted / 0 modified (§84)
    safety = _load_json(_latest("safety_verify*.json", reports))
    safety_detail = ("§84: snapshot re-scan accounts for every change; fails on any "
                     "RAG-attributable or unexplained modification/deletion/move")
    if safety:
        # A pass earned by a broad allowlist is not the same as a clean run.
        # The excuse in force must be visible next to the verdict, or the
        # reviewer is signing off on a number without its caveat.
        pats = safety.get("volatile_patterns") or []
        excused = len(safety.get("allowlisted_modified") or []) + \
            len(safety.get("allowlisted_deleted") or [])
        safety_detail += (
            f" — verdict {safety.get('verdict', 'PASS' if safety.get('pass') else 'FAIL')}, "
            f"{len(pats)} operator-declared volatile pattern(s) excusing "
            f"{excused} change(s)" + (f": {pats[:5]}" if pats else ""))
        if not safety.get("hashed"):
            safety_detail += (" — WARNING: snapshot has no content hashes, so "
                              "same-size edits and renames are invisible")
    item("DATA_SAFETY", _latest("safety_verify*.json", reports),
         None if safety is None else bool(safety.get("pass")) and bool(safety.get("hashed")),
         safety_detail)

    # per-mode benchmarks (§80–§82)
    for mode in ("FAST", "DEEP", "FULLSWING"):
        rep = _load_json(_latest(f"report_{mode.lower()}*.json", bench_dir))
        ok = None
        detail = f"§8{'0' if mode == 'FAST' else '1' if mode == 'DEEP' else '2'}: real benchmark run for {mode}"
        if rep:
            # An artifact is not evidence unless the thing it certifies was
            # actually measured: a wrong-project rate computed over zero
            # questions reads as a perfect 0.0, and a report labelled for one
            # mode may have executed another.
            measured = rep.get("wrong_project_measured", 0)
            modes = rep.get("modes_run") or []
            ok = (rep.get("questions", 0) >= 5
                  and rep.get("recall", {}).get("@5", 0) > 0
                  and measured >= rep.get("questions", 0)
                  and modes == [mode]
                  and rep.get("wrong_project_rate", 1.0) < 0.2)
            detail += (f" — recall@5={rep.get('recall', {}).get('@5')}, "
                       f"wrong_project={rep.get('wrong_project_rate')} "
                       f"(measured on {measured} questions), "
                       f"modes_run={modes}, "
                       f"p95={rep.get('latency_ms', {}).get('p95')}ms")
        item(f"{mode}_MODE", _latest(f"report_{mode.lower()}*.json", bench_dir),
             ok, detail)

    # MULTIMODAL — §83 visual test report
    mm = _load_json(_latest("multimodal_eval*.json", bench_dir))
    item("MULTIMODAL", _latest("multimodal_eval*.json", bench_dir),
         None if mm is None else bool(mm.get("pass")),
         "§83: page/source correctness on real visual documents")

    # GRAPH — graph exists and materially helps (layer comparison, §86)
    cmp_rep = _load_json(_latest("layer_compare*.json", bench_dir))
    ok = None
    if cmp_rep and "hybrid" in cmp_rep and "hybrid_graph" in cmp_rep:
        ok = (cmp_rep["hybrid_graph"]["recall"].get("@5", 0)
              >= cmp_rep["hybrid"]["recall"].get("@5", 0))
    item("GRAPH", _latest("layer_compare*.json", bench_dir), ok,
         "§86: hybrid+graph does not regress hybrid; multi-hop gain documented")

    # CITATIONS / RETRIEVAL_QUALITY are judged only on PER-MODE reports.
    #
    # `report_*.json` also matches the layer-comparison artifacts written by
    # `compare_layers` (report_baseline_dense_*, report_hybrid_*), whose whole
    # purpose is to run degraded configurations. Certifying retrieval quality
    # from one of those measures the wrong thing (round-2 reviewer N7).
    mode_reports = [p for m in ("fast", "deep", "fullswing")
                    if (p := _latest(f"report_{m}*.json", bench_dir))]
    # Keep the report and its PATH together. Round-3 reviewer R3-6: the report
    # was chosen by recency while the cited path was "last of fast/deep/
    # fullswing present", so the audit graded one artifact and cited another —
    # a reviewer opening the evidence found numbers contradicting the verdict.
    # This module exists to prevent exactly that.
    graded = sorted(
        ((p, r) for p in mode_reports if (r := _load_json(p)) is not None),
        key=lambda pr: str(pr[1].get("generated_at") or ""))
    any_bench_path, any_bench = graded[-1] if graded else (None, None)

    def _enough(rep: dict | None) -> bool:
        """A ratio needs a sample. Round-2 reviewer N6: a one-question report
        with recall 1.0 and citation accuracy 1.0 passed both gates."""
        return bool(rep) and rep.get("questions", 0) >= MIN_BENCH_QUESTIONS

    cit_ok = None
    cit_detail = "§40/§45: cited page/location matches expectation on the benchmark"
    if any_bench:
        if not _enough(any_bench):
            cit_ok = False
            cit_detail += (f" — only {any_bench.get('questions', 0)} question(s); "
                           f"needs >= {MIN_BENCH_QUESTIONS}")
        elif any_bench.get("citation_page_accuracy") is None:
            cit_ok = False
            cit_detail += " — citation_page_accuracy was never measured (no expect_page set)"
        else:
            cit_ok = any_bench["citation_page_accuracy"] >= 0.7
            cit_detail += f" — {any_bench['citation_page_accuracy']}"
    item("CITATIONS", any_bench_path, cit_ok, cit_detail)

    # RESTART — §85 restart test report
    rs = _load_json(_latest("restart_test*.json", reports))
    item("RESTART", _latest("restart_test*.json", reports),
         None if rs is None else bool(rs.get("pass")),
         "§85: indexes/graph/manifest persist and queries work after restart")

    # LATENCY — FAST p95 within objective (§10), from real traces
    fast = _load_json(_latest("report_fast*.json", bench_dir))
    ok = None
    detail = "§10: FAST retrieval p95 target ~250ms (objective, not fabricated)"
    if fast:
        lat = fast.get("latency_ms", {})
        p95 = lat.get("p95")
        meaningful = lat.get("percentiles_meaningful", False)
        # Below ~20 samples p95 collapses onto the maximum, so certifying a
        # latency objective from it would be false precision.
        ok = (p95 is not None and meaningful and p95 <= 2000)
        detail += (f" — measured end-to-end p95={p95}ms over n={lat.get('n')}"
                   + ("" if meaningful else
                      " (SAMPLE TOO SMALL for a percentile — needs n>=20)"))
    item("LATENCY", _latest("report_fast*.json", bench_dir), ok, detail)

    # RETRIEVAL QUALITY — recall/MRR thresholds on validated question set (§44)
    ok = None
    detail = ("§44: Recall@10 ≥ 0.6 and MRR ≥ 0.4 on the validated benchmark "
              "(thresholds to be re-tuned against the real corpus)")
    if any_bench:
        if not _enough(any_bench):
            ok = False
            detail += (f" — only {any_bench.get('questions', 0)} question(s); "
                       f"needs >= {MIN_BENCH_QUESTIONS}")
        else:
            ok = (any_bench.get("recall", {}).get("@10", 0) >= 0.6
                  and any_bench.get("mrr", 0) >= 0.4)
            detail += (f" — recall@10={any_bench.get('recall', {}).get('@10')}, "
                       f"mrr={any_bench.get('mrr')} over "
                       f"{any_bench.get('questions')} questions")
    item("RETRIEVAL_QUALITY", any_bench_path, ok, detail)

    overall = ("PASS" if all(v["status"] == "PASS" for v in items.values())
               else "FAIL" if any(v["status"] == "FAIL" for v in items.values())
               else "PENDING")
    return {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "overall": overall, "items": items,
            "note": ("This audit only reads evidence artifacts. PASS overall "
                     "still requires the independent Reviewer's countersignature "
                     "in docs/REVIEWER.md (§47/§51).")}


def write_report(cfg: Config, audit_result: dict) -> Path:
    lines = ["# REVIEWER AUDIT (generated from evidence artifacts)",
             "",
             f"Generated: {audit_result['generated_at']}",
             f"Overall: **{audit_result['overall']}**",
             "",
             "| Category | Status | Evidence | Detail |",
             "|---|---|---|---|"]
    for name, v in audit_result["items"].items():
        lines.append(f"| {name} | {v['status']} | "
                     f"{v['evidence'] or '—'} | {v['detail']} |")
    lines += ["", "> " + audit_result["note"], "",
              "## Reviewer countersignature", "",
              "- [ ] I independently re-ran the failing/pending checks",
              "- [ ] I inspected architecture, safety and benchmark methodology",
              "- [ ] VERDICT: (PASS / FAIL / PASS WITH CONDITIONS) — signed, date",
              ""]
    out = cfg.dir("reports") / "reviewer_audit.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out
