r"""Regression tests for the round-5 reviewer's findings.

The wiring tests here assert BEHAVIOUR that changes when the plumbing is cut,
not that a helper returns something when called directly. Round 4's "end to
end" tests called `Engine._term_stats()` themselves, so severing the call in
`Engine.query` left the whole suite green — the defect they were written to
close, re-created one layer up.
"""

import json
from pathlib import Path

import pytest

from alirag import reviewer
from alirag.answer import Engine
from alirag.config import Config, EmbedConfig
from alirag.inventory import reinfer_metadata
from alirag.manifest import Manifest
from alirag.safety import SafetyGuard
from alirag.sparse import SparseIndex
from alirag.verify import verify


# ------------------------------------------------------------------ F5-1
TITLE_BLOCK = {"chunk_id": 1,
               "text": "DAWSON MERIDIAN TOWERS / Drawing title block. "
                       "Sheet 12 of 40. Scale 1:100.",
               "filename": "titleblock L-900.txt", "project": "Dawson",
               "revision": "R01", "superseded_by": None, "sources": ["dense"]}


@pytest.mark.parametrize("q", [
    "In the Dawson Meridian Towers, what is the final claim amount?",
    "what is the final claim amount for Meridian Towers Dawson?",
    "final claim amount, dawson meridian towers?",
    "MERIDIAN TOWERS DAWSON final claim amount",
])
def test_project_name_alone_is_never_evidence_whatever_the_word_order(q):
    """F5-1: `_project_hint` fires only on a verbatim match and the phrase
    strip ran only when a hint was set, so REORDERING the project name left the
    name itself counting as discriminating evidence. A drawing title block
    containing nothing but the project name and a sheet number came back
    SUPPORTED for a money question. Whether the floor held depended on the word
    order of the question."""
    v = verify([TITLE_BLOCK], query=q,
               known_projects=["Dawson Meridian Towers", "Meridian Towers"])
    assert v.status == "INSUFFICIENT", (q, v.status, v.flags)


def test_a_real_answer_is_not_lost_when_it_shares_the_project_name():
    """The rule must not become N4-5 again: project terms are not subtracted,
    they simply cannot be the ONLY thing an item is admitted on."""
    ev = [{"chunk_id": 1,
           "text": "Dawson Meridian Towers: the final claim amount certified "
                   "is RM50,569.30 under interim certificate 11.",
           "filename": "claim.pdf", "project": "Dawson", "revision": "R01",
           "superseded_by": None, "sources": ["dense"]}]
    v = verify(ev, query="what is the final claim amount for Meridian Towers Dawson?",
               known_projects=["Dawson Meridian Towers"])
    assert v.status != "INSUFFICIENT", (v.status, v.flags)


# ------------------------------------------------------------------ F5-2
def test_exact_id_leg_finds_a_code_inside_a_longer_filename(tmp_path: Path):
    """F5-2: `code_variants()` was added to the VERIFIER only. The ids table
    still stored `L201REVB`, so `search_ids` looked up `L201` and found
    nothing — the §9 exact path was dead for the majority of real drawing
    filenames, leaving FAST to hope BM25 ranked the right sheet into the top 20
    across 662k files."""
    idx = SparseIndex(tmp_path / "sparse.sqlite")
    idx.index_chunks([
        {"chunk_id": 1, "file_id": 1, "text": "Planting layout for Zone B.",
         "filename": "L-201-RevB.pdf", "project": "Dawson"},
        {"chunk_id": 2, "file_id": 2, "text": "Setting out plan.",
         "filename": "DWG-L-204-R03.pdf", "project": "Dawson"},
    ])
    hits = idx.search_ids("find L-201", k=10)
    assert [h["chunk_id"] for h in hits] == [1], hits
    assert [h["chunk_id"] for h in idx.search_ids("L-204", k=10)] == [2]
    idx.close()


def test_named_document_is_answered_via_the_exact_leg_at_scale(big_ingested,
                                                               tmp_path: Path):
    """At scale, and asserting the leg that answered — a one-document corpus
    proves nothing, because BM25 cannot miss."""
    from alirag.ingest import Ingestor
    from alirag.inventory import scan
    cfg, mf = big_ingested
    src = Path(cfg.source_roots[0])
    (src / "L-201-RevB.txt").write_text(
        "Planting layout for Zone B. Rain trees at 8m centres.", encoding="utf-8")
    scan(cfg, SafetyGuard(cfg.source_roots, cfg.workspace), mf, progress_every=0)
    ing = Ingestor(cfg, mf=mf)
    ing.run()

    eng = Engine(cfg)
    resp = eng.query("find L-201", use_llm=False, use_cache=False)
    eng.close()
    assert resp["evidence_status"] != "INSUFFICIENT", resp["verifier_flags"]
    hit = next((s for s in resp["sources"] if "L-201" in s["file"]), None)
    assert hit is not None, [s["file"] for s in resp["sources"]][:5]
    assert "exact" in hit["retrievers"], hit["retrievers"]


# ------------------------------------------------------------------ F5-3
def test_shipped_config_can_produce_a_clean_safety_pass(tmp_path: Path):
    """F5-3: the shipped config declares */hermes/* and */sci_ai_library/*
    volatile because those services rewrite state continuously. The walk
    covered them, so every run was PASS_WITH_EXCUSES — which the round-4 audit
    gate then refused. The §84 gate was unsatisfiable as shipped, and an
    unsatisfiable gate is one the operator learns to ignore."""
    from alirag.cli import main
    src = tmp_path / "sources"
    (src / "hermes").mkdir(parents=True)
    (src / "Dawson").mkdir()
    (src / "Dawson" / "tender.txt").write_text("terms", encoding="utf-8")
    (src / "hermes" / "beat.log").write_text("tick", encoding="utf-8")

    cfg = Config(workspace=str(tmp_path / "ALI_RAG"), source_roots=[str(src)],
                 embed=EmbedConfig(provider="hash", dim=256))
    cfg.ingest.exclude_dirs = tuple(cfg.ingest.exclude_dirs) + ("hermes",)
    cfg.volatile_patterns = ["*/hermes/*"]
    cfg_path = cfg.dir("config") / "config.yaml"
    cfg.save(cfg_path)

    main(["--config", str(cfg_path), "safety", "snapshot"])
    (src / "hermes" / "beat.log").write_text("tick tock tick", encoding="utf-8")
    main(["--config", str(cfg_path), "safety", "verify"])

    rep = json.loads(sorted(cfg.dir("reports").glob("safety_verify_*.json"))[-1]
                     .read_text(encoding="utf-8"))
    assert rep["verdict"] == "PASS", rep
    assert reviewer.audit(cfg)["items"]["DATA_SAFETY"]["status"] == "PASS"


def test_a_document_change_still_fails_with_exclusions_in_force(tmp_path: Path):
    """Excluding service directories must not blunt the actual guarantee."""
    src = tmp_path / "sources"
    (src / "hermes").mkdir(parents=True)
    (src / "tender.txt").write_text("original", encoding="utf-8")
    guard = SafetyGuard([str(src)], str(tmp_path / "ws"),
                        excluded_dirs=("hermes",))
    snap = tmp_path / "ws" / "snap.jsonl"
    guard.snapshot(snap)
    (src / "tender.txt").write_text("clobbered", encoding="utf-8")
    res = guard.verify_snapshot(snap)
    assert res["pass"] is False and res["verdict"] == "FAIL"


# ------------------------------------------------------------------ F5-4
def test_volatile_declaration_is_refused_when_nothing_is_excluded():
    """F5-4: three of the four SafetyGuard constructions omitted
    `excluded_dirs`, and the only thing protecting them was this refusal —
    which had no test. Mutating it to `if False:` left 205/205 green."""
    guard = SafetyGuard(["/tmp/x"], "/tmp/ws")          # no excluded dirs
    with pytest.raises(Exception) as e:
        guard.allow_volatile(["*/hermes/*"])
    assert "excluded" in str(e.value).lower()


def test_every_guard_construction_passes_excluded_dirs():
    """Plumbing, asserted rather than assumed."""
    import re as _re
    for mod in ("cli", "api", "ingest"):
        text = (Path("v3/alirag") / f"{mod}.py").read_text(encoding="utf-8")
        for call in _re.findall(r"SafetyGuard\((?:[^()]|\([^()]*\))*\)", text):
            assert "excluded_dirs" in call, f"{mod}.py: {call}"


# ------------------------------------------------------------------ F5-5
UNATTRIBUTED_MONEY = [
    {"chunk_id": i, "text": f"Interim payment of RM{i}2,400.00 was released "
                            "for the zoysia boulevard turf works.",
     "filename": f"scan_{i}.pdf", "project": "UNKNOWN", "revision": "R01",
     "superseded_by": None, "sources": ["dense"]} for i in (1, 2)]


@pytest.mark.parametrize("q", [
    "what is the final claim amount for the zoysia boulevard turf?",
    "how much was billed for the zoysia boulevard turf?",
    "what is the unpaid portion for the zoysia boulevard turf?",
    "what is the shortfall on the zoysia boulevard turf?",
    "is the zoysia boulevard turf done yet?",
    "how long do we have for the zoysia boulevard turf?",
])
def test_escalation_keys_on_the_evidence_not_the_phrasing(q):
    """F5-5: the same unattributable money chunk escalated for one phrasing and
    merely disclosed for another. The verifier already knows the evidence is
    monetary — MONEY_RE fires on it to build the conflict list — so keying on
    that is a property of the material, which no rewording can evade."""
    v = verify(UNATTRIBUTED_MONEY, query=q)
    assert v.status == "AMBIGUOUS_PROJECT", (q, v.status, v.flags)


def test_non_monetary_unattributed_evidence_is_not_over_escalated():
    """The evidence trigger must not swallow everything: a chunk with no
    figure and no date, asked a non-sensitive question, still just discloses."""
    ev = [{"chunk_id": 1,
           "text": "Zoysia matrella turf is laid in the boulevard planting beds.",
           "filename": "note.pdf", "project": "UNKNOWN", "revision": "R01",
           "superseded_by": None, "sources": ["dense"]}]
    v = verify(ev, query="which turf species is used in the boulevard beds?")
    assert v.status == "PARTIAL", (v.status, v.flags)


# ------------------------------------------------------------------ F5-6
def test_document_frequencies_actually_reach_the_verifier(big_ingested):
    """F5-6, the headline failure of round 4's own fix.

    Round 4 added three "end to end" tests that all called `_term_stats()`
    directly, so cutting the call inside `Engine.query` left 205/205 green.
    This test asserts a BEHAVIOUR that only holds when the wiring is intact:
    'girth' appears in every document of this corpus, is NOT in the hand-written
    boilerplate list, and can therefore only be discarded by measurement. With
    the wiring cut, {girth, samanea} clears the floor and the answer is
    returned; with it intact, only {samanea} survives and the floor refuses.
    """
    cfg, _ = big_ingested
    eng = Engine(cfg)
    resp = eng.query("girth samanea", use_llm=False, use_cache=False)
    eng.close()
    assert resp["evidence_status"] == "INSUFFICIENT", \
        ("measured document frequencies did not reach the verifier",
         resp["evidence_status"], resp["verifier_flags"])


def test_unmeasured_weighting_is_disclosed_as_unmeasured(ingested):
    """The §88-style disclosure must not be able to claim measurement it never
    had: mutating `df_is_meaningful()` to `True` left the suite green."""
    cfg, _ = ingested                     # small corpus -> fallback list
    eng = Engine(cfg)
    resp = eng.query("what is the warranty period for the submersible pump?",
                     use_llm=False, use_cache=False)
    eng.close()
    assert resp["evidence_status"] == "INSUFFICIENT"
    assert any("fallback boilerplate list" in f for f in resp["verifier_flags"]), \
        resp["verifier_flags"]


# ------------------------------------------------------------------ F5-7
def test_reinference_does_not_forge_project_provenance(tmp_path: Path):
    """F5-7: the UPDATE omitted `project_source`, so a manually confirmed
    'content' attribution survived a re-infer that had just replaced the
    project with a folder guess. The citation then asserted the guess had been
    confirmed from document content — §4 violated by the column added to
    satisfy §4."""
    from alirag.inventory import scan
    src = tmp_path / "src" / "Dawson"
    src.mkdir(parents=True)
    (src / "spec.txt").write_text("root barrier", encoding="utf-8")
    cfg = Config(workspace=str(tmp_path / "ws"),
                 source_roots=[str(tmp_path / "src")],
                 embed=EmbedConfig(provider="hash", dim=256))
    mf = Manifest(cfg.manifest_db)
    scan(cfg, SafetyGuard(cfg.source_roots, cfg.workspace), mf, progress_every=0)

    # an operator confirms a different project from the document's contents
    mf.con.execute("UPDATE files SET project='Kepong', project_source='content'")
    mf.commit()

    reinfer_metadata(cfg, mf)
    row = mf.con.execute(
        "SELECT project, project_source FROM files").fetchone()
    mf.close()
    assert row["project"] == "Dawson", row["project"]
    assert row["project_source"] == "folder", \
        "a folder guess must not inherit 'content' provenance"


# ------------------------------------------------------------------ audit gates
def test_audit_rejects_a_high_wrong_project_rate():
    """Mutating the threshold to 1.1 — accepting a 100% wrong-project rate on
    the §60 gate — left the suite green."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        cfg = Config(workspace=str(Path(td) / "ws"), source_roots=[td],
                     embed=EmbedConfig(provider="hash", dim=256))
        (cfg.dir("benchmark") / "report_fast_1.json").write_text(json.dumps({
            "questions": 6, "recall": {"@5": 0.9, "@10": 0.9}, "mrr": 0.8,
            "wrong_project_rate": 0.9, "wrong_project_measured": 6,
            "modes_run": ["FAST"], "citation_page_accuracy": 0.9,
            "generated_at": "2026-08-17 10:00:00",
            "latency_ms": {"p95": 400, "n": 30, "percentiles_meaningful": True},
        }), encoding="utf-8")
        assert reviewer.audit(cfg)["items"]["FAST_MODE"]["status"] == "FAIL"


def test_project_unknown_pct_is_computed_not_asserted(ingested):
    """The metric D-20's withdrawal tells the reader to trust: hardcoding it to
    0.0 left the suite green."""
    _, mf = ingested
    mf.con.execute("UPDATE files SET project='UNKNOWN'")
    mf.commit()
    assert mf.stats()["project_unknown_pct"] == 100.0
