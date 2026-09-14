r"""Regression tests for the round-2 reviewer's new findings (N3, N5–N9, N11).

Each asserts the defect the reviewer demonstrated, so reverting the
corresponding fix turns it red. N1/N2/N4 live with their subject matter in
test_safety_breach.py and test_isolation_regressions.py.
"""

import json
import time
from pathlib import Path

import pytest

from alirag import reviewer
from alirag.bench import BenchmarkError, run_retrieval_bench
from alirag.config import Config, EmbedConfig
from alirag.verify import verify


def _ev(**over):
    e = {"chunk_id": 1, "text": "Final claim amount RM50,569.30 as certified "
                                "for the softscape works.",
         "filename": "claim.pdf", "project": "Dawson", "revision": "R01",
         "superseded_by": None, "sources": ["sparse", "dense"]}
    e.update(over)
    return e


QUERY = "final claim amount certified softscape"


# ---------------------------------------------------------------- N3
def test_unknown_project_evidence_is_disclosed_not_silently_merged():
    """Round-2 reviewer N3: the project set was built with `!= "UNKNOWN"`, so
    an unattributed document merged with a named project and raised no flag.
    On this corpus 43,897 of ~45,000 files are UNKNOWN — the common case."""
    ev = [_ev(chunk_id=1, project="Dawson"),
          _ev(chunk_id=2, project="UNKNOWN", filename="scan_0421.pdf",
              text="Final claim amount RM99,111.22 as certified softscape.")]
    v = verify(ev, query=QUERY)
    assert any("no known project" in f.lower() for f in v.flags), v.flags
    assert "scan_0421.pdf" in " ".join(v.flags)
    assert v.status != "SUPPORTED", \
        "evidence that cannot be attributed must not read as fully supported"


def test_all_unknown_project_evidence_is_not_supported():
    """Every citation unattributed used to yield SUPPORTED with no flags.

    Updated for round 4: QUERY asks for a claim amount, and D-20's lenient
    disclosure is withdrawn, so an all-unattributable evidence set for a
    figure now escalates rather than answering with a footnote (N4-7)."""
    ev = [_ev(chunk_id=1, project="UNKNOWN", filename="a.pdf"),
          _ev(chunk_id=2, project=None, filename="b.pdf")]
    v = verify(ev, query=QUERY)
    assert v.status == "AMBIGUOUS_PROJECT", v.status
    assert any("no known project" in f.lower() for f in v.flags), v.flags


def test_known_project_evidence_still_reads_as_supported():
    """The disclosure must not make every answer PARTIAL."""
    v = verify([_ev(chunk_id=1), _ev(chunk_id=2, filename="claim2.pdf")],
               query=QUERY)
    assert v.status == "SUPPORTED", (v.status, v.flags)


# ---------------------------------------------------------------- N5
def test_bench_refuses_duplicate_questions(ingested, tmp_path: Path):
    """25 copies of one question satisfied every honesty gate: n>=20 for
    'meaningful' percentiles, recall 1.0, wrong-project 0.0. A percentile over
    repeats of one query measures the cache, not the workload."""
    cfg, _ = ingested
    q = {"q": "which document is the LAI-003 turf instruction?",
         "expect_file": "LAI-003 turf instruction.txt",
         "project": "Dawson", "reviewed": True}
    qf = tmp_path / "dupes.jsonl"
    qf.write_text("\n".join(json.dumps(q) for _ in range(25)), encoding="utf-8")
    with pytest.raises(BenchmarkError) as e:
        run_retrieval_bench(cfg, qf, use_llm=False)
    assert "more than once" in str(e.value)


def test_bench_refuses_too_few_distinct_questions(ingested, tmp_path: Path):
    cfg, _ = ingested
    from conftest import BENCH_QUESTIONS
    qf = tmp_path / "few.jsonl"
    qf.write_text("\n".join(json.dumps(q) for q in BENCH_QUESTIONS[:2]),
                  encoding="utf-8")
    with pytest.raises(BenchmarkError) as e:
        run_retrieval_bench(cfg, qf, use_llm=False)
    assert "distinct" in str(e.value)


# ---------------------------------------------------------------- N6 / N7
def _cfg(tmp_path: Path) -> Config:
    return Config(workspace=str(tmp_path / "ALI_RAG"), source_roots=[str(tmp_path)],
                  embed=EmbedConfig(provider="hash", dim=256))


def _report(**over) -> dict:
    rep = {"questions": 6, "recall": {"@5": 0.8, "@10": 0.9}, "mrr": 0.7,
           "wrong_project_rate": 0.0, "wrong_project_measured": 6,
           "modes_run": ["FAST"], "citation_page_accuracy": 0.9,
           "generated_at": "2026-08-17 10:00:00",
           "latency_ms": {"p95": 400, "n": 30, "percentiles_meaningful": True}}
    rep.update(over)
    return rep


def _write(cfg: Config, name: str, rep: dict, stamp: int | None = None):
    p = cfg.dir("benchmark") / f"{name}_{stamp or int(time.time())}.json"
    p.write_text(json.dumps(rep), encoding="utf-8")
    return p


def test_quality_gates_require_a_minimum_sample(tmp_path: Path):
    """Round-2 reviewer N6: a ONE-question report with recall@10=1.0 and
    citation accuracy 1.0 passed both gates. The per-mode gates required 5
    questions; these two required none."""
    cfg = _cfg(tmp_path)
    _write(cfg, "report_fast", _report(
        questions=1, recall={"@5": 1.0, "@10": 1.0}, mrr=1.0,
        citation_page_accuracy=1.0, wrong_project_measured=1))
    rep = reviewer.audit(cfg)
    assert rep["items"]["RETRIEVAL_QUALITY"]["status"] == "FAIL"
    assert rep["items"]["CITATIONS"]["status"] == "FAIL"


def test_quality_gates_ignore_layer_comparison_artifacts(tmp_path: Path):
    """Round-2 reviewer N7: `_latest("report_*.json")` also matched the
    degraded-configuration reports written by compare_layers, and picked them
    lexicographically — so a poor recent FAST run was masked by an older,
    alphabetically-later hybrid_graph artifact."""
    cfg = _cfg(tmp_path)
    _write(cfg, "report_fast", _report(
        recall={"@5": 0.1, "@10": 0.1}, mrr=0.1, citation_page_accuracy=0.1,
        generated_at="2026-08-17 09:00:00"), stamp=2000000000)
    # The layer report is BOTH alphabetically later AND more recent, so only
    # excluding degraded-configuration runs by kind can keep it out. It is not
    # a rival measurement of quality: compare_layers exists to run the stack
    # with legs switched off.
    _write(cfg, "report_hybrid_graph", _report(
        recall={"@5": 0.9, "@10": 0.9}, mrr=0.9, citation_page_accuracy=0.95,
        generated_at="2026-08-17 20:00:00"), stamp=1000000000)
    rep = reviewer.audit(cfg)
    assert rep["items"]["RETRIEVAL_QUALITY"]["status"] == "FAIL", \
        "a layer-comparison artifact masked the real FAST result"
    assert rep["items"]["CITATIONS"]["status"] == "FAIL"
    assert "hybrid_graph" not in (rep["items"]["RETRIEVAL_QUALITY"]["evidence"] or "")


def test_latest_picks_the_most_recent_not_the_last_alphabetically(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _write(cfg, "report_fast", _report(generated_at="2026-01-01 00:00:00"),
           stamp=9999999999)                       # sorts last by NAME
    newest = _write(cfg, "report_fast", _report(generated_at="2026-08-17 23:00:00"),
                    stamp=1111111111)              # sorts first by name, newest by time
    assert reviewer._latest("report_fast*.json", cfg.dir("benchmark")) == newest


# ---------------------------------------------------------------- N9
def test_volatile_patterns_are_reachable_from_config_and_reported(tmp_path: Path):
    """Round-2 reviewer N9: `allow_volatile()` existed but nothing outside the
    tests could reach it, so on the target machine — where a live service
    rewrites its own logs under a source root — `safety verify` could only ever
    report pass:false. A gate that cannot be satisfied honestly invites being
    satisfied dishonestly. The declaration must also appear in the artifact, so
    the excuse itself can be reviewed."""
    from alirag.cli import main
    src = tmp_path / "sources"
    (src / "hermes").mkdir(parents=True)
    doc = src / "spec.txt"
    doc.write_text("root barrier 50mm", encoding="utf-8")
    log = src / "hermes" / "beat.log"
    log.write_text("tick", encoding="utf-8")

    cfg = Config(workspace=str(tmp_path / "ALI_RAG"), source_roots=[str(src)],
                 embed=EmbedConfig(provider="hash", dim=256))
    # a volatile declaration may only point inside a directory the
    # operator already excluded from indexing (round-4 N4-6)
    cfg.ingest.exclude_dirs = tuple(cfg.ingest.exclude_dirs) + ("hermes",)
    cfg_path = cfg.dir("config") / "config.yaml"
    cfg.save(cfg_path)

    main(["--config", str(cfg_path), "safety", "snapshot"])
    log.write_text("tick tock tick", encoding="utf-8")   # live service writes
    main(["--config", str(cfg_path), "safety", "verify"])

    rep = json.loads(sorted(cfg.dir("reports").glob("safety_verify_*.json"))[-1]
                     .read_text(encoding="utf-8"))
    assert rep["pass"] is True, (rep["unexplained_modified"], rep["volatile_patterns"])
    assert rep["excluded_dirs"], "the declaration in force must be on the artifact"
    # Round-6 R6-1: the walk covers the excluded directory too. Its change is
    # CLASSIFIED, not omitted — reported and counted, without failing §84.
    assert rep["verdict"] == "PASS_WITH_EXCLUSIONS", rep["verdict"]
    assert any("beat.log" in p for p in rep["excluded_dir_modified"])
    assert any("beat.log" in p for p in rep["modified"]), "it must still be SEEN"


def test_a_document_change_is_never_excused_by_an_exclusion(tmp_path: Path):
    """The escape hatch must not become a way to pass §84 while a real
    document is edited."""
    from alirag.safety import SafetyGuard
    src = tmp_path / "sources"
    src.mkdir()
    doc = src / "tender.txt"
    doc.write_text("original", encoding="utf-8")
    guard = SafetyGuard([str(src)], str(tmp_path / "ws"),
                        excluded_dirs=("hermes",))
    snap = tmp_path / "ws" / "snap.jsonl"
    guard.snapshot(snap)
    doc.write_text("edited by something else", encoding="utf-8")
    res = guard.verify_snapshot(snap)
    assert res["pass"] is False
    assert any("tender.txt" in p for p in res["unexplained_modified"])


def test_audit_fails_data_safety_when_the_snapshot_has_no_hashes(tmp_path: Path):
    """A hashless snapshot cannot see a same-size edit or a rename, so a pass
    from one certifies nothing (§84)."""
    cfg = _cfg(tmp_path)
    (cfg.dir("reports") / "safety_verify_1.json").write_text(
        json.dumps({"pass": True, "hashed": False, "volatile_patterns": []}),
        encoding="utf-8")
    rep = reviewer.audit(cfg)
    assert rep["items"]["DATA_SAFETY"]["status"] == "FAIL"
    assert "no content hashes" in rep["items"]["DATA_SAFETY"]["detail"]


def test_audit_surfaces_excluded_dir_changes_next_to_the_verdict(tmp_path: Path):
    """A pass carried by exclusions is not a clean run, and the reviewer must
    see that without opening the artifact — an absent number reads as clean."""
    cfg = _cfg(tmp_path)
    (cfg.dir("reports") / "safety_verify_1.json").write_text(json.dumps({
        "pass": True, "hashed": True, "verdict": "PASS_WITH_EXCLUSIONS",
        "files_before": 12, "excluded_dirs": ["hermes", "sci_ai_library"],
        "excluded_dir_modified": ["E:/x/hermes/beat.log"],
        "excluded_dir_deleted": []}), encoding="utf-8")
    detail = reviewer.audit(cfg)["items"]["DATA_SAFETY"]["detail"]
    assert "PASS_WITH_EXCLUSIONS" in detail
    assert "1 change(s) inside excluded dirs" in detail
    assert "hermes" in detail


# ---------------------------------------------------------------- N11
def test_explain_does_not_leak_the_cache_fingerprint(ingested):
    """§53: /query stripped it, /explain did not."""
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from alirag.api import create_app
    cfg, _ = ingested
    with TestClient(create_app(cfg)) as c:
        e = c.post("/explain", json={"query": "find LAI-003",
                                     "use_llm": False}).json()
    assert "_fingerprint" not in e


# ---------------------------------------------------------------- N8
def test_fullswing_second_pass_evidence_is_verified(ingested):
    """Round-2 reviewer N8: the FULLSWING second pass extended verdict.kept
    AFTER verify() returned, so its results were cited and packed into the
    prompt having passed no project check, no superseded disclosure and no
    conflict surfacing. The checks ran on a strict subset of the evidence
    actually used."""
    from alirag.answer import Engine
    cfg, mf = ingested
    eng = Engine(cfg)

    # Both chunks must be genuinely relevant to the query, or the relevance
    # floor drops them and the project check never gets to run — the test would
    # then pass for the wrong reason.
    dawson = mf.con.execute(
        "SELECT c.chunk_id FROM chunks c JOIN files f ON f.file_id=c.file_id "
        "WHERE f.project='Dawson' AND c.text LIKE '%claim amount%' LIMIT 1").fetchone()[0]
    meridian = mf.con.execute(
        "SELECT c.chunk_id FROM chunks c JOIN files f ON f.file_id=c.file_id "
        "WHERE f.project='Meridian' AND c.text LIKE '%claim amount%' LIMIT 1").fetchone()[0]

    real_retrieve = eng.retriever.retrieve
    calls = {"n": 0}

    def thin_then_foreign(query, policy, trace, **kw):
        """First pass returns one Dawson chunk (thin, so the second pass
        fires); the second pass surfaces a Meridian chunk — exactly what an
        unscoped seeded re-query does when the seed is a shared entity."""
        calls["n"] += 1
        hydrate = eng.retriever.hydrate
        if calls["n"] == 1:
            return hydrate([{"chunk_id": dawson, "score": 1.0, "source": "dense"}])
        return hydrate([{"chunk_id": meridian, "score": 1.0, "source": "dense"}])

    eng.retriever.retrieve = thin_then_foreign
    try:
        resp = eng.query(
            "fullswing: reconstruct the complete history of the final "
            "claim amount certified", use_llm=False, use_cache=False)
    finally:
        eng.retriever.retrieve = real_retrieve
        eng.close()

    # None of the following means anything unless the second pass actually
    # fired and its evidence actually reached the citations.
    assert calls["n"] == 2, "the second pass did not fire"
    projects = {s["project"] for s in resp["sources"]}
    assert projects == {"Dawson", "Meridian"}, \
        f"second-pass evidence never reached the citations: {projects}"

    # It did reach them -> the verifier must have seen the COMBINED set.
    # Assert the ISOLATION check specifically. "project" appears in the
    # unattributed-evidence flag too, so a loose substring match here could be
    # satisfied by a different check entirely.
    assert resp["evidence_status"] == "AMBIGUOUS_PROJECT" or \
        any("spans multiple projects" in f or "cross-project" in f
            for f in resp["verifier_flags"]), \
        (f"second-pass evidence from {projects} was cited without the §60 "
         f"check: status={resp['evidence_status']} flags={resp['verifier_flags']}")
