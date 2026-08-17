r"""Regression tests for the round-3 reviewer's findings.

Every test here was run against the pre-fix code and observed to fail. The
three marked R3-12 exist because the reviewer's revert matrix found the
corresponding sub-fixes unguarded: reverting them alone left the suite green
because the floor's two halves were covering for each other.
"""

import json
from pathlib import Path

import pytest

from alirag import reviewer
from alirag.answer import Engine
from alirag.bench import BenchmarkError, run_retrieval_bench
from alirag.config import Config, EmbedConfig
from alirag.router import route
from alirag.safety import SafetyGuard, VolatilePatternRejected, hash_path
from alirag.sparse import is_document_code
from alirag.terms import content_terms, discriminative_terms
from alirag.verify import verify

KNOWN_PROJECTS = ["Dawson", "Meridian"]


# ------------------------------------------------------------------ R3-1
@pytest.mark.parametrize("q", [
    "compare the rain tree diameter with the turf spec in this project",
    "compare revision R00 and R01 of the tender for the project",
    "does any project document mention a defects liability period?",
    "compare the final claim amount stated for the project",
])
def test_ordinary_questions_do_not_consent_to_cross_project_merging(q):
    """R3-1: `\\bcompare\\b.*\\bprojects?\\b` (greedy) and `\\bany project\\b`
    set cross_project=True on ordinary questions. That single flag disables the
    §60 guard, so Dawson's and Meridian's claim amounts were merged into one
    prompt and labelled 'as asked' — by a user who asked nothing of the kind."""
    assert route(q, known_projects=KNOWN_PROJECTS).cross_project is False


@pytest.mark.parametrize("q", [
    "final claim amount across all projects",
    "list every project with an outstanding VO",
    "compare all projects by contract sum",
    "jumlah tuntutan untuk semua projek",
])
def test_explicit_cross_project_phrases_still_consent(q):
    """Narrowing must not close the door: an explicit instruction still works,
    otherwise the legitimate question becomes unaskable."""
    assert route(q, known_projects=KNOWN_PROJECTS).cross_project is True


def test_unconsented_multi_project_money_question_asks(ingested):
    cfg, _ = ingested
    eng = Engine(cfg)
    resp = eng.query("compare the final claim amount stated for the project",
                     use_llm=False, use_cache=False)
    eng.close()
    if len({s["project"] for s in resp["sources"]}) > 1:
        assert resp["evidence_status"] == "AMBIGUOUS_PROJECT", resp["verifier_flags"]
        assert "RM50,569.30" not in resp["answer"]
        assert "RM99,111.22" not in resp["answer"]


# ------------------------------------------------------------------ R3-2
def test_revision_and_short_tokens_are_not_document_codes():
    """R3-2: `harvest_ids` matches R01/D7/L2, which are revision and detail
    markers carried by nearly every sheet in the corpus. Treating them as
    document identifiers meant naming a revision named tens of thousands of
    files at once."""
    for tok in ("R01", "R00", "Rev A", "R1", "D7", "L2", "T12",
                # separated revision forms: these satisfy the length rule and
                # are excluded only by the revision rule itself
                "REV-01", "R-01", "Rev.02", "REV_03"):
        assert not is_document_code(tok), f"{tok} must not identify a document"
    for tok in ("LAI-003", "L-201", "KP-980ASPEN-CS", "NCR-12", "LAI003"):
        assert is_document_code(tok), f"{tok} must identify a document"


@pytest.mark.parametrize("q", [
    "What is the pump warranty period on Dawson drawing L-201?",
    "For Dawson, what is the mechanical ventilation rate in detail D-7?",
    "Dawson: what does revision R01 say about the fire pump test?",
])
def test_code_mention_in_body_does_not_certify_unrelated_content(ingested, q):
    """R3-2 end-to-end. The tender spec cross-refers to L-201 and R01 and says
    nothing about pumps or ventilation. A code MENTIONED in a body is a
    reference to a document; only the filename is the document's identity."""
    cfg, _ = ingested
    eng = Engine(cfg)
    resp = eng.query(q, use_llm=False, use_cache=False)
    eng.close()
    assert resp["evidence_status"] == "INSUFFICIENT", \
        f"{q!r} -> {resp['evidence_status']} citing " \
        f"{[s['file'] for s in resp['sources']]}"


def test_naming_a_document_still_answers(ingested):
    """The narrowing must not break the §9 exact path it exists to protect."""
    cfg, _ = ingested
    eng = Engine(cfg)
    for q in ("find LAI-003", "what does LAI-003 say?"):
        resp = eng.query(q, use_llm=False, use_cache=False)
        assert resp["evidence_status"] != "INSUFFICIENT", q
        assert any("LAI-003" in s["file"] for s in resp["sources"]), q
    eng.close()


# ------------------------------------------------------------------ R3-3
def _ev(cid, text, filename, project="Dawson", sources=("dense",)):
    return {"chunk_id": cid, "text": text, "filename": filename,
            "project": project, "revision": "R01", "superseded_by": None,
            "sources": list(sources)}


def test_floor_filters_per_item_instead_of_gating_the_set():
    """R3-3: `best_overlap` was a max and the code check an `any(...)`, so ONE
    qualifying item admitted the whole set. Unrelated chunks were then cited
    with full §40 provenance and packed into the prompt, having met no floor of
    their own."""
    ev = [
        _ev(1, "LAI-003 replaces the turf specification for Zone B.",
            "LAI-003 turf instruction.txt", sources=["exact"]),
        _ev(2, "Ficus microcarpa of minimum trunk diameter 100mm per MT-L-05.",
            "Landscape Spec.txt", project="Meridian"),
        _ev(3, "Invoice for scaffolding hire, March 2026.", "invoice_881.pdf"),
    ]
    v = verify(ev, query="what does LAI-003 say about the pump warranty?")
    kept_files = {e["filename"] for e in v.kept}
    assert kept_files == {"LAI-003 turf instruction.txt"}, kept_files
    assert any("relevance floor" in f for f in v.flags), v.flags


# ------------------------------------------------------------------ R3-4
def test_generic_domain_words_do_not_clear_the_floor(ingested):
    """R3-4: a flat count of 2 shared terms was cleared by {locations, shown}
    and by {contractor, shall, supply} — vocabulary that saturates every
    construction document in the corpus."""
    cfg, _ = ingested
    eng = Engine(cfg)
    for q in ("In the Dawson project, what locations are shown for the "
              "security cameras?",
              "Which contractor shall supply the pump and the generator?"):
        resp = eng.query(q, use_llm=False, use_cache=False)
        assert resp["evidence_status"] == "INSUFFICIENT", \
            f"{q!r} -> {resp['evidence_status']}"
    eng.close()


def test_measured_document_frequency_overrides_the_fallback_list():
    """The real instrument is corpus statistics; the word list is the fallback.
    A term that is rare in THIS corpus must count even if it looks generic."""
    shared = {"drawing", "samanea"}
    # fallback: "drawing" is boilerplate by the list
    assert discriminative_terms(shared) == {"samanea"}
    # measured: "drawing" is rare here, so it discriminates after all
    df = {"drawing": 5, "samanea": 400}
    assert discriminative_terms(shared, df, 1000) == {"drawing"}


def test_document_frequency_is_ignored_when_the_corpus_is_too_small():
    """A share needs a population: over 10 chunks, a term in 3 reads as 30%
    'common' when the corpus is simply too small to have boilerplate."""
    shared = {"samanea", "zoysia"}
    assert discriminative_terms(shared, {"samanea": 3, "zoysia": 1}, 10) == shared


def test_project_name_alone_is_not_evidence():
    """All three R3-2 attacks rode on {project name + one code-ish token}.
    Project scope is enforced by §60 isolation; counting it as topical overlap
    counts it twice."""
    assert discriminative_terms({"dawson", "samanea"}, exclude={"dawson"}) == {"samanea"}


def test_revision_tokens_are_not_discriminating_terms():
    assert discriminative_terms({"r01", "rev", "zoysia"}) == {"zoysia"}


# ------------------------------------------------------------------ R3-12
def test_content_terms_removes_stopwords():
    """R3-12: reverting the stopword removal alone left the suite green,
    because MIN_CONTENT_OVERLAP covered for it. Guard each half."""
    assert content_terms("the and for what yang dan") == set()
    assert content_terms("the rain tree diameter") == {"rain", "tree", "diameter"}


def test_a_single_shared_term_is_insufficient():
    """The other uncovered half: MIN_CONTENT_OVERLAP reverted to 1."""
    ev = [_ev(1, "Samanea saman rain trees along the boulevard.", "spec.pdf")]
    assert verify(ev, query="samanea warranty period").status == "INSUFFICIENT"
    assert verify(ev, query="samanea rain trees").status != "INSUFFICIENT"


def test_snapshot_without_hashes_reports_hashed_false(tmp_path: Path):
    """R3-12: only the reviewer-side consumption of `hashed` was tested, from a
    hand-written dict — never that verify_snapshot actually reports it."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("x", encoding="utf-8")
    guard = SafetyGuard([str(src)], str(tmp_path / "ws"))
    snap = tmp_path / "ws" / "snap.jsonl"
    guard.snapshot(snap, hash_files=False)
    assert guard.verify_snapshot(snap)["hashed"] is False
    guard.snapshot(snap, hash_files=True)
    assert guard.verify_snapshot(snap)["hashed"] is True


# ------------------------------------------------------------------ R3-5
def test_duplicate_questions_are_not_disguised_by_punctuation(ingested,
                                                              tmp_path: Path):
    """R3-5: the key was `" ".join(q.lower().split())`, so rotating trailing
    punctuation turned 25 copies into 25 'distinct' questions — clearing every
    gate the check exists to hold, percentiles_meaningful included."""
    cfg, _ = ingested
    # Every variant is textually UNIQUE, so the pre-fix key (whitespace-only
    # normalization) sees 25 distinct questions. Only punctuation-stripping
    # reveals them as one.
    qs = [{"q": "what trunk diameter is required for the rain trees"
                + "." * (i + 1),
           "expect_file": "Landscape Tender Spec R01.txt",
           "project": "Dawson", "reviewed": True} for i in range(25)]
    qf = tmp_path / "dupes.jsonl"
    qf.write_text("\n".join(json.dumps(q) for q in qs), encoding="utf-8")
    with pytest.raises(BenchmarkError) as e:
        run_retrieval_bench(cfg, qf, use_llm=False)
    assert "more than once" in str(e.value)


# ------------------------------------------------------------------ R3-6
def test_audit_cites_the_artifact_it_graded(tmp_path: Path):
    """R3-6: the report was chosen by recency but the cited path was 'last of
    fast/deep/fullswing present', so a reviewer opening the evidence found
    numbers contradicting the verdict — from the module whose whole purpose is
    to stop that."""
    cfg = Config(workspace=str(tmp_path / "ALI_RAG"), source_roots=[str(tmp_path)],
                 embed=EmbedConfig(provider="hash", dim=256))
    base = {"questions": 6, "wrong_project_rate": 0.0, "wrong_project_measured": 6,
            "latency_ms": {"p95": 400, "n": 30, "percentiles_meaningful": True}}
    (cfg.dir("benchmark") / "report_fast_1.json").write_text(json.dumps({
        **base, "modes_run": ["FAST"], "generated_at": "2026-08-17 12:00:00",
        "recall": {"@5": 1.0, "@10": 1.0}, "mrr": 1.0,
        "citation_page_accuracy": 1.0}), encoding="utf-8")
    (cfg.dir("benchmark") / "report_fullswing_1.json").write_text(json.dumps({
        **base, "modes_run": ["FULLSWING"], "generated_at": "2026-08-17 09:00:00",
        "recall": {"@5": 0.2, "@10": 0.2}, "mrr": 0.2,
        "citation_page_accuracy": 0.1}), encoding="utf-8")

    rep = reviewer.audit(cfg)
    for cat in ("RETRIEVAL_QUALITY", "CITATIONS"):
        cited = rep["items"][cat]["evidence"] or ""
        assert "report_fast" in cited, f"{cat} cited {cited}"
        # and the verdict must follow the artifact it cited
        assert rep["items"][cat]["status"] == "PASS"


# ------------------------------------------------------------------ R3-7
@pytest.mark.parametrize("pat", ["*", "**", "*/", "*.pdf", "*.docx", "**.xlsx"])
def test_overbroad_volatile_patterns_are_refused(pat):
    """R3-7: with `volatile_patterns: ["*"]` a source document was rewritten
    and another deleted, and verify_snapshot still returned pass:True. The
    escape hatch must not be usable against the corpus it protects."""
    guard = SafetyGuard(["/tmp/does-not-matter"], "/tmp/ws")
    with pytest.raises(VolatilePatternRejected):
        guard.allow_volatile([pat])


def test_directory_anchored_patterns_are_still_accepted():
    guard = SafetyGuard(["/tmp/does-not-matter"], "/tmp/ws")
    guard.allow_volatile(["*/hermes/*", "*/sci_ai_library/*", "*/logs/*.log"])
    assert len(guard.volatile_patterns) == 3


def test_excused_run_is_distinguishable_from_a_clean_one(tmp_path: Path):
    """A pass earned by an allowlist is not a clean run, and the machine-
    readable verdict must say so — not only a detail string nobody parses."""
    src = tmp_path / "src"
    (src / "hermes").mkdir(parents=True)
    (src / "tender.txt").write_text("original", encoding="utf-8")
    (src / "hermes" / "beat.log").write_text("tick", encoding="utf-8")
    guard = SafetyGuard([str(src)], str(tmp_path / "ws"))
    guard.allow_volatile(["*/hermes/*"])
    snap = tmp_path / "ws" / "snap.jsonl"
    guard.snapshot(snap)

    assert guard.verify_snapshot(snap)["verdict"] == "PASS"

    (src / "hermes" / "beat.log").write_text("tick tock", encoding="utf-8")
    res = guard.verify_snapshot(snap)
    assert res["pass"] is True
    assert res["verdict"] == "PASS_WITH_EXCUSES"

    (src / "tender.txt").write_text("edited by something else", encoding="utf-8")
    res = guard.verify_snapshot(snap)
    assert res["pass"] is False and res["verdict"] == "FAIL"
    assert any("tender.txt" in p for p in res["unexplained_modified"])


# ------------------------------------------------------------------ R3-8
def test_project_label_sync_failure_is_loud(ingested, monkeypatch):
    """R3-8: a bare `except Exception` returned a count, so an I/O error left
    the operator with a normal result dict, no error and a zero exit — while
    the project-scoped sparse leg was silently dead and F3(c) was back."""
    from alirag import inventory, sparse as sparse_mod
    cfg, mf = ingested

    real = sparse_mod.SparseIndex

    class BrokenIndex(real):
        """Writes fail the way a full or locked disk fails."""
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            outer = self

            class Con:
                def execute(self, sql, *args):
                    if sql.lstrip().upper().startswith("UPDATE"):
                        raise OSError("disk I/O error")
                    return outer._real_con.execute(sql, *args)

                def commit(self):
                    return outer._real_con.commit()

                def close(self):
                    return outer._real_con.close()

            self._real_con = self.con
            self.con = Con()

    monkeypatch.setattr(sparse_mod, "SparseIndex", BrokenIndex)
    with pytest.raises(inventory.SyncError) as e:
        inventory._sync_sparse_projects(cfg, mf)
    msg = str(e.value)
    assert "inconsistent" in msg or "stale" in msg, msg


# ------------------------------------------------------------------ N3 conditions
def test_sensitive_questions_escalate_unattributed_evidence():
    """Reviewer N3 condition 1: a PARTIAL on 'what locations are shown' is a
    different risk from a PARTIAL on 'what is the final claim amount'. Nobody
    reads a footnote as disqualifying a figure."""
    ev = [_ev(1, "Final claim amount RM50,569.30 as certified.", "claim.pdf",
              project="Dawson"),
          _ev(2, "Final claim amount RM99,111.22 as certified.", "scan_0421.pdf",
              project="UNKNOWN")]
    v = verify(ev, query="what is the final claim amount certified?")
    assert v.status == "AMBIGUOUS_PROJECT", (v.status, v.flags)
    assert "UNKNOWN" in v.by_project, v.by_project


def test_non_sensitive_questions_only_disclose():
    """The escalation must stay narrow, or D-20's whole argument collapses."""
    ev = [_ev(1, "Samanea saman rain trees along the boulevard planting.",
              "spec.pdf", project="Dawson"),
          _ev(2, "Samanea saman rain trees at the entrance plaza.",
              "scan_0422.pdf", project="UNKNOWN")]
    v = verify(ev, query="where are the samanea rain trees planted?")
    assert v.status == "PARTIAL", (v.status, v.flags)
    assert any("no known project" in f.lower() for f in v.flags)


def test_status_reports_the_unknown_project_share(ingested):
    """N3 condition 3: D-20 is only defensible while this number is visible."""
    _, mf = ingested
    stats = mf.stats()
    assert "project_unknown" in stats and "project_unknown_pct" in stats
    assert 0.0 <= stats["project_unknown_pct"] <= 100.0


# ------------------------------------------------------------------ R3-9
def test_malformed_fts_query_is_recorded_not_silently_empty(ingested):
    """R3-9: a degraded lexical leg looked identical to 'no documents matched'."""
    import sqlite3

    from alirag.instrument import Trace
    from alirag.sparse import SparseIndex
    cfg, _ = ingested
    idx = SparseIndex(cfg.sparse_db)

    class Con:
        def execute(self, *a, **k):
            raise sqlite3.OperationalError("fts5: syntax error near \"/\"")

    idx.con = Con()
    tr = Trace("q", "FAST")
    assert idx.search("anything", k=5, trace=tr) == []
    assert tr.data.get("sparse_error"), "a broken lexical leg must be visible"


# ------------------------------------------------------------------ R3-10
def test_lexical_tiebreak_ignores_stopwords():
    """R3-10: a third private tokenizer with no stopword list, in a system
    whose worst grounding defect came from having two."""
    from alirag.retrieve import _lexical_tiebreak
    hits = [{"chunk_id": 1, "text": "the and for the and for", "score": 1.0},
            {"chunk_id": 2, "text": "samanea saman rain tree", "score": 1.0}]
    out = _lexical_tiebreak("the and for samanea saman", hits)
    assert out[0]["chunk_id"] == 2
    assert out[1]["lexical_overlap"] == 0.0


# ------------------------------------------------------------------ §1 (standing)
def test_extraction_and_query_never_touch_the_sources(ingested):
    """The headline guarantee, re-asserted by evidence after a round of changes
    to retrieval and verification."""
    cfg, _ = ingested
    root = Path(cfg.source_roots[0])
    before = {p: (hash_path(p), p.stat().st_mtime_ns)
              for p in root.rglob("*") if p.is_file()}
    eng = Engine(cfg)
    for q in ("find LAI-003", "rain tree trunk diameter",
              "deep: final claim amount certified"):
        eng.query(q, use_llm=False, use_cache=False)
    eng.close()
    after = {p: (hash_path(p), p.stat().st_mtime_ns)
             for p in root.rglob("*") if p.is_file()}
    assert before == after
