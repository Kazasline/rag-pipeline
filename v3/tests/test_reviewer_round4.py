r"""Regression tests for the round-4 reviewer's findings.

The N4-8 tests run against `big_ingested`, a corpus above MIN_DOCS_FOR_DF, so
they exercise the document-frequency instrument that actually runs in
production. Before this fixture existed, no test in the suite did — and
disabling the DF plumbing entirely left all 179 green.
"""

import json
from pathlib import Path

import pytest

from alirag import reviewer
from alirag.answer import Engine
from alirag.config import Config, EmbedConfig
from alirag.safety import SafetyGuard, VolatilePatternRejected
from alirag.sparse import code_variants
from alirag.terms import MIN_DOCS_FOR_DF, discriminative_terms
from alirag.verify import verify


# ------------------------------------------------------------------ N4-8
def test_the_big_fixture_actually_exercises_measured_weighting(big_ingested):
    """Guard the guard: if this corpus ever drops below the threshold, the
    tests below silently stop testing the production instrument."""
    cfg, _ = big_ingested
    eng = Engine(cfg)
    df, total = eng._term_stats("samanea trunk girth specification")
    eng.close()
    assert total >= MIN_DOCS_FOR_DF, f"fixture too small: {total} chunks"
    assert df and df.get("samanea", 0) > 0


def test_measured_weighting_is_wired_end_to_end(big_ingested):
    """N4-8: replacing `_term_stats` with `return None, 0` — the engine never
    computing or passing document frequencies at all — left 179/179 green.
    The whole measured path was unguarded end to end."""
    cfg, _ = big_ingested
    eng = Engine(cfg)
    df, total = eng._term_stats("the contractor shall provide the works")
    eng.close()
    assert total >= MIN_DOCS_FOR_DF
    # boilerplate saturates this corpus, so measurement must see it as common
    assert df["contractor"] / total > 0.25
    # ...and the floor must therefore discard it
    assert discriminative_terms({"contractor", "samanea"}, df, total) == {"samanea"}


def test_boilerplate_is_refused_at_scale_even_when_rare(big_ingested):
    """N4-1 at production scale: a boilerplate word whose measured frequency
    happens to fall below MAX_DF_RATIO must NOT be restored as discriminating.
    Measurement may only make the floor stricter."""
    cfg, _ = big_ingested
    eng = Engine(cfg)
    df, total = eng._term_stats("drawings section")
    eng.close()
    rare_boilerplate = {t: 1 for t in ("drawing", "section", "clause")}
    assert discriminative_terms({"drawing", "section", "clause"},
                                {**df, **rare_boilerplate}, total) == set()


@pytest.mark.parametrize("q", [
    "Which contractor shall supply the pump and the generator?",
    "what locations are shown for the security cameras?",
])
def test_attack_queries_refused_at_scale(big_ingested, q):
    """The two queries the round-3 commit claimed as fixed, run against a
    corpus where the measured path is live — which is where the reviewer
    showed they came back SUPPORTED."""
    cfg, _ = big_ingested
    eng = Engine(cfg)
    resp = eng.query(q, use_llm=False, use_cache=False)
    eng.close()
    assert resp["evidence_status"] == "INSUFFICIENT", \
        f"{q!r} -> {resp['evidence_status']} citing " \
        f"{[s['file'] for s in resp['sources']][:3]}"


def test_real_questions_still_answered_at_scale(big_ingested):
    cfg, _ = big_ingested
    eng = Engine(cfg)
    resp = eng.query("what is planted in the boulevard area, samanea?",
                     use_llm=False, use_cache=False)
    eng.close()
    assert resp["evidence_status"] != "INSUFFICIENT", resp["verifier_flags"]


# ------------------------------------------------------------------ N4-3
def test_document_codes_match_inside_longer_filenames():
    """N4-3: `normalize_id("L-201-RevB")` is `L201REVB`, so `find L-201`
    returned INSUFFICIENT while the file sat in the nearest-match list.
    Revision-suffixed and prefix-qualified sheet names are the norm here."""
    for fn in ("L-201.pdf", "L-201-RevB.pdf", "L-201_R01.pdf",
               "DWG-L-201-R03.pdf", "Dawson-L-201-planting.pdf",
               "L-201 Zone B.pdf"):
        assert "L201" in code_variants(fn), fn


def test_naming_a_revision_suffixed_document_answers(tmp_path: Path):
    """End-to-end version of N4-3 on a filename the old rule refused."""
    from alirag.ingest import Ingestor
    from alirag.inventory import scan
    from alirag.manifest import Manifest
    src = tmp_path / "src" / "Dawson"
    src.mkdir(parents=True)
    (src / "L-201-RevB.txt").write_text(
        "Planting layout for Zone B. Rain trees at 8m centres.", encoding="utf-8")
    cfg = Config(workspace=str(tmp_path / "ws"), source_roots=[str(tmp_path / "src")],
                 embed=EmbedConfig(provider="hash", dim=256))
    cfg.dense.backend = "memmap"
    mf = Manifest(cfg.manifest_db)
    scan(cfg, SafetyGuard(cfg.source_roots, cfg.workspace), mf, progress_every=0)
    ing = Ingestor(cfg, mf=mf)
    ing.run()
    ing.close()

    eng = Engine(cfg)
    resp = eng.query("find L-201", use_llm=False, use_cache=False)
    eng.close()
    assert resp["evidence_status"] != "INSUFFICIENT", resp["verifier_flags"]
    assert any("L-201" in s["file"] for s in resp["sources"])


# ------------------------------------------------------------------ N4-4
def test_naming_a_file_does_not_certify_arbitrary_content():
    """N4-4: a filename code match bypassed the content floor for EVERY chunk
    of that file, unflagged — a scaffolding invoice inside L-201.pdf was
    returned SUPPORTED as evidence for a pump warranty."""
    ev = [{"chunk_id": 1, "text": "Invoice for scaffolding hire, March 2026.",
           "filename": "L-201.pdf", "project": "Dawson", "revision": "R01",
           "superseded_by": None, "sources": ["exact"]}]
    v = verify(ev, query="what is the pump warranty period on drawing L-201?")
    assert v.status != "SUPPORTED", (v.status, v.flags)
    assert any("not because its content answers" in f for f in v.flags), v.flags


# ------------------------------------------------------------------ N4-5
def test_project_label_is_excluded_as_a_phrase_not_term_by_term():
    """N4-5: subtracting every word of a descriptive project name destroyed
    real evidence — for project 'Pump Station Upgrade', the question 'what is
    the pump warranty period' lost 'pump' and a chunk literally containing the
    answer was refused."""
    ev = [{"chunk_id": 1,
           "text": "The pump warranty period is 24 months from practical completion.",
           "filename": "spec.pdf", "project": "Pump Warranty Programme",
           "revision": "R01", "superseded_by": None, "sources": ["dense"]}]
    # Term-by-term subtraction removes BOTH "pump" and "warranty" from the
    # question, leaving one term and refusing a chunk that literally states the
    # answer. The phrase "Pump Warranty Programme" never appears in the
    # question, so nothing should be stripped at all.
    v = verify(ev, query="what is the pump warranty period",
               project_hint="Pump Warranty Programme")
    assert v.status != "INSUFFICIENT", (v.status, v.flags)


# ------------------------------------------------------------------ N4-6
@pytest.mark.parametrize("pat", ["*/Dawson/*", "*/Tender/*", "*/sources/*",
                                 "*/Projects/*", "*"])
def test_volatile_patterns_naming_the_corpus_are_refused(pat):
    """N4-6: R3-7 refused only the two literal strings the round-3 reviewer
    cited. A directory-anchored pattern naming the CORPUS was accepted, and
    with it a tender could be rewritten and an instruction deleted while §84
    reported pass:True."""
    guard = SafetyGuard(["/tmp/x"], "/tmp/ws",
                        excluded_dirs=("hermes", "sci_ai_library"))
    with pytest.raises(VolatilePatternRejected):
        guard.allow_volatile([pat])


def test_excluded_dirs_are_the_only_declaration_mechanism():
    """N4-6 bounded the per-path allowlist; round 6 removed it. The directory
    declaration in config is now the single mechanism, and it is visible."""
    guard = SafetyGuard(["/tmp/x"], "/tmp/ws",
                        excluded_dirs=("hermes", "sci_ai_library"))
    assert guard.excluded_dirs == ("hermes", "sci_ai_library")
    with pytest.raises(VolatilePatternRejected):
        guard.allow_volatile(["*/hermes/*"])


def test_audit_refuses_to_certify_an_excused_safety_run(tmp_path: Path):
    """N4-6: `reviewer.audit` gated on `pass` and ignored `verdict`, so
    PASS_WITH_EXCUSES was decorative at the only place that consumed it."""
    cfg = Config(workspace=str(tmp_path / "ALI_RAG"), source_roots=[str(tmp_path)],
                 embed=EmbedConfig(provider="hash", dim=256))
    (cfg.dir("reports") / "safety_verify_1.json").write_text(json.dumps({
        "pass": True, "verdict": "PASS_WITH_EXCUSES", "hashed": True,
        "volatile_patterns": ["*/hermes/*"],
        "allowlisted_modified": ["E:/x/hermes/beat.log"],
        "allowlisted_deleted": []}), encoding="utf-8")
    assert reviewer.audit(cfg)["items"]["DATA_SAFETY"]["status"] == "FAIL"

    (cfg.dir("reports") / "safety_verify_2.json").write_text(json.dumps({
        "pass": True, "verdict": "PASS", "hashed": True,
        "volatile_patterns": [], "allowlisted_modified": [],
        "allowlisted_deleted": []}), encoding="utf-8")
    assert reviewer.audit(cfg)["items"]["DATA_SAFETY"]["status"] == "PASS"


# ------------------------------------------------------------------ N4-7
def test_all_unknown_evidence_escalates_for_a_figure():
    """N4-7: escalation required a KNOWN project to also be present, so the
    all-unattributable case — the worst one — never escalated."""
    ev = [{"chunk_id": i, "text": f"Final claim amount RM{i}9,111.22 certified.",
           "filename": f"scan_{i}.pdf", "project": "UNKNOWN", "revision": "R01",
           "superseded_by": None, "sources": ["dense"]} for i in (1, 2)]
    v = verify(ev, query="what is the final claim amount certified?")
    assert v.status == "AMBIGUOUS_PROJECT", (v.status, v.flags)


@pytest.mark.parametrize("q", [
    "how much did the contractor charge?",
    "what is the figure quoted?",
    "what did they agree to pay?",
    "how many days of delay were allowed?",
    "when do we have to finish?",
])
def test_sensitive_intent_covers_the_evasions(q):
    """N4-7: the regex missed ordinary phrasings of the same questions."""
    from alirag.verify import SENSITIVE_INTENT
    assert SENSITIVE_INTENT.search(q), q


# ------------------------------------------------------------------ N4-9
def test_citations_disclose_how_the_project_was_determined(ingested):
    """N4-9: `project_source` was recorded and documented as letting the reader
    tell a folder-derived guess from a confirmed attribution, but nothing ever
    read it — citations presented the guess as fact."""
    cfg, _ = ingested
    eng = Engine(cfg)
    resp = eng.query("find LAI-003", use_llm=False, use_cache=False)
    eng.close()
    assert resp["sources"]
    for s in resp["sources"]:
        assert s.get("project_source") in ("folder", "content", "manual",
                                           "UNKNOWN")


# ------------------------------------------------------------------ N4-10
def test_wrong_project_rate_excludes_unattributed_from_the_denominator(
        ingested, tmp_path: Path):
    """N4-10: `project in (expected, "UNKNOWN", None)` scored unattributed
    sources as CORRECT, so the metric excused the very population it exists to
    police."""
    from alirag.bench import run_retrieval_bench
    from conftest import write_questions
    cfg, mf = ingested
    mf.con.execute("UPDATE files SET project='UNKNOWN'")
    mf.commit()
    qf = write_questions(tmp_path / "q.jsonl")
    rep = run_retrieval_bench(cfg, qf, label="adhoc", use_llm=False)
    # Nothing is attributable, so nothing is MEASURABLE. The honest report is
    # "not measured", not a rate — scoring UNKNOWN as correct produced a
    # perfect 0.0, and scoring it as wrong produces a meaningless 1.0.
    assert rep["wrong_project_measured"] == 0, rep["wrong_project_measured"]
    assert rep["wrong_project_rate"] is None, rep["wrong_project_rate"]
    assert rep["unattributed_sources_in_top3"] > 0
    # ...and an unmeasured rate must not satisfy the reviewer's gate
    from alirag import reviewer
    (cfg.dir("benchmark") / "report_fast_9.json").write_text(
        json.dumps({**rep, "modes_run": ["FAST"], "label": "fast"}),
        encoding="utf-8")
    assert reviewer.audit(cfg)["items"]["FAST_MODE"]["status"] == "FAIL"
