r"""Regression tests for the round-6 reviewer's findings.

Every test here was written BECAUSE the revert matrix reported the
corresponding fix unguarded — including four fixes written earlier in this same
round, which had no tests at all until the matrix said so. That is the point of
the artifact: the fix and the evidence that it is load-bearing are separate
pieces of work, and only one of them can be produced by intending to.
"""

from pathlib import Path

import pytest

from alirag.answer import Engine
from alirag.config import Config, EmbedConfig
from alirag.safety import SafetyGuard
from alirag.sparse import MAX_CODE_VARIANTS, SparseIndex, code_variants
from alirag.verify import _query_doc_codes, verify


def _ev(text, **over):
    e = {"chunk_id": 1, "text": text, "filename": "doc.pdf",
         "project": "UNKNOWN", "revision": "R01", "superseded_by": None,
         "sources": ["dense"]}
    e.update(over)
    return e


# ------------------------------------------------------------------ F5-5b
def test_a_date_in_unattributed_evidence_escalates():
    """The F5-5 mutation disabled the query regex, MONEY_RE and DATE_RE at
    once and reported one RED. Split apart, the DATE half had no test."""
    ev = [_ev("Handover of the boulevard turf was recorded on 12/08/2026.",
              filename="scan_a.pdf"),
          _ev("Handover of the boulevard turf was recorded on 03/09/2026.",
              chunk_id=2, filename="scan_b.pdf")]
    v = verify(ev, query="what is the handover date for the boulevard turf?")
    assert v.status == "AMBIGUOUS_PROJECT", (v.status, v.flags)


def test_a_quantity_in_unattributed_evidence_escalates():
    ev = [_ev("The defects liability period for the boulevard turf runs "
              "for 24 months.", filename="scan_c.pdf")]
    v = verify(ev, query="how long is the defects liability period for the "
                         "boulevard turf?")
    assert v.status == "AMBIGUOUS_PROJECT", (v.status, v.flags)


# ------------------------------------------------------------------ F5-5d
def test_the_query_side_trigger_still_works_on_figureless_evidence():
    """The evidence trigger must not make the query trigger dead code: a
    status question whose evidence carries no number at all still escalates.

    This also pins the boundary of R6-2. "What is the approval status?" is
    SENSITIVE (an unattributable source is still a §60 problem) but not
    QUANTITATIVE — its answer is a word, so requiring a figure in the evidence
    would refuse a correct answer. Writing the R6-2 rule against the wider
    regex did exactly that, and this test caught it."""
    ev = [_ev("The turf submission was rejected and returned unopened.",
              filename="scan_d.pdf"),
          _ev("The turf submission was approved without comment.",
              chunk_id=2, filename="scan_e.pdf")]
    v = verify(ev, query="what is the approval status of the turf submission?")
    assert v.status == "AMBIGUOUS_PROJECT", (v.status, v.flags)


# ------------------------------------------------------------------ R6-1b
def test_a_document_inside_an_excluded_dir_fails_the_verdict(tmp_path: Path):
    """R6-1b: an excluded directory holding DOCUMENTS means the exclusion list
    overlaps the corpus — the operator is losing indexing AND safety coverage
    without being told. That must fail, not be classified away."""
    src = tmp_path / "src"
    (src / "models").mkdir(parents=True)
    doc = src / "models" / "Tender Spec R01.pdf"
    doc.write_text("original terms", encoding="utf-8")
    log = src / "models" / "weights.log"
    log.write_text("tick", encoding="utf-8")

    guard = SafetyGuard([str(src)], str(tmp_path / "ws"),
                        excluded_dirs=("models",))
    snap = tmp_path / "ws" / "snap.jsonl"
    guard.snapshot(snap)

    # a non-document change inside the excluded dir is classified, not failed
    log.write_text("tick tock", encoding="utf-8")
    res = guard.verify_snapshot(snap)
    assert res["pass"] is True and res["verdict"] == "PASS_WITH_EXCLUSIONS"

    # ...but a DOCUMENT changing there is a configuration error and fails
    doc.write_text("clobbered", encoding="utf-8")
    res = guard.verify_snapshot(snap)
    assert res["pass"] is False, res["verdict"]
    assert any("Tender Spec" in p for p in res["excluded_dir_documents_modified"])


def test_a_document_deleted_inside_an_excluded_dir_fails(tmp_path: Path):
    src = tmp_path / "src"
    (src / "build").mkdir(parents=True)
    doc = src / "build" / "Contract.docx"
    doc.write_text("terms", encoding="utf-8")
    guard = SafetyGuard([str(src)], str(tmp_path / "ws"),
                        excluded_dirs=("build",))
    snap = tmp_path / "ws" / "snap.jsonl"
    guard.snapshot(snap)
    doc.unlink()
    res = guard.verify_snapshot(snap)
    assert res["pass"] is False
    assert any("Contract" in p for p in res["excluded_dir_documents_deleted"])


# ------------------------------------------------------------------ R6-2
TITLE_BLOCKS = [
    "SKYPARK TOWERS — PODIUM LANDSCAPE GA — SHEET 3 OF 40 — SCALE 1:200",
    "CLIENT: MERIDIAN SDN BHD — CONSULTANT: SCI — HARDSCAPE PAVING LAYOUT",
]


@pytest.mark.parametrize("text", TITLE_BLOCKS)
@pytest.mark.parametrize("q", [
    "what is the final claim amount for the Skypark Towers podium?",
    "what is the certified payment for the hardscape paving?",
    "when was the podium landscape handed over?",
])
def test_a_figure_question_is_not_answered_by_a_title_block(text, q):
    """R6-2: F5-1 subtracted terms belonging to MANIFEST PROJECT LABELS, and a
    title block is full of words that are not labels — the development name,
    the drawing title, the client, the consultant. Two of them cleared the
    floor and the block came back SUPPORTED, unflagged, for a money question."""
    ev = [_ev(text, filename="L-900.pdf", project="Skypark Towers")]
    v = verify(ev, query=q, known_projects=["Skypark Towers", "Meridian"])
    assert v.status == "INSUFFICIENT", (q, text[:30], v.status, v.flags)


def test_a_figure_question_is_answered_when_the_figure_is_there():
    """The rule keys on the material, so real answers must survive it."""
    ev = [_ev("The final claim amount certified is RM50,569.30.",
              filename="claim.pdf", project="Dawson")]
    assert verify(ev, query="what is the final claim amount?").status \
        == "SUPPORTED"
    ev2 = [_ev("The defects liability period is 24 months from completion.",
               filename="spec.pdf", project="Dawson")]
    assert verify(ev2, query="how long is the defects liability period?").status \
        == "SUPPORTED"


# ------------------------------------------------------------------ R6-3
def test_bare_numbers_are_not_document_identifiers(tmp_path: Path):
    """R6-3: 'A-1234 Planting Schedule.pdf' indexed the bare number '1234', so
    'what does grid 12-34 show?' scored an exact hit on it at RRF weight 2.0 —
    the heaviest in the system — putting an unrelated document on top."""
    assert "1234" not in code_variants("A-1234 Planting Schedule.pdf")
    assert "A1234" in code_variants("A-1234 Planting Schedule.pdf")
    # pure words are equally unreachable by any query and must not be stored
    v = code_variants("KP-980ASPEN-CS-LANDSCAPE-26.pdf")
    assert "LANDSCAPE" not in v and "CSLANDSCAPE" not in v

    idx = SparseIndex(tmp_path / "s.sqlite")
    idx.index_chunks([{"chunk_id": 1, "file_id": 1, "text": "Planting schedule.",
                       "filename": "A-1234 Planting Schedule.pdf",
                       "project": "Dawson"}])
    assert idx.search_ids("what does grid 12-34 show?", k=5) == []
    assert idx.search_ids("refer to detail 12/34", k=5) == []
    assert [h["chunk_id"] for h in idx.search_ids("find A-1234", k=5)] == [1]
    idx.close()


def test_code_variant_total_is_bounded_by_the_constant():
    """RV-6: the old test compared against MAX_CODE_VARIANTS itself, so raising
    the constant to 10**9 kept it green. Assert an absolute ceiling."""
    assert MAX_CODE_VARIANTS <= 1000, MAX_CODE_VARIANTS
    many = " ".join(f"AB-{i:03d}-CDE-FGH-JKL-MNO{i}" for i in range(120))
    assert len(code_variants(many, limit=200)) <= 1000


# ------------------------------------------------------------------ R6-3b
def test_query_side_codes_are_expanded_to_variants():
    """R6-3b: F5-2 fixed the index side only, so a user pasting the full sheet
    number from an email missed the short filename on disk — while the reverse
    direction worked."""
    codes = _query_doc_codes("what does DWG-L-201-R03 show?")
    assert "L201" in codes, codes
    ev = {"filename": "L-201.pdf", "text": "Planting layout."}
    from alirag.verify import _names_the_document
    assert _names_the_document(ev, codes)


# ------------------------------------------------------------------ RV-8
def test_second_pass_reverify_keeps_the_known_project_list(ingested):
    """RV-8: the FULLSWING second pass re-verifies (N8), but dropped
    `known_projects`, so the F5-1 protection did not apply to the combined
    evidence — the wiring-vs-helper gap, for the third round running."""
    import inspect

    from alirag import answer
    src = inspect.getsource(answer.Engine.query)
    second = src.split("second_pass", 1)[0].rsplit("verify(combined", 1)
    assert len(second) == 2, "second-pass verify() call not found"
    call = "verify(combined" + src.split("verify(combined", 1)[1].split(")", 1)[0]
    assert "known_projects" in call, call


# ------------------------------------------------------------------ RV-10
def test_dense_leg_is_scoped_before_fusion(ingested):
    """RV-10: three of the four retrieval legs had a scoping mutation in the
    matrix; the dense leg did not, and dropping its `allowed_chunks` changed
    no test."""
    from alirag.dense import MemmapDense
    from alirag.instrument import Trace
    cfg, mf = ingested
    dawson = {r[0] for r in mf.con.execute(
        "SELECT c.chunk_id FROM chunks c JOIN files f ON f.file_id=c.file_id "
        "WHERE f.project='Dawson'")}
    seen = {}

    class Recording(MemmapDense):
        def search(self, qvec, k=20, allowed_chunks=None):
            seen["allowed"] = allowed_chunks
            return super().search(qvec, k=k, allowed_chunks=allowed_chunks)

    eng = Engine(cfg)
    eng.retriever.dense = Recording(cfg.dense_dir, cfg.embed.dim)
    eng.retriever.retrieve("root barrier", cfg.policies["FAST"],
                           Trace("q", "FAST"), project="Dawson")
    eng.close()
    assert seen.get("allowed") is not None, "dense leg ran unscoped"
    assert seen["allowed"] <= dawson


# ------------------------------------------------------------------ RV-13
def test_filename_hits_outrank_body_mentions(tmp_path: Path):
    """RV-13: the exact leg weights a filename match above a body mention, so
    'find LAI-003' returns the instruction itself before every document that
    cites it. Flattening the weights changed no test."""
    idx = SparseIndex(tmp_path / "s.sqlite")
    idx.index_chunks([
        {"chunk_id": 1, "file_id": 1, "text": "Refer to LAI-003 for turf areas.",
         "filename": "Tender Spec.pdf", "project": "Dawson"},
        {"chunk_id": 2, "file_id": 2, "text": "Replace cow grass with Zoysia.",
         "filename": "LAI-003 turf instruction.pdf", "project": "Dawson"},
    ])
    hits = idx.search_ids("find LAI-003", k=5)
    assert [h["chunk_id"] for h in hits][0] == 2, hits
    assert hits[0]["score"] > hits[1]["score"]
    idx.close()


# ------------------------------------------------------------------ RV-9
def test_excluded_dir_match_is_by_component_not_leaf(tmp_path: Path):
    """RV-9: matching only the leaf directory name would leave a file nested
    deeper inside an excluded tree — hermes/logs/2026/beat.log — classified as
    unexplained, and would change nothing in the suite."""
    src = tmp_path / "src"
    deep = src / "hermes" / "logs" / "2026"
    deep.mkdir(parents=True)
    beat = deep / "beat.log"
    beat.write_text("tick", encoding="utf-8")
    guard = SafetyGuard([str(src)], str(tmp_path / "ws"),
                        excluded_dirs=("hermes",))
    snap = tmp_path / "ws" / "snap.jsonl"
    guard.snapshot(snap)
    beat.write_text("tick tock", encoding="utf-8")
    res = guard.verify_snapshot(snap)
    assert str(beat) in res["excluded_dir_modified"], res
    assert res["pass"] is True


# ------------------------------------------------------------------ isolation
# The matrix reported F5-1 and N4-4 unguarded after R6-2 landed: R6-2's
# shape check refuses the money questions those tests used, so it caught the
# failure first and their own mechanism was never exercised. A test that only
# passes because a LATER guard fires proves nothing about the earlier one.
# These use NON-quantitative questions, where R6-2 cannot mask anything.
def test_project_name_only_evidence_fails_a_non_quantitative_question():
    """F5-1, isolated from R6-2."""
    tb = {"chunk_id": 1,
          "text": "DAWSON MERIDIAN TOWERS — Drawing title block. Scale 1:100.",
          "filename": "titleblock.pdf", "project": "Dawson", "revision": "R01",
          "superseded_by": None, "sources": ["dense"]}
    v = verify([tb], query="which tree species is planted at Dawson Meridian Towers?",
               known_projects=["Dawson Meridian Towers"])
    assert v.status == "INSUFFICIENT", (v.status, v.flags)


def test_naming_a_file_caps_a_non_quantitative_answer_at_partial():
    """N4-4, isolated from R6-2: the user named the document, so it is returned
    — but nothing in it met the content floor, so the answer can never read as
    SUPPORTED."""
    ev = [{"chunk_id": 1, "text": "Scaffolding hire, March delivery note.",
           "filename": "L-201.pdf", "project": "Dawson", "revision": "R01",
           "superseded_by": None, "sources": ["exact"]}]
    v = verify(ev, query="which planting zones does drawing L-201 cover?")
    assert v.status == "PARTIAL", (v.status, v.flags)
    assert any("not because its content answers" in f for f in v.flags), v.flags
