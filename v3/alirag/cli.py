r"""ALI RAG V3 command line.

  python -m alirag.cli inspect                 Phase 0: read-only machine report
  python -m alirag.cli inventory               Phase 1: read-only source inventory
  python -m alirag.cli ingest [--limit N] [--docling] [--ocr] [--render]
  python -m alirag.cli query "cepat: find LAI-003" [--mode FAST] [--no-llm]
  python -m alirag.cli status                  dashboard (§77)
  python -m alirag.cli metrics [--mode FAST]   latency percentiles from real traces
  python -m alirag.cli bench make|run|compare  benchmark harness (§43/§86)
  python -m alirag.cli review                  evidence-gated reviewer audit (§87)
  python -m alirag.cli safety snapshot|verify  §84 acceptance evidence
  python -m alirag.cli restart-test            §85 persistence check
  python -m alirag.cli backup                  §66 config/manifest/graph backup
  python -m alirag.cli serve                   local API (§53)
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

# F-V3-06: the Windows console defaults to cp1252, which cannot encode the
# arrows/box characters that appear in retrieved document text — printing a
# result crashed the whole command with UnicodeEncodeError. V1 learned this and
# reconfigured stdout; V3 did not carry it over. Answers must never be lost to
# a console encoding.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

from .config import load_config
from .manifest import Manifest
from .safety import SafetyGuard


def _print(obj):
    text = json.dumps(obj, indent=2, ensure_ascii=False, default=str)
    try:
        print(text)
    except UnicodeEncodeError:
        # last-resort belt and braces if reconfigure() was unavailable
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(text.encode(enc, errors="replace").decode(enc, errors="replace"))


def main(argv: list[str] | None = None):
    ap = argparse.ArgumentParser(prog="alirag", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="path to config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("inspect")
    p = sub.add_parser("inventory")
    p.add_argument("--max-files", type=int)
    p.add_argument("--root", action="append",
                   help="scan only this folder (repeatable); overrides source_roots")
    p.add_argument("--reinfer", action="store_true",
                   help="re-apply metadata rules to files already in the manifest "
                        "(no re-hashing) — use after changing inference rules")
    p = sub.add_parser("ingest")
    p.add_argument("--limit", type=int)
    p.add_argument("--project", help="only ingest files whose project matches")
    p.add_argument("--path", help="only ingest files under this path prefix")
    p.add_argument("--docling", action="store_true")
    p.add_argument("--ocr", action="store_true")
    p.add_argument("--render", action="store_true")
    p = sub.add_parser("failures", help="show why files failed to ingest")
    p.add_argument("--limit", type=int, default=40)
    p = sub.add_parser("query")
    p.add_argument("text")
    p.add_argument("--mode", choices=["FAST", "DEEP", "FULLSWING"])
    p.add_argument("--no-llm", action="store_true")
    sub.add_parser("status")
    p = sub.add_parser("metrics")
    p.add_argument("--mode")
    p = sub.add_parser("bench")
    p.add_argument("action", choices=["make", "run", "compare"])
    p.add_argument("--csv", action="store_true",
                   help="write the question template as a CSV you can edit in "
                        "Excel instead of JSONL (recommended)")
    p.add_argument("--questions")
    p.add_argument("--label", default="full")
    p.add_argument("--llm", action="store_true")
    sub.add_parser("review")
    p = sub.add_parser("safety")
    p.add_argument("action", choices=["snapshot", "verify"])
    p.add_argument("--sample", type=int, metavar="N",
                   help="snapshot only N files and report throughput, so the "
                        "cost of a full run can be estimated on THIS machine "
                        "before committing to it (§88). Writes to a separate "
                        "file; never overwrites the real snapshot.")
    sub.add_parser("restart-test")
    sub.add_parser("backup")
    sub.add_parser("serve")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)

    if args.cmd == "inspect":
        from .inspect_machine import main as phase0
        out = cfg.dir("reports") / f"phase0_machine_{int(time.time())}.json"
        phase0(str(out))
        return

    if args.cmd == "inventory":
        from .inventory import organization_report, reinfer_metadata, scan
        guard = SafetyGuard(cfg.source_roots, cfg.workspace,
                            excluded_dirs=cfg.ingest.exclude_dirs)
        mf = Manifest(cfg.manifest_db)
        if args.reinfer:
            res = reinfer_metadata(cfg, mf)
            _print({"reinfer": res, "organization": organization_report(mf)})
            mf.close()
            return
        counts = scan(cfg, guard, mf, max_files=args.max_files, roots=args.root)
        report = organization_report(mf)
        out = cfg.dir("reports") / f"inventory_{int(time.time())}.json"
        out.write_text(json.dumps({"counts": counts, "organization": report},
                                  indent=2, ensure_ascii=False), encoding="utf-8")
        _print({"counts": counts,
                "organization": {k: (dict(list(v.items())[:15]) if isinstance(v, dict) else v)
                                 for k, v in report.items()},
                "report": str(out)})
        mf.close()
        return

    if args.cmd == "ingest":
        from .ingest import Ingestor
        ing = Ingestor(cfg)
        counts = ing.run(limit=args.limit, use_docling=args.docling,
                         ocr=args.ocr, render_pages=args.render,
                         project=args.project, path_prefix=args.path)
        _print(counts)
        ing.close()
        return

    if args.cmd == "failures":  # §78 — a failed document must not disappear
        mf = Manifest(cfg.manifest_db)
        rows = mf.con.execute(
            "SELECT original_path, extension, index_note FROM files "
            "WHERE index_status='FAILED' LIMIT ?", (args.limit,)).fetchall()
        by_reason: dict = {}
        for r in mf.con.execute(
                "SELECT index_note, COUNT(*) c FROM files WHERE index_status='FAILED' "
                "GROUP BY index_note ORDER BY c DESC LIMIT 20"):
            by_reason[(r[0] or "")[:160]] = r[1]
        by_ext = {r[0]: r[1] for r in mf.con.execute(
            "SELECT extension, COUNT(*) FROM files WHERE index_status='FAILED' "
            "GROUP BY extension ORDER BY 2 DESC")}
        _print({"total_failed": mf.con.execute(
                    "SELECT COUNT(*) FROM files WHERE index_status='FAILED'").fetchone()[0],
                "by_reason": by_reason, "by_extension": by_ext,
                "examples": [{"path": r[0], "ext": r[1], "note": r[2]} for r in rows[:15]]})
        mf.close()
        return

    if args.cmd == "query":
        from .answer import Engine
        eng = Engine(cfg)
        resp = eng.query(args.text, mode_override=args.mode,
                         use_llm=not args.no_llm)
        resp.pop("_fingerprint", None)
        _print(resp)
        eng.close()
        return

    if args.cmd == "status":  # §77 operator view
        from .answer import Engine
        eng = Engine(cfg)
        _print({"manifest": eng.mf.stats(), "sparse": eng.sparse.stats(),
                "graph": eng.graph.stats(), "dense_count": eng.dense.count(),
                "dense_backend": type(eng.dense).__name__,
                "llm_up": eng.llm.health(), "workspace": cfg.workspace})
        eng.close()
        return

    if args.cmd == "metrics":
        from .instrument import percentiles
        _print(percentiles(cfg.dir("query_history") / "query_traces.jsonl",
                           mode=args.mode))
        return

    if args.cmd == "bench":
        from . import bench
        qpath = Path(args.questions) if args.questions else \
            cfg.dir("benchmark") / "questions.jsonl"
        if args.action == "make":
            if args.csv:
                out = bench.make_csv_template(cfg)
                print(f"""
[bench] Question sheet written to:
    {out}

Open it in Excel (double-click). You will see one row per document, with the
document's name already filled in and a snippet of its text so you can tell
what it is.

For each row you want to use:
  1. `question`      - type a REAL question in your own words, one you would
                       actually ask, whose answer is inside that document.
  2. `expected_file` - already filled in. Change it only if a DIFFERENT file
                       is the one that should answer your question.
  3. `project`       - already filled in; correct it if it is wrong.
  4. `reviewed`      - change `no` to `yes` once you are happy with the row.

Rows you leave alone are ignored. You need at least 5 rows set to `yes`.
Delete the rest if you like. Save as CSV (keep the format when Excel asks).

Then run:
    python -m alirag.cli bench run --questions "{out}" --label fast

Why you have to do this and not me: the score is meaningless unless the
questions are real and the expected answers are correct, and only you know
your documents. A question set I invented would produce a number that looks
like a measurement and is not one.
""".rstrip(), flush=True)
            else:
                out = bench.make_template(cfg)
                print(f"[bench] draft written to {out} — REVIEW EVERY QUESTION, "
                      f"then save as {qpath}")
        elif args.action == "run":
            _print(bench.run_retrieval_bench(cfg, qpath, use_llm=args.llm,
                                             label=args.label))
        else:
            rep = bench.compare_layers(cfg, qpath)
            out = cfg.dir("benchmark") / f"layer_compare_{int(time.time())}.json"
            out.write_text(json.dumps(rep, indent=2), encoding="utf-8")
            _print(rep)
        return

    if args.cmd == "review":
        from .reviewer import audit, write_report
        result = audit(cfg)
        path = write_report(cfg, result)
        _print(result)
        print(f"\n[review] report: {path}")
        return

    if args.cmd == "safety":
        guard = SafetyGuard(cfg.source_roots, cfg.workspace,
                            excluded_dirs=cfg.ingest.exclude_dirs)
        # volatile_patterns is removed (round-6 R6-1). If a config still sets
        # it, say so rather than silently ignoring a line the operator believes
        # is protecting them.
        if cfg.volatile_patterns:
            print("[safety] WARNING: volatile_patterns is no longer honoured "
                  "and was ignored: " + ", ".join(map(str, cfg.volatile_patterns))
                  + "\n          Declare these directories in "
                  "ingest.exclude_dirs instead; their changes are then reported "
                  "under excluded_dir_* without failing the §84 verdict.",
                  flush=True)
        snap = cfg.dir("reports") / "safety_snapshot.jsonl"
        if args.action == "snapshot":
            if args.sample:
                # §88: the cost of hashing every file on a terabyte drive has
                # never been measured, and DATA_SAFETY acceptance depends on
                # that run completing. Measure a sample HERE rather than
                # publishing an estimate derived from different hardware.
                sample_path = cfg.dir("reports") / "safety_snapshot.sample.jsonl"
                t0 = time.time()
                n = guard.snapshot(sample_path, max_files=args.sample)
                dt = max(time.time() - t0, 1e-6)
                total = sum(1 for _ in guard._walk_sources())
                rate = n / dt
                _print({
                    "sample": str(sample_path), "files_sampled": n,
                    "elapsed_s": round(dt, 2),
                    "files_per_second": round(rate, 1),
                    "files_under_source_roots": total,
                    "estimated_full_run_s": round(total / rate, 1) if rate else None,
                    "note": ("EXTRAPOLATION from a sample on this machine, not "
                             "a measured full run. Hashing cost varies with "
                             "file size, so a sample biased toward small files "
                             "will understate it. This is here so the operator "
                             "can decide before starting, not so the estimate "
                             "can be quoted as a result (§79/§88)."),
                })
                return
            t0 = time.time()
            n = guard.snapshot(snap)
            _print({"snapshot": str(snap), "files": n,
                    "elapsed_s": round(time.time() - t0, 1),
                    "excluded_dirs": list(guard.excluded_dirs)})
        else:
            result = guard.verify_snapshot(snap)
            out = cfg.dir("reports") / f"safety_verify_{int(time.time())}.json"
            out.write_text(json.dumps(result, indent=2), encoding="utf-8")
            _print({**result,
                    "deleted": result["deleted"][:20],
                    "modified": result["modified"][:20],
                    "report": str(out)})
        return

    if args.cmd == "restart-test":  # §85
        from .answer import Engine
        eng = Engine(cfg)
        before = {"manifest": eng.mf.stats(), "dense": eng.dense.count(),
                  "graph": eng.graph.stats()}
        eng.close()
        # simulate service restart: brand-new handles reading persisted state
        eng2 = Engine(cfg)
        after = {"manifest": eng2.mf.stats(), "dense": eng2.dense.count(),
                 "graph": eng2.graph.stats()}
        q_ok = True
        try:
            eng2.query("restart smoke test", use_llm=False, use_cache=False)
        except Exception as e:  # noqa: BLE001
            q_ok = False
            print(f"[restart-test] query failed: {e}")
        eng2.close()
        result = {"before": before, "after": after,
                  "pass": before == after and q_ok, "query_ok": q_ok}
        out = cfg.dir("reports") / f"restart_test_{int(time.time())}.json"
        out.write_text(json.dumps(result, indent=2), encoding="utf-8")
        _print(result)
        return

    if args.cmd == "backup":  # §66
        stamp = time.strftime("%Y%m%d_%H%M%S")
        dest = cfg.dir("backup_config") / stamp
        dest.mkdir(parents=True, exist_ok=True)
        copied = []
        for key, name in (("config", "config"), ("manifest", "manifest"),
                          ("graph", "graph"), ("benchmark", "benchmark"),
                          ("skills", "skills")):
            src = cfg.dir(key)
            if any(src.iterdir()):
                shutil.copytree(src, dest / name, dirs_exist_ok=True)
                copied.append(name)
        _print({"backup": str(dest), "copied": copied,
                "note": "dense/sparse indexes are rebuildable derivatives (§67); "
                        "originals on the source drive are untouched and remain "
                        "the ultimate source (§66)"})
        return

    if args.cmd == "serve":
        from .api import serve
        serve(cfg)
        return


if __name__ == "__main__":
    main(sys.argv[1:])
