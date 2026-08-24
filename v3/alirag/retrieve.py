r"""Retrieval orchestrator (spec §7, §9, §12–§15, §17, §74).

One orchestrator, specialized tools (§58). Per the routed mode's policy it
activates only the components required:

  FAST:      exact-ID -> (sparse ∥ dense) -> RRF -> tiny evidence set
  DEEP:      + broader candidates, graph 1-hop, source expansion, verifier
  FULLSWING: + wider still, graph 2-hop, second-pass retrieval on evidence gaps

Sparse and dense legs run in parallel threads (§74) — they touch different
stores (SQLite FTS vs memmap/Qdrant) so this is safe and roughly halves
retrieval latency. Fusion is Reciprocal Rank Fusion (k=60), the proven
default; alternatives are a benchmark decision (§17).
"""

from __future__ import annotations

import concurrent.futures
import time

from .config import Config, RetrievalPolicy
from .instrument import Trace
from .manifest import Manifest


def rrf_fuse(result_lists: list[list[dict]], k: int = 60,
             weights: dict[str, float] | None = None) -> list[dict]:
    """Reciprocal Rank Fusion across retriever outputs.
    score = Σ w_source / (k + rank). Exact-ID hits get a strong default weight
    so a matching drawing/document code cannot be buried by prose (§9)."""
    weights = weights or {"exact": 2.0, "sparse": 1.0, "dense": 1.0, "graph": 0.7}
    fused: dict[int, dict] = {}
    for results in result_lists:
        for rank, r in enumerate(results, 1):
            w = weights.get(r.get("source", ""), 1.0)
            e = fused.setdefault(r["chunk_id"], {"chunk_id": r["chunk_id"],
                                                 "score": 0.0, "sources": []})
            e["score"] += w / (k + rank)
            if r.get("source") not in e["sources"]:
                e["sources"].append(r.get("source"))
    return sorted(fused.values(), key=lambda x: -x["score"])


class Retriever:
    def __init__(self, cfg: Config, mf: Manifest, sparse, dense, graph, embedder):
        self.cfg = cfg
        self.mf = mf
        self.sparse = sparse
        self.dense = dense
        self.graph = graph
        self.embedder = embedder

    # ------------------------------------------------------------ helpers
    def known_projects(self) -> list[str]:
        return [r[0] for r in self.mf.con.execute(
            "SELECT DISTINCT project FROM files WHERE project != 'UNKNOWN'")]

    def _chunks_for_project(self, project: str) -> set[int]:
        return {r[0] for r in self.mf.con.execute(
            "SELECT c.chunk_id FROM chunks c JOIN files f ON f.file_id=c.file_id "
            "WHERE f.project=?", (project,))}

    def hydrate(self, hits: list[dict]) -> list[dict]:
        """Attach chunk text + full provenance to fused hits."""
        out = []
        for h in hits:
            row = self.mf.chunk(h["chunk_id"])
            if row is None:
                continue
            out.append({**h, "text": row["text"], "page": row["page"],
                        "locator": row["locator"], "filename": row["filename"],
                        "path": row["original_path"], "project": row["project"],
                        "project_source": row["project_source"],
                        "revision": row["revision"],
                        "document_type": row["document_type"],
                        "superseded_by": row["superseded_by"],
                        "file_id": row["file_id"], "level": row["level"],
                        "ord": row["ord"], "parent_ord": row["parent_ord"]})
        return out

    def expand_source(self, hit: dict, radius: int = 1) -> str:
        """Source expansion (§12): pull neighboring chunks of a hit so DEEP
        reads context, not an isolated fragment."""
        rows = self.mf.con.execute(
            "SELECT text FROM chunks WHERE file_id=? AND ord BETWEEN ? AND ? "
            "ORDER BY ord", (hit["file_id"], hit["ord"] - radius, hit["ord"] + radius)
        ).fetchall()
        return "\n".join(r[0] for r in rows)

    # ------------------------------------------------------------ main entry
    def retrieve(self, query: str, policy: RetrievalPolicy, trace: Trace,
                 project: str | None = None,
                 exact_ids: list[str] | None = None) -> list[dict]:
        legs: list[list[dict]] = []

        # Resolve the project scope FIRST so every leg honours it, including
        # the exact-ID leg (§60).
        allowed = self._chunks_for_project(project) if project else None

        # exact-ID leg first — cheap, deterministic, and decisive when it hits.
        # Gated on sparse_k so a dense-only baseline (§86) disables ALL lexical legs.
        t0 = time.perf_counter()
        exact_hits = (self.sparse.search_ids(query, k=policy.sparse_k,
                                             allowed_chunks=allowed)
                      if exact_ids and policy.sparse_k > 0 else [])
        trace.stage("exact_search", time.perf_counter() - t0,
                    {"hits": len(exact_hits)})
        if exact_hits:
            legs.append(exact_hits)

        # sparse ∥ dense (§74)
        def _sparse():
            t = time.perf_counter()
            if policy.sparse_k <= 0:
                return [], time.perf_counter() - t
            r = self.sparse.search(query, k=policy.sparse_k, project=project)
            return r, time.perf_counter() - t

        def _dense():
            t = time.perf_counter()
            te = time.perf_counter()
            qvec = self.embedder.embed([query])[0]
            embed_s = time.perf_counter() - te
            # Dispatch on the backend's actual capability rather than by
            # catching TypeError: a TypeError raised INSIDE a backend's search
            # would otherwise trigger a retry with a signature it rejects,
            # masking the real error behind a misleading second one.
            if hasattr(self.dense, "supports_project_filter"):
                r = self.dense.search(qvec, k=policy.dense_k, project=project)
            else:
                r = self.dense.search(qvec, k=policy.dense_k,
                                      allowed_chunks=allowed)
            return r, time.perf_counter() - t, embed_s

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            fs = ex.submit(_sparse)
            fd = ex.submit(_dense)
            sparse_hits, sparse_s = fs.result()
            dense_hits, dense_s, embed_s = fd.result()
        trace.stage("embedding", embed_s)
        trace.stage("sparse_search", sparse_s, {"hits": len(sparse_hits)})
        trace.stage("dense_search", dense_s, {"hits": len(dense_hits)})
        legs.append(sparse_hits)
        legs.append(dense_hits)

        # graph leg — only when the policy asks for hops AND we have seeds (§9)
        if policy.graph_hops > 0:
            t0 = time.perf_counter()
            seeds = (exact_ids or []) + ([project] if project else [])
            if not seeds:
                seeds = [w for w in query.split() if len(w) > 3][:4]
            hood = self.graph.neighborhood(seeds, hops=policy.graph_hops)
            # Scope BEFORE fusion (round-2 reviewer N4). The graph was the one
            # leg with no project scoping: its hits entered RRF, occupied
            # fused_k slots, and were only dropped afterwards. At corpus scale
            # a project-scoped query could have its whole fusion window taken
            # by foreign graph hits and then be truncated to nothing — the same
            # filter-after-selection defect as F4, in the last leg that had it.
            graph_ids = hood["chunk_ids"]
            if allowed is not None:
                graph_ids = [c for c in graph_ids if c in allowed]
            graph_hits = [{"chunk_id": cid, "score": 1.0, "source": "graph"}
                          for cid in graph_ids[:policy.sparse_k]]
            trace.stage("graph_search", time.perf_counter() - t0,
                        {"hits": len(graph_hits), "edges": len(hood["edges"])})
            if graph_hits:
                legs.append(graph_hits)

        # fusion
        t0 = time.perf_counter()
        fused = rrf_fuse(legs)[:policy.fused_k]
        hydrated = self.hydrate(fused)
        if allowed is not None:
            # Fail CLOSED. This previously ended in `or hydrated`, which
            # restored the unfiltered list whenever the filter emptied it —
            # a deliberate fail-open on the §60 isolation boundary. Returning
            # nothing for a project with no matching evidence is the correct
            # answer; the verifier then reports INSUFFICIENT.
            hydrated = [h for h in hydrated if h["chunk_id"] in allowed]
        trace.stage("fusion", time.perf_counter() - t0, {"fused": len(hydrated)})

        # Lexical tie-break (DEEP/FULLSWING). NOT a reranker: it is a cheap
        # term-overlap nudge, and calling it "rerank" invited readers to assume
        # cross-encoder reranking. A real reranker goes here only if the
        # benchmark proves the latency is paid back in accuracy (§37).
        if policy.rerank and hydrated:
            t0 = time.perf_counter()
            hydrated = _lexical_tiebreak(query, hydrated)
            trace.stage("lexical_tiebreak", time.perf_counter() - t0)

        return hydrated[:policy.evidence_k]


def _lexical_tiebreak(query: str, hits: list[dict]) -> list[dict]:
    r"""Cheap deterministic nudge: fraction of distinct query terms appearing as
    WHOLE WORDS in the chunk, used to break near-ties in the fused score.

    Two corrections over the previous version: terms are tokenized with \w+
    rather than split on whitespace (so "amount?" and "Dawson," could never
    match anything), and matching is word-boundary rather than substring (so
    "cost" no longer matches "costume").
    """
    # Round-3 reviewer R3-10: this had its own private tokenizer with no
    # stopword list, so DEEP/FULLSWING ranking was nudged by "the", "and",
    # "for" — a third definition of "word" in a system whose worst grounding
    # defect came from having two. There is now one, in terms.py.
    from .terms import content_terms
    qterms = content_terms(query)
    if not qterms:
        return hits
    rescored = []
    for h in hits:
        words = content_terms(h["text"])
        overlap = len(qterms & words) / len(qterms)
        rescored.append({**h, "lexical_overlap": round(overlap, 3),
                         "score": h["score"] * (1.0 + overlap)})
    return sorted(rescored, key=lambda x: -x["score"])
