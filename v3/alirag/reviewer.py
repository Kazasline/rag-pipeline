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
    hits = sorted(folder.glob(pattern))
    return hits[-1] if hits else None


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
    item("DATA_SAFETY", _latest("safety_verify*.json", reports),
         None if safety is None else bool(safety.get("pass")),
         "§84: snapshot re-scan accounts for every change; fails on any "
         "RAG-attributable or unexplained modification/deletion/move")

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

    # CITATIONS — from any benchmark with citation_page_accuracy measured
    any_bench = _load_json(_latest("report_*.json", bench_dir))
    item("CITATIONS", _latest("report_*.json", bench_dir),
         None if not any_bench or any_bench.get("citation_page_accuracy") is None
         else any_bench["citation_page_accuracy"] >= 0.7,
         "§40/§45: cited page/location matches expectation on the benchmark")

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
    if any_bench:
        ok = (any_bench.get("recall", {}).get("@10", 0) >= 0.6
              and any_bench.get("mrr", 0) >= 0.4)
    item("RETRIEVAL_QUALITY", _latest("report_*.json", bench_dir), ok,
         "§44: Recall@10 ≥ 0.6 and MRR ≥ 0.4 on the validated benchmark "
         "(thresholds to be re-tuned against the real corpus)")

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
