"""End-to-end pipeline: ingest -> retrieve -> verify -> respond (no LLM server;
use_llm=False exercises everything up to prompt construction)."""

from alirag.answer import Engine
from alirag.graph import Graph
from alirag.instrument import percentiles


def test_ingest_states_and_stats(ingested):
    cfg, mf = ingested
    stats = mf.stats()
    assert stats["state_INDEXED"] >= 5
    assert stats["chunks_total"] >= 5
    assert stats.get("state_FAILED", 0) == 0


def test_exact_id_query_fast_path(ingested):
    cfg, _ = ingested
    eng = Engine(cfg)
    resp = eng.query("find LAI-003", use_llm=False)
    assert resp["mode"] == "FAST"
    assert resp["evidence_status"] in ("SUPPORTED", "PARTIAL")
    files = [s["file"] for s in resp["sources"]]
    assert any("LAI-003" in f for f in files)
    eng.close()


def test_semantic_query_finds_species(ingested):
    cfg, _ = ingested
    eng = Engine(cfg)
    resp = eng.query("rain tree trunk diameter requirement", use_llm=False)
    files = " ".join(s["file"] for s in resp["sources"])
    assert "Tender" in files
    eng.close()


def test_project_isolation_wrong_project_trap(ingested):
    """§60: a Dawson-targeted query must not answer from Meridian evidence."""
    cfg, _ = ingested
    eng = Engine(cfg)
    resp = eng.query("what is the final claim amount for Dawson?", use_llm=False)
    projects = {s["project"] for s in resp["sources"]}
    assert projects <= {"Dawson"}, f"Meridian leaked into Dawson query: {projects}"
    eng.close()


def test_insufficient_evidence_honest(ingested):
    """§39: nonsense query -> INSUFFICIENT, no fabricated answer."""
    cfg, _ = ingested
    eng = Engine(cfg)
    resp = eng.query("cepat: quantum flux capacitor warranty schedule xyzzy",
                     use_llm=False)
    assert resp["evidence_status"] == "INSUFFICIENT"
    assert "insufficient evidence" in resp["answer"].lower()
    eng.close()


def test_superseded_revision_disclosed(ingested):
    cfg, _ = ingested
    eng = Engine(cfg)
    resp = eng.query("deep: what does the tender specify for root barrier?",
                     use_llm=False)
    sup_flags = [s for s in resp["sources"] if s["superseded"]]
    if sup_flags:  # R00 retrieved alongside R01 -> verifier must have flagged it
        assert any("superseded" in f.lower() for f in resp["verifier_flags"])
    # current revision must be cited first when both are present
    names = [s["file"] for s in resp["sources"]]
    if any("R00" in n for n in names) and any("R01" in n for n in names):
        assert names.index(next(n for n in names if "R01" in n)) < \
               names.index(next(n for n in names if "R00" in n))
    eng.close()


def test_conflict_surfaced_across_amounts(ingested):
    cfg, _ = ingested
    eng = Engine(cfg)
    resp = eng.query("deep: final claim amount certified", use_llm=False)
    # amounts differ across revisions/projects -> either conflict reported or
    # project filter reduced evidence to one amount; both are honest outcomes
    assert resp["evidence_status"] in ("SUPPORTED", "PARTIAL", "INSUFFICIENT")
    if len({s["project"] for s in resp["sources"]}) > 1:
        assert resp["conflicts"] or resp["verifier_flags"]
    eng.close()


def test_graph_built_with_provenance(ingested):
    cfg, _ = ingested
    g = Graph(cfg.graph_db)
    stats = g.stats()
    assert stats["nodes"] > 0 and stats["edges"] > 0
    # LAI-003 mentioned by the tender -> REFERENCES edge with chunk provenance
    hood = g.neighborhood(["LAI-003"], hops=1)
    assert hood["edges"], "no graph neighborhood for LAI-003"
    assert any(e["relation"] in ("REFERENCES", "BELONGS_TO") for e in hood["edges"])
    refs = [e for e in hood["edges"] if e["relation"] == "REFERENCES"]
    assert all(e["file_id"] is not None for e in refs), "edge without provenance"
    g.close()


def test_traces_recorded_and_percentiles(ingested):
    cfg, _ = ingested
    eng = Engine(cfg)
    for q in ("find LAI-003", "root barrier detail", "rain tree size"):
        eng.query(q, use_llm=False, use_cache=False)
    log = cfg.dir("query_history") / "query_traces.jsonl"
    assert log.exists()
    pct = percentiles(log)
    assert pct["queries"] >= 3
    assert "total_ms" in pct and pct["total_ms"]["p50"] > 0
    assert "sparse_search" in pct and "dense_search" in pct
    eng.close()


def test_query_cache_hit_and_invalidation(ingested):
    cfg, mf = ingested
    eng = Engine(cfg)
    r1 = eng.query("find LAI-003", use_llm=False)
    r2 = eng.query("find LAI-003", use_llm=False)
    assert not r1["cached"] and r2["cached"]
    # index fingerprint moves -> cache invalidated (§31)
    mf.con.execute("UPDATE files SET indexed_at=indexed_at+999 WHERE file_id=1")
    mf.commit()
    r3 = eng.query("find LAI-003", use_llm=False)
    assert not r3["cached"]
    eng.close()


def test_restart_persistence(ingested):
    """§85: fresh handles over persisted stores serve queries identically."""
    cfg, _ = ingested
    e1 = Engine(cfg)
    before = (e1.mf.stats(), e1.dense.count(), e1.graph.stats())
    r_before = e1.query("find LAI-003", use_llm=False, use_cache=False)
    e1.close()
    e2 = Engine(cfg)
    after = (e2.mf.stats(), e2.dense.count(), e2.graph.stats())
    r_after = e2.query("find LAI-003", use_llm=False, use_cache=False)
    assert before == after
    assert [s["file"] for s in r_before["sources"]] == \
           [s["file"] for s in r_after["sources"]]
    e2.close()
