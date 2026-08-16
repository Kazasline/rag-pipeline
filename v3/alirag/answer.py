r"""Query pipeline: route -> retrieve -> verify -> evidence pack -> LLM -> cited answer
(spec §7, §16, §30, §39–§40, §54, §76).

Corpus evidence has authority over model memory (§39): the system prompt
forbids answering project facts from general knowledge, and when the verifier
says INSUFFICIENT the pipeline returns the honest §16/§39 reply instead of
calling the LLM to improvise. FAST that finds too little says so briefly and
offers DEEP — it does not silently escalate into a long investigation.

Response object (§54):
  {mode, answer, confidence, sources, latency_ms, evidence_status, ...}
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from .config import Config
from .dense import make_dense
from .embed import make_embedder
from .graph import Graph
from .instrument import Trace
from .llm import LLMClient, LLMError
from .manifest import Manifest
from .retrieve import Retriever
from .router import route
from .sparse import SparseIndex
from .verify import verify

SYSTEM_PROMPT = (
    "You are a document-grounded assistant for a landscape/construction "
    "practice. Answer ONLY from the evidence blocks provided. Cite evidence "
    "as [n]. If the evidence does not contain the answer, say exactly: "
    "'Insufficient evidence found in the indexed corpus.' and state what was "
    "found and what is missing. Never use general knowledge for "
    "project-specific facts (amounts, dates, clauses, quantities, statuses). "
    "If evidence items conflict, present the conflict explicitly. "
    "Questions may be in English or Malay; answer in the language of the "
    "question. Keep FAST answers short and direct."
)

INSUFFICIENT_FAST = ("Fast search found insufficient evidence for a reliable "
                     "conclusion. Ask again with 'deep:' for a thorough search.")
INSUFFICIENT = "Insufficient evidence found in the indexed corpus."


class Engine:
    """One warm engine instance serves all queries (model/service handles
    stay open across requests — §11 'keep the model warm')."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.mf = Manifest(cfg.manifest_db)
        self.sparse = SparseIndex(cfg.sparse_db)
        self.dense = make_dense(cfg)
        self.graph = Graph(cfg.graph_db)
        self.embedder = make_embedder(cfg)
        self.retriever = Retriever(cfg, self.mf, self.sparse, self.dense,
                                   self.graph, self.embedder)
        self.llm = LLMClient(cfg)
        self._projects_cache: list[str] | None = None
        self._cache_dir = cfg.dir("cache")

    def close(self):
        self.mf.close()
        self.sparse.close()
        self.graph.close()

    # ------------------------------------------------------------ cache (§31)
    def _cache_key(self, query: str, mode: str) -> Path:
        h = hashlib.blake2b(f"{mode}|{query.strip().lower()}".encode(),
                            digest_size=12).hexdigest()
        return self._cache_dir / f"q_{h}.json"

    def _index_fingerprint(self) -> str:
        row = self.mf.con.execute(
            "SELECT COUNT(*), IFNULL(MAX(indexed_at),0) FROM files").fetchone()
        return f"{row[0]}:{row[1]}"

    # ------------------------------------------------------------ main
    def query(self, query: str, mode_override: str | None = None,
              use_llm: bool = True, use_cache: bool = True) -> dict:
        if self._projects_cache is None:
            self._projects_cache = self.retriever.known_projects()

        r = route(query, known_projects=self._projects_cache)
        if mode_override:
            r.mode = mode_override.upper()
            r.reason = f"caller override -> {r.mode}"
        trace = Trace(query, r.mode)
        trace.set("route_reason", r.reason)
        trace.set("project_hint", r.project_hint)
        trace.set("exact_ids", r.exact_ids)

        # cache lookup — invalidated whenever the index fingerprint moves (§31)
        fp = self._index_fingerprint()
        cache_path = self._cache_key(r.cleaned_query, r.mode)
        if use_cache and cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if cached.get("_fingerprint") == fp:
                    cached["cached"] = True
                    trace.stage("cache_hit", 0.0)
                    trace.set("cached", True)
                    trace.finish()
                    trace.save(self.cfg.dir("query_history"))
                    return cached
            except (json.JSONDecodeError, OSError):
                pass

        policy = self.cfg.policies[r.mode]
        evidence = self.retriever.retrieve(
            r.cleaned_query, policy, trace,
            project=r.project_hint, exact_ids=r.exact_ids)

        # verifier runs in every mode: it is pure Python over already-fetched
        # rows (microseconds), and wrong-project control (§60) matters in FAST
        # just as much as in DEEP — only the LLM-side depth differs per mode.
        verdict = verify(evidence, project_hint=r.project_hint,
                         query=r.cleaned_query)
        trace.set("evidence_status", verdict.status)
        trace.set("verifier_flags", verdict.flags)

        # FULLSWING second pass: if thin, re-retrieve seeded by evidence terms (§15)
        if r.mode == "FULLSWING" and len(verdict.kept) < max(3, policy.evidence_k // 3):
            seeds = " ".join({h["filename"] for h in verdict.kept})
            if seeds:
                extra = self.retriever.retrieve(
                    f"{r.cleaned_query} {seeds}", policy, trace,
                    project=r.project_hint, exact_ids=r.exact_ids)
                known = {h["chunk_id"] for h in verdict.kept}
                verdict.kept.extend(h for h in extra if h["chunk_id"] not in known)
                trace.set("second_pass", True)

        resp = self._answer(r, verdict, trace, use_llm=use_llm)
        resp["_fingerprint"] = fp
        trace.finish()
        trace.save(self.cfg.dir("query_history"))
        resp["latency_ms"] = trace.data["stages"] | {"total": trace.data["total_ms"]}
        if use_cache and resp.get("evidence_status") != "INSUFFICIENT":
            try:
                cache_path.write_text(json.dumps(resp, ensure_ascii=False,
                                                 default=str), encoding="utf-8")
            except OSError:
                pass
        return resp

    # ------------------------------------------------------------ answer build
    def _answer(self, r, verdict, trace: Trace, use_llm: bool) -> dict:
        base = {"mode": r.mode, "route_reason": r.reason,
                "evidence_status": verdict.status,
                "verifier_flags": verdict.flags, "conflicts": verdict.conflicts,
                "sources": [_source_line(h) for h in verdict.kept],
                "cached": False}

        if verdict.status == "INSUFFICIENT":
            found = "; ".join(f"{h['filename']} ({h.get('locator') or ''})"
                              for h in verdict.kept[:3])
            note = f" Nearest matches: {found}." if found else ""
            return {**base, "confidence": 0.0,
                    "answer": (INSUFFICIENT_FAST if r.mode == "FAST"
                               else INSUFFICIENT + note)}

        # evidence pack — smallest context that can answer (§30)
        t0 = time.perf_counter()
        blocks = []
        for i, h in enumerate(verdict.kept, 1):
            text = h["text"]
            if r.mode in ("DEEP", "FULLSWING") and h.get("parent_ord") is not None:
                text = self.retriever.expand_source(h)   # §12 source expansion
            budget = 900 if r.mode == "FAST" else 1800 if r.mode == "DEEP" else 2600
            blocks.append(f"[{i}] {h['filename']} — {_loc(h)} "
                          f"(project: {h.get('project')})\n{text[:budget]}")
        context = "\n\n".join(blocks)
        for flag in verdict.flags:
            context += f"\n\n[VERIFIER NOTE] {flag}"
        for c in verdict.conflicts:
            context += f"\n\n[CONFLICT] {c}"
        trace.stage("prompt_build", time.perf_counter() - t0,
                    {"context_chars": len(context)})

        if not use_llm:
            return {**base, "confidence": _confidence(verdict),
                    "answer": "(retrieval-only mode) " + (verdict.kept[0]["text"][:300]
                                                          if verdict.kept else "")}

        user_msg = (f"MODE: {r.mode}\n\nEVIDENCE:\n{context}\n\n"
                    f"QUESTION: {r.cleaned_query}")
        try:
            out = self.llm.chat(SYSTEM_PROMPT, user_msg, mode=r.mode)
        except LLMError as e:
            return {**base, "confidence": _confidence(verdict),
                    "answer": f"(LLM unavailable: {e}) Top evidence:\n"
                              + "\n".join(f"[{i}] {h['text'][:200]}"
                                          for i, h in enumerate(verdict.kept[:3], 1))}
        trace.set("ttft_ms", out["ttft_ms"])
        trace.stage("generation", out["gen_ms"] / 1000,
                    {"tokens": out["tokens"], "tokens_per_s": out["tokens_per_s"]})
        return {**base, "confidence": _confidence(verdict), "answer": out["text"],
                "ttft_ms": out["ttft_ms"], "tokens_per_s": out["tokens_per_s"]}


def _loc(h: dict) -> str:
    if h.get("locator"):
        return h["locator"]
    if h.get("page"):
        return f"p.{h['page']}"
    return "document"


def _source_line(h: dict) -> dict:
    """§40 provenance: file + page/sheet/slide + revision + project + path."""
    return {"file": h["filename"], "location": _loc(h),
            "revision": h.get("revision"), "project": h.get("project"),
            "path": h.get("path"),
            "superseded": bool(h.get("superseded_by")),
            "retrievers": h.get("sources", [])}


def _confidence(verdict) -> float:
    if verdict.status == "INSUFFICIENT":
        return 0.0
    base = 0.85 if verdict.status == "SUPPORTED" else 0.55
    return round(min(base + 0.02 * len(verdict.kept), 0.95), 2)
