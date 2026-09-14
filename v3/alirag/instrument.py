r"""Performance instrumentation (spec §41–§42).

Every query produces a Trace: mode, retrievers used, per-stage seconds,
candidate counts, TTFT, tokens/sec, sources. Traces append to a JSONL log in
13_QUERY_HISTORY; `percentiles()` aggregates p50/p95/p99 per stage across a
log — the numbers reported to the user come from these records, never from a
single lucky query (§42) and never from fabrication (§79).
"""

from __future__ import annotations

import json
import time
from pathlib import Path


class Trace:
    def __init__(self, query: str, mode: str):
        self.t0 = time.perf_counter()
        self.data = {"ts": time.time(), "query": query, "mode": mode,
                     "stages": {}, "meta": {}}

    def stage(self, name: str, seconds: float, extra: dict | None = None):
        self.data["stages"][name] = round(seconds * 1000, 2)  # ms
        if extra:
            self.data["meta"][name] = extra

    def set(self, key: str, value):
        self.data[key] = value

    def finish(self) -> dict:
        self.data["total_ms"] = round((time.perf_counter() - self.t0) * 1000, 2)
        return self.data

    def save(self, log_dir: str | Path):
        path = Path(log_dir) / "query_traces.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(self.data, ensure_ascii=False, default=str) + "\n")


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    idx = min(len(xs) - 1, max(0, round(p / 100 * (len(xs) - 1))))
    return xs[idx]


def percentiles(trace_log: str | Path, mode: str | None = None,
                include_cached: bool = False) -> dict:
    """Aggregate p50/p95/p99 per stage + total from a trace JSONL.

    Cache hits are EXCLUDED by default: they cost ~0 ms and describe no
    retrieval or generation work, so mixing them in silently deflated the p50
    that gets quoted as system latency.
    """
    stages: dict[str, list[float]] = {}
    totals: list[float] = []
    ttfts: list[float] = []
    n = 0
    cached_skipped = 0
    path = Path(trace_log)
    if not path.exists():
        return {"queries": 0, "note": "no traces recorded yet — run real queries first"}
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if mode and rec.get("mode") != mode:
                continue
            if rec.get("cached") and not include_cached:
                cached_skipped += 1
                continue
            n += 1
            if "total_ms" in rec:
                totals.append(rec["total_ms"])
            if rec.get("ttft_ms"):
                ttfts.append(rec["ttft_ms"])
            for s, ms in rec.get("stages", {}).items():
                stages.setdefault(s, []).append(ms)
    out = {"queries": n, "mode": mode or "ALL",
           "cache_hits_excluded": cached_skipped}
    for name, vals in [("total_ms", totals), ("ttft_ms", ttfts),
                       *stages.items()]:
        if vals:
            out[name] = {"p50": round(percentile(vals, 50), 1),
                         "p95": round(percentile(vals, 95), 1),
                         "p99": round(percentile(vals, 99), 1),
                         "n": len(vals),
                         # p95/p99 collapse onto the maximum for small samples
                         "percentiles_meaningful": len(vals) >= 20}
    return out
