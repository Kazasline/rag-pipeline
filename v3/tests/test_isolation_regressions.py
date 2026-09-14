r"""Regression tests for the §60 project-isolation fixes (round-1 finding F3).

The round-2 reviewer reverted each of these three fixes individually and the
whole suite stayed green — the fixes were real but unguarded, and the claim
that every finding had a failing-before test was false. The 5-file fixture
cannot reach any of these paths, so each test builds the specific condition:

  (a) retrieve.py — the project filter used to end in `or hydrated`, restoring
      the UNFILTERED list whenever filtering emptied it.
  (b) sparse.search_ids — the exact-ID leg carries RRF weight 2.0 and took no
      project scope, so a matching code in another project outranked everything.
  (c) inventory.reinfer_metadata — corrected the manifest but not the
      denormalized `project` column in FTS, so the sparse leg kept filtering on
      stale labels.

Each asserts the leak DIRECTLY, so reverting the corresponding fix turns it red.
"""

from pathlib import Path

import pytest

from alirag.config import Config, EmbedConfig
from alirag.instrument import Trace
from alirag.inventory import reinfer_metadata, scan
from alirag.manifest import Manifest
from alirag.retrieve import Retriever
from alirag.safety import SafetyGuard
from alirag.sparse import SparseIndex


@pytest.fixture()
def wired(ingested):
    """A Retriever over the ingested fixture, with real indexes."""
    from alirag.dense import make_dense
    from alirag.embed import make_embedder
    from alirag.graph import Graph
    cfg, mf = ingested
    sparse = SparseIndex(cfg.sparse_db)
    graph = Graph(cfg.graph_db)
    r = Retriever(cfg, mf, sparse, make_dense(cfg), graph, make_embedder(cfg))
    yield cfg, mf, r
    sparse.close()
    graph.close()


# ---------------------------------------------------------------- F3(a)
def test_project_filter_fails_closed_when_it_empties_the_result(wired):
    """`or hydrated` was a deliberate fail-open on the isolation boundary: a
    Dawson query whose fused window contained only Meridian chunks got the
    Meridian chunks back. Returning nothing is the correct answer — the
    verifier then reports INSUFFICIENT."""
    cfg, mf, r = wired

    meridian = [row[0] for row in mf.con.execute(
        "SELECT c.chunk_id FROM chunks c JOIN files f ON f.file_id=c.file_id "
        "WHERE f.project='Meridian'")]
    assert meridian, "fixture must contain Meridian chunks"

    class OnlyMeridian:
        """Every leg returns foreign chunks — the exact condition the fail-open
        branch was reached under."""
        def search(self, *a, **k):
            return [{"chunk_id": c, "score": 1.0, "source": "sparse"}
                    for c in meridian]

        def search_ids(self, *a, **k):
            return []

    r.sparse = OnlyMeridian()
    r.dense = type("D", (), {
        "search": lambda self, *a, **k: [
            {"chunk_id": c, "score": 1.0, "source": "dense"} for c in meridian]})()

    policy = cfg.policies["FAST"]
    out = r.retrieve("final claim amount", policy, Trace("q", "FAST"),
                     project="Dawson")
    leaked = [h for h in out if (h.get("project") or "") != "Dawson"]
    assert not leaked, f"Meridian evidence leaked into a Dawson-scoped query: {leaked}"
    assert out == [], "fail-closed means an empty result, not a fallback"


# ---------------------------------------------------------------- F3(b)
def test_exact_id_leg_is_scoped_to_the_project(ingested):
    """The exact leg carries the heaviest RRF weight (2.0). Unscoped, a code
    matching in another project outranks in-project evidence."""
    cfg, mf = ingested
    sparse = SparseIndex(cfg.sparse_db)

    dawson = {row[0] for row in mf.con.execute(
        "SELECT c.chunk_id FROM chunks c JOIN files f ON f.file_id=c.file_id "
        "WHERE f.project='Dawson'")}
    unscoped = sparse.search_ids("LAI-003", k=20)
    scoped = sparse.search_ids("LAI-003", k=20, allowed_chunks=dawson)

    assert unscoped, "fixture must produce exact hits for LAI-003"
    assert scoped, "scoping must not empty a leg that has in-project hits"
    assert all(h["chunk_id"] in dawson for h in scoped), \
        "exact-ID leg returned chunks outside the allowed project"

    # and scoping to a project with NO matching code returns nothing rather
    # than falling back to the unscoped list
    assert sparse.search_ids("LAI-003", k=20, allowed_chunks=set()) == []
    sparse.close()


# ---------------------------------------------------------------- F3(c)
def test_reinference_syncs_project_into_the_sparse_index(ingested):
    """The FTS table stores a denormalized `project` used by the sparse leg's
    filter. If re-inference corrects the manifest but not FTS, every
    project-scoped sparse search runs against stale labels."""
    cfg, mf = ingested
    sparse = SparseIndex(cfg.sparse_db)

    before = sparse.search("root barrier", k=10, project="Dawson")
    assert before, "fixture must have Dawson chunks matching 'root barrier'"

    # simulate the drift: FTS labels go stale relative to the manifest
    sparse.con.execute("UPDATE fts SET project='STALE'")
    sparse.con.commit()
    assert sparse.search("root barrier", k=10, project="Dawson") == [], \
        "precondition: stale FTS labels break the project-scoped sparse leg"
    sparse.close()

    reinfer_metadata(cfg, mf)

    sparse = SparseIndex(cfg.sparse_db)
    after = sparse.search("root barrier", k=10, project="Dawson")
    assert after, "re-inference corrected the manifest but left FTS stale"
    assert {h["chunk_id"] for h in after} == {h["chunk_id"] for h in before}
    sparse.close()


# ---------------------------------------------------------------- N4
def test_graph_leg_is_scoped_before_fusion(wired):
    """Round-2 reviewer N4: the graph leg had no project scope, so its hits
    occupied fused_k slots and were only dropped afterwards — the same
    filter-after-selection defect as F4, in the last leg that still had it."""
    cfg, mf, r = wired
    meridian = [row[0] for row in mf.con.execute(
        "SELECT c.chunk_id FROM chunks c JOIN files f ON f.file_id=c.file_id "
        "WHERE f.project='Meridian'")]
    dawson = [row[0] for row in mf.con.execute(
        "SELECT c.chunk_id FROM chunks c JOIN files f ON f.file_id=c.file_id "
        "WHERE f.project='Dawson'")]

    import dataclasses

    class FloodingGraph:
        """Returns foreign chunks first — at corpus scale this is what a
        cross-project entity (a shared supplier, a common detail code) does."""
        def neighborhood(self, seeds, hops=1, limit=200):
            return {"nodes": [], "edges": [],
                    "chunk_ids": meridian + dawson, "chunk_hops": {}}

    # The other legs rank the in-project chunk BELOW the foreign ones — the
    # realistic case, since the foreign documents are near-identical
    # boilerplate. With the graph leg unscoped it adds a third vote for the
    # foreign chunks, pushing the only Dawson chunk out of the fusion window;
    # the post-fusion filter then removes the foreign hits and returns nothing.
    ordered = meridian + dawson[:1]

    class Ranked:
        def search(self, *a, **k):
            return [{"chunk_id": c, "score": 1.0, "source": "sparse"}
                    for c in ordered]

        def search_ids(self, *a, **k):
            return []

    r.sparse = Ranked()
    r.dense = type("D", (), {
        "search": lambda self, *a, **k: [
            {"chunk_id": c, "score": 1.0, "source": "dense"} for c in ordered]})()
    r.graph = FloodingGraph()

    policy = dataclasses.replace(cfg.policies["DEEP"],
                                 fused_k=len(meridian), graph_hops=1)
    out = r.retrieve("root barrier detail", policy, Trace("q", "DEEP"),
                     project="Dawson")
    assert all((h.get("project") or "") == "Dawson" for h in out)
    assert out, ("in-project evidence was crowded out of the fusion window by "
                 "unscoped graph hits, then filtered away to nothing")
