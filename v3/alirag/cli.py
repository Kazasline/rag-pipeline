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

from .config import load_config
from .manifest import Manifest
from .safety import SafetyGuard


def _print(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def main(argv: list[str] | None = None):
    ap = argparse.ArgumentParser(prog="alirag", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="path to config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("inspect")
    p = sub.add_parser("inventory")
    p.add_argument("--max-files", type=int)
    p = sub.add_parser("ingest")
    p.add_argument("--limit", type=int)
    p.add_argument("--docling", action="store_true")
    p.add_argument("--ocr", action="store_true")
    p.add_argument("--render", action="store_true")
    p = sub.add_parser("query")
    p.add_argument("text")
    p.add_argument("--mode", choices=["FAST", "DEEP", "FULLSWING"])
    p.add_argument("--no-llm", action="store_true")
    sub.add_parser("status")
    p = sub.add_parser("metrics")
    p.add_argument("--mode")
    p = sub.add_parser("bench")
    p.add_argument("action", choices=["make", "run", "compare"])
    p.add_argument("--questions")
    p.add_argument("--label", default="full")
    p.add_argument("--llm", action="store_true")
    sub.add_parser("review")
    p = sub.add_parser("safety")
    p.add_argument("action", choices=["snapshot", "verify"])
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
        from .inventory import organization_report, scan
        guard = SafetyGuard(cfg.source_roots, cfg.workspace)
        mf = Manifest(cfg.manifest_db)
        counts = scan(cfg, guard, mf, max_files=args.max_files)
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
                         ocr=args.ocr, render_pages=args.render)
        _print(counts)
        ing.close()
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
        guard = SafetyGuard(cfg.source_roots, cfg.workspace)
        snap = cfg.dir("reports") / "safety_snapshot.jsonl"
        if args.action == "snapshot":
            n = guard.snapshot(snap)
            _print({"snapshot": str(snap), "files": n})
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
