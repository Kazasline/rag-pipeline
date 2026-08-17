r"""Benchmark harness (spec §36, §42–§45, §72, §80–§82, §86).

Principles enforced in code:
  * results come only from actually running queries against the actual index —
    an empty index or empty question set raises instead of reporting;
  * every report is stamped with machine + index fingerprints, so a number
    can never be quoted for hardware it wasn't measured on;
  * layered comparison (§86): the same question set can be run with legs
    disabled (dense-only baseline vs hybrid vs hybrid+graph) to prove each
    component earns its complexity.

Question file: JSONL in 15_BENCHMARK/, one object per line:
  {"q": "...", "expect_file": "substring-of-path-or-name",
   "expect_page": 27, "kind": "exact|semantic|crossdoc|visual|trap", "mode": "FAST"}

`make_template` samples the manifest to draft such a file locally (no cloud,
§43); the user validates/edits expectations by hand before scoring means
anything.
"""

from __future__ import annotations

import json
import platform
import time
from pathlib import Path

from .answer import Engine
from .config import Config
from .instrument import percentile


class BenchmarkError(RuntimeError):
    pass


def _machine_fingerprint() -> dict:
    """Identify the hardware a number was measured on.

    `platform.platform()` alone records only OS and kernel, so it cannot
    support the claim that a figure is tied to specific hardware — the GPU and
    memory that dominate these measurements were absent from it entirely.
    """
    import os as _os
    from .inspect_machine import inspect_gpu, inspect_ram
    return {"platform": platform.platform(),
            "cpu_count": _os.cpu_count(),
            "gpu": inspect_gpu(),
            "ram": inspect_ram()}


def make_template(cfg: Config, out_path: Path | None = None, n: int = 30) -> Path:
    """Draft a benchmark question file from real indexed files (locally).
    Expectations reference actual paths; the QUESTIONS ARE PLACEHOLDERS the
    user must review — retrieval scores against an unreviewed template
    measure nothing (§43/§44)."""
    from .manifest import Manifest
    mf = Manifest(cfg.manifest_db)
    rows = mf.con.execute(
        "SELECT f.filename, f.original_path, f.project, f.document_type, c.page, c.text "
        "FROM files f JOIN chunks c ON c.file_id=f.file_id "
        "WHERE f.index_status='INDEXED' GROUP BY f.file_id "
        "ORDER BY RANDOM() LIMIT ?", (n,)).fetchall()
    mf.close()
    if not rows:
        raise BenchmarkError("no indexed files — ingest before building a benchmark")
    out_path = out_path or cfg.dir("benchmark") / "questions.draft.jsonl"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write('{"_comment": "REVIEW EVERY LINE. Replace q with a real question '
                'whose answer lives in expect_file, confirm expect_file names a '
                'specific document, set project, then set reviewed:true. The '
                'harness refuses to score any line where reviewed is not true."}\n')
        for r in rows:
            snippet = " ".join((r["text"] or "")[:120].split())
            f.write(json.dumps({
                "q": f"REVIEW-ME: ask about `{snippet[:80]}`",
                "expect_file": r["filename"],
                "expect_page": r["page"],
                "kind": "semantic", "mode": "",
                "project": r["project"],
                "reviewed": False,          # a human must set this to true
                "_type": r["document_type"],
            }, ensure_ascii=False) + "\n")
    return Path(out_path)


# A question is unusable as evidence unless a human actually wrote it.
# The reviewer showed the old check was defeated by deleting nine characters
# from the placeholder text, so the gate now rests on a signed marker the
# generator writes and the reviewer must consciously flip, plus structural
# checks that a lazy edit cannot satisfy.
MIN_EXPECT_LEN = 6


def _validate_question(rec: dict, idx: int) -> None:
    """Reject questions that cannot produce a meaningful score."""
    q = (rec.get("q") or "").strip()
    expect = (rec.get("expect_file") or "").strip()
    if rec.get("reviewed") is not True:
        raise BenchmarkError(
            f"question {idx} is not marked reviewed. Every question must carry "
            '"reviewed": true, set by a human who confirmed the question is '
            "real and the expected source is correct (§43).")
    if len(q) < 10 or q.upper().startswith("REVIEW-ME"):
        raise BenchmarkError(f"question {idx} is a placeholder or too short: {q!r}")
    if len(expect) < MIN_EXPECT_LEN:
        raise BenchmarkError(
            f"question {idx}: expect_file {expect!r} is too short to identify a "
            "document. A bare extension matches every file and scores a "
            "meaningless Recall@K of 1.0.")
    if expect.startswith(".") and "/" not in expect and "\\" not in expect:
        raise BenchmarkError(
            f"question {idx}: expect_file {expect!r} is an extension, not a "
            "document — it would match the whole corpus.")
    if not rec.get("project"):
        raise BenchmarkError(
            f"question {idx}: missing \"project\". Without it the "
            "wrong-project rate cannot be computed, and an unmeasured rate of "
            "0.0 would silently satisfy the reviewer gate.")


def _load_questions(path: Path) -> list[dict]:
    qs = []
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if "_comment" in rec:
                continue
            _validate_question(rec, i)
            qs.append(rec)
    if not qs:
        raise BenchmarkError(
            f"no validated questions in {path} — a benchmark cannot be scored "
            "from an empty or unreviewed set (§43)")
    return qs


def run_retrieval_bench(cfg: Config, questions_path: Path,
                        k_values: tuple = (5, 10), use_llm: bool = False,
                        label: str = "full") -> dict:
    """Retrieval-quality + latency benchmark. Recall@K / MRR over the
    expected-source checks (§44), latency percentiles from the same runs."""
    qs = _load_questions(questions_path)
    eng = Engine(cfg)
    stats = eng.mf.stats()
    if stats.get("chunks_total", 0) == 0:
        raise BenchmarkError("index is empty — nothing to benchmark")

    per_q = []
    latencies = []
    for rec in qs:
        t0 = time.perf_counter()
        resp = eng.query(rec["q"], mode_override=rec.get("mode") or None,
                         use_llm=use_llm, use_cache=False)
        dt = (time.perf_counter() - t0) * 1000
        latencies.append(dt)
        expect = rec["expect_file"].lower()
        ranks = [i for i, s in enumerate(resp.get("sources", []), 1)
                 if expect in (s.get("file", "") + s.get("path", "")).lower()]
        first = ranks[0] if ranks else None
        page_ok = None
        if first and rec.get("expect_page"):
            src = resp["sources"][first - 1]
            page_ok = str(rec["expect_page"]) in str(src.get("location", ""))
        # `project` is mandatory (see _validate_question), so this is always
        # genuinely measured. Reporting 0.0 for "not checked" is what let an
        # unmeasured benchmark satisfy the reviewer's wrong-project gate.
        expected_project = rec.get("project")
        wrong_project = any(
            s.get("project") not in (expected_project, "UNKNOWN", None)
            for s in resp.get("sources", [])[:3])
        # Which retrieval legs actually contributed. §86 asks whether each
        # layer earns its complexity; that cannot be checked from recall alone
        # if the "baseline" silently still ran the full stack.
        sources_used = sorted({leg for s in resp.get("sources", [])
                               for leg in (s.get("retrievers") or [])})
        per_q.append({"q": rec["q"], "kind": rec.get("kind"), "mode": resp["mode"],
                      "sources_used": sources_used,
                      "first_rank": first, "page_ok": page_ok,
                      "wrong_project_in_top3": wrong_project,
                      "evidence_status": resp["evidence_status"],
                      "latency_ms": round(dt, 1)})
    eng.close()

    n = len(per_q)
    modes_run = sorted({r["mode"] for r in per_q})
    if label.upper() in ("FAST", "DEEP", "FULLSWING") and modes_run != [label.upper()]:
        raise BenchmarkError(
            f"--label {label} claims a {label.upper()} benchmark but the run "
            f"actually executed modes {modes_run}. A mislabelled artifact would "
            f"satisfy the reviewer's {label.upper()} gate with the wrong data.")

    report = {
        "label": label, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "modes_run": modes_run,
        "machine": _machine_fingerprint(),
        "index_stats": stats, "questions": n, "use_llm": use_llm,
        "recall": {f"@{k}": round(sum(1 for r in per_q
                                      if r["first_rank"] and r["first_rank"] <= k) / n, 3)
                   for k in k_values},
        "mrr": round(sum(1 / r["first_rank"] for r in per_q if r["first_rank"]) / n, 3),
        "citation_page_accuracy": _ratio([r["page_ok"] for r in per_q]),
        "wrong_project_rate": round(
            sum(1 for r in per_q if r["wrong_project_in_top3"]) / n, 3),
        "wrong_project_measured": n,   # explicit: how many questions were checked
        "latency_ms": {"p50": round(percentile(latencies, 50), 1),
                       "p95": round(percentile(latencies, 95), 1),
                       "p99": round(percentile(latencies, 99), 1),
                       "n": len(latencies),
                       # below ~20 samples p95 and p99 both collapse onto the
                       # maximum; reporting them as percentiles is false precision
                       "percentiles_meaningful": len(latencies) >= 20},
        "per_question": per_q,
    }
    out = cfg.dir("benchmark") / f"report_{label}_{int(time.time())}.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    report["_report_path"] = str(out)
    return report


def _ratio(vals: list) -> float | None:
    known = [v for v in vals if v is not None]
    return round(sum(1 for v in known if v) / len(known), 3) if known else None


def compare_layers(cfg: Config, questions_path: Path) -> dict:
    """§86 layered comparison. Runs the same questions with retrieval legs
    disabled via policy edits: dense-only baseline, hybrid, hybrid+graph.
    Each layer must earn its complexity with measured gains."""
    import copy
    results = {}
    variants = {
        "baseline_dense": {"sparse_k": 0, "graph_hops": 0},
        "hybrid": {"graph_hops": 0},
        "hybrid_graph": {},
    }
    for label, overrides in variants.items():
        c = copy.deepcopy(cfg)
        for pol in c.policies.values():
            for k, v in overrides.items():
                setattr(pol, k, v)
        results[label] = {k: v for k, v in
                          run_retrieval_bench(c, questions_path, label=label).items()
                          if k in ("recall", "mrr", "wrong_project_rate",
                                   "latency_ms", "_report_path")}
    return results
