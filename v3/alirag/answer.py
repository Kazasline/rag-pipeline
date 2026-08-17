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
        """Cache validity key.

        Covers the index AND the pipeline/model configuration: a code or model
        change alters how an answer is produced, so cached answers from before
        it must not survive (F-V3-12). Otherwise a fixed bug keeps being served
        from cache and looks unfixed.
        """
        import dataclasses

        from . import PIPELINE_VERSION
        row = self.mf.con.execute(
            "SELECT COUNT(*), IFNULL(MAX(indexed_at),0) FROM files").fetchone()
        cfg = self.cfg.llm
        # Everything that shapes the answer belongs in the key — including the
        # retrieval policy and the system prompt. Omitting them meant halving
        # evidence_k, or rewriting the prompt outright, still served the old
        # answer from cache.
        policy_repr = json.dumps(
            {m: dataclasses.asdict(p) for m, p in sorted(self.cfg.policies.items())},
            sort_keys=True)
        shape = "|".join([
            PIPELINE_VERSION, cfg.api_style, cfg.model, cfg.model_fast,
            cfg.model_deep, cfg.model_fullswing,
            str(cfg.max_answer_tokens_fast), str(cfg.max_answer_tokens_deep),
            str(cfg.max_answer_tokens_fullswing),
            self.cfg.embed.model, str(self.cfg.embed.dim),
            policy_repr, SYSTEM_PROMPT,
        ])
        digest = hashlib.blake2b(shape.encode("utf-8"), digest_size=8).hexdigest()
        return f"{row[0]}:{row[1]}:{digest}"

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
                    # Do NOT replay the latency recorded when this answer was
                    # first computed: it was measured for a different request
                    # and, aggregated, it deflated the p50 the operator reads.
                    cached.pop("latency_ms", None)
                    cached.pop("ttft_ms", None)
                    trace.stage("cache_hit", 0.0)
                    trace.set("cached", True)
                    trace.finish()
                    trace.save(self.cfg.dir("query_history"))
                    cached["latency_ms"] = {"cache_hit": 0.0,
                                            "total": trace.data["total_ms"]}
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
                         query=r.cleaned_query, cross_project=r.cross_project)
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
                combined = verdict.kept + [h for h in extra
                                           if h["chunk_id"] not in known]
                # Re-verify the COMBINED set (round-2 reviewer N8).
                #
                # The second pass used to extend verdict.kept after verify()
                # had already returned, so its results were cited and packed
                # into the prompt having passed no project check, no superseded
                # disclosure and no conflict surfacing — the checks were run on
                # a strict subset of the evidence actually used.
                verdict = verify(combined, project_hint=r.project_hint,
                                 query=r.cleaned_query,
                                 cross_project=r.cross_project)
                trace.set("second_pass", True)
                trace.set("evidence_status", verdict.status)
                trace.set("verifier_flags", verdict.flags)

        resp = self._answer(r, verdict, trace, use_llm=use_llm)
        resp["_fingerprint"] = fp
        trace.finish()
        trace.save(self.cfg.dir("query_history"))
        resp["latency_ms"] = trace.data["stages"] | {"total": trace.data["total_ms"]}
        # F-V3-12: never cache a failed generation. An empty answer was stored
        # under PARTIAL and then served back after the bug was fixed, so the
        # failure outlived its cause and looked unfixed.
        cacheable = (resp.get("evidence_status") not in ("INSUFFICIENT",
                                                         "AMBIGUOUS_PROJECT")
                     and not resp.get("generation_error"))
        if use_cache and cacheable:
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

        # §60: an ambiguous question gets a question back, not a blended answer.
        if verdict.status == "AMBIGUOUS_PROJECT":
            lines = []
            for proj, items in sorted(verdict.by_project.items(),
                                      key=lambda kv: (-len(kv[1]), kv[0])):
                names = sorted({i["filename"] for i in items})[:3]
                lines.append(f"  - {proj}: {len(items)} evidence item(s) "
                             f"({', '.join(names)})")
            return {**base, "evidence_score": 0.0,
                    "projects": sorted(verdict.by_project),
                    "answer": (
                        "This question matches documents from more than one "
                        "project, and the answers would differ. Tell me which "
                        "project you mean and I will answer from that one only "
                        "(or say 'across all projects' to compare them):\n"
                        + "\n".join(lines))}

        if verdict.status == "INSUFFICIENT":
            found = "; ".join(f"{h['filename']} ({h.get('locator') or ''})"
                              for h in verdict.kept[:3])
            note = f" Nearest matches: {found}." if found else ""
            return {**base, "evidence_score": 0.0,
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
            return {**base, "evidence_score": _evidence_score(verdict),
                    "answer": "(retrieval-only mode) " + (verdict.kept[0]["text"][:300]
                                                          if verdict.kept else "")}

        user_msg = (f"MODE: {r.mode}\n\nEVIDENCE:\n{context}\n\n"
                    f"QUESTION: {r.cleaned_query}")
        try:
            out = self.llm.chat(SYSTEM_PROMPT, user_msg, mode=r.mode)
        except LLMError as e:
            return {**base, "evidence_score": _evidence_score(verdict),
                    "answer": f"(LLM unavailable: {e}) Top evidence:\n"
                              + "\n".join(f"[{i}] {h['text'][:200]}"
                                          for i, h in enumerate(verdict.kept[:3], 1))}
        trace.set("ttft_ms", out["ttft_ms"])
        trace.stage("generation", out["gen_ms"] / 1000,
                    {"tokens": out["tokens"],
                     "reasoning_tokens": out.get("reasoning_tokens"),
                     "finish_reason": out.get("finish_reason"),
                     "tokens_per_s": out["tokens_per_s"]})

        # F-V3-09: an empty generation is a FAILURE, not a supported answer.
        # Retrieval may have been perfect, but with no text there is nothing
        # grounded to report — saying so (with the reason) beats handing back
        # an empty string carrying a 0.93 confidence.
        if not out["text"]:
            reason = out.get("empty_reason") or "model returned no content"
            return {**base,
                    "evidence_status": "PARTIAL",
                    "evidence_score": 0.0,
                    "answer": (f"Retrieval succeeded but the model produced no answer "
                               f"({reason}). The evidence found is listed under sources."),
                    "generation_error": reason,
                    "ttft_ms": out["ttft_ms"],
                    "reasoning_tokens": out.get("reasoning_tokens"),
                    "finish_reason": out.get("finish_reason")}

        return {**base, "evidence_score": _evidence_score(verdict), "answer": out["text"],
                "ttft_ms": out["ttft_ms"], "tokens_per_s": out["tokens_per_s"],
                "reasoning_tokens": out.get("reasoning_tokens"),
                "finish_reason": out.get("finish_reason")}


def _loc(h: dict) -> str:
    if h.get("locator"):
        return h["locator"]
    if h.get("page"):
        return f"p.{h['page']}"
    return "document"


def _source_line(h: dict) -> dict:
    """§40 provenance: file + page/sheet/slide + revision + project + path."""
    return {"file": h["filename"], "location": _loc(h),
            # identifiers so a citation can be followed programmatically —
            # /source/{file_id} exists but the citation did not carry the id
            "file_id": h.get("file_id"), "chunk_id": h.get("chunk_id"),
            "page": h.get("page"),
            "revision": h.get("revision"), "project": h.get("project"),
            "path": h.get("path"),
            "superseded": bool(h.get("superseded_by")),
            "retrievers": h.get("sources", [])}


def _evidence_score(verdict) -> float:
    """A coarse function of evidence STATUS and COUNT — not a calibrated
    confidence.

    It was previously published as `confidence`, which invited the reader to
    treat it as a probability of correctness: an off-corpus question scored
    0.63. It has no relationship to retrieval scores or measured accuracy, so
    it is named for what it actually is, and `confidence` is only emitted once
    calibration exists.
    """
    if verdict.status == "INSUFFICIENT":
        return 0.0
    base = 0.85 if verdict.status == "SUPPORTED" else 0.55
    return round(min(base + 0.02 * len(verdict.kept), 0.95), 2)
