r"""Regression tests for the round-7 reviewer's findings, plus the eight
mechanisms its own mutations found unguarded.

Round 6: 6 of the reviewer's 15 mutations came back green. Round 7: 8 of 25.
The rate is not falling, which is the argument the reviewer makes and I accept:
a matrix authored by the builder establishes that the builder's imagination was
exhausted, not that the code is covered.
"""

import json
import tempfile
from pathlib import Path

import pytest

from alirag import reviewer
from alirag.config import Config, EmbedConfig
from alirag.safety import SafetyGuard, hash_path
from alirag.sparse import SparseIndex, code_variants, harvest_ids
from alirag.verify import verify

# A REAL drawing title block. The round-6 fixtures had no date in them, so the
# test passed on inputs that are not what the class looks like (R7-1).
TITLE_BLOCK = ("DAWSON PODIUM LANDSCAPE GENERAL ARRANGEMENT / SHEET 3 OF 40 "
               "SCALE 1:200 DATE 12/03/2024 / DRAWN AZMI CHECKED LIM "
               "APPROVED TAN REV R03")


def _ev(text, **over):
    e = {"chunk_id": 1, "text": text, "filename": "DWG-L-201-R03 Podium GA.pdf",
         "project": "Dawson", "revision": "R03", "superseded_by": None,
         "sources": ["dense"]}
    e.update(over)
    return e


# ------------------------------------------------------------------ R7-1
def test_a_dated_title_block_does_not_answer_a_money_question():
    """R7-1: every drawing title block carries a date, and the shape check
    accepted money OR date OR quantity — so a title block satisfied 'what is
    the final claim amount?', end-to-end and unflagged."""
    v = verify([_ev(TITLE_BLOCK)],
               query="what is the final claim amount for the Dawson podium landscape?",
               known_projects=["Dawson"])
    assert v.status == "INSUFFICIENT", (v.status, v.flags)
    assert any("monetary amount" in f for f in v.flags), v.flags


# ------------------------------------------------------------------ R7-2
def test_the_shape_asked_for_is_the_shape_required():
    """R7-2: the check did not distinguish WHICH shape it matched, so a date
    question was satisfied by a figure and a money question by an area."""
    money_only = _ev("The interim payment released was RM12,500.00 for the podium.")
    assert verify([money_only],
                  query="when is the completion date for the podium?").status \
        == "INSUFFICIENT"
    area_only = _ev("The podium landscape covers 1200 sqm of soft landscape.")
    assert verify([area_only],
                  query="what is the total claim amount for the podium?").status \
        == "INSUFFICIENT"
    # ...and each is answered by its own shape
    assert verify([money_only],
                  query="what payment was released for the podium?").status \
        == "SUPPORTED"


# ------------------------------------------------------------------ R7-9
def test_a_milestone_answers_a_date_question():
    """R7-9: construction contracts express most dates as milestones. The
    narrowing was supposed to stop refusing legitimate word-answers and did
    not, because 'period' pulled the question into the quantity check."""
    ev = _ev("The defects liability period for the podium landscape starts "
             "upon issuance of the Certificate of Practical Completion.")
    v = verify([ev], query="when does the defects liability period for the "
                           "podium landscape start?")
    assert v.status == "SUPPORTED", (v.status, v.flags)


# ------------------------------------------------------------------ R7-3
@pytest.mark.parametrize("ext", [".skp", ".rvt", ".3dm", ".ifc", ".dwf",
                                 ".xlsm", ".docm", ".zip", ".weirdext"])
def test_unrecognised_files_in_an_excluded_dir_are_not_excused(tmp_path: Path,
                                                               ext: str):
    """R7-3: the escape hatch was gated on a 21-entry ALLOWLIST of document
    extensions, and the shipped exclusions name `models` and `build` — folders
    that in this domain hold .skp/.rvt/.xlsm. Every 3D model in a project could
    be destroyed with §84 reporting pass:True. §1 says FILES, not files with a
    recognised extension, so the default is now FAIL."""
    src = tmp_path / "src"
    (src / "Dawson" / "Models").mkdir(parents=True)
    f = src / "Dawson" / "Models" / ("site_model" + ext)
    f.write_text("original", encoding="utf-8")
    guard = SafetyGuard([str(src)], str(tmp_path / "ws"),
                        excluded_dirs=("models",))
    snap = tmp_path / "ws" / "snap.jsonl"
    guard.snapshot(snap)
    f.write_text("destroyed", encoding="utf-8")
    res = guard.verify_snapshot(snap)
    assert res["pass"] is False, (ext, res["verdict"])
    assert any(ext in p for p in res["excluded_dir_documents_modified"])


def test_recognised_service_state_in_an_excluded_dir_is_still_excused(tmp_path: Path):
    """The inversion must not make the gate unsatisfiable again."""
    src = tmp_path / "src"
    (src / "hermes").mkdir(parents=True)
    log = src / "hermes" / "beat.log"
    log.write_text("tick", encoding="utf-8")
    guard = SafetyGuard([str(src)], str(tmp_path / "ws"),
                        excluded_dirs=("hermes",))
    snap = tmp_path / "ws" / "snap.jsonl"
    guard.snapshot(snap)
    log.write_text("tick tock", encoding="utf-8")
    res = guard.verify_snapshot(snap)
    assert res["pass"] is True and res["verdict"] == "PASS_WITH_EXCLUSIONS"


# ------------------------------------------------------------------ R7-4 / RV7-2
def test_a_change_we_made_inside_an_excluded_dir_is_still_ours(tmp_path: Path):
    """RV7-2 (unguarded): swapping the classify() precedence made a
    RAG-attributable change inside an excluded directory report as
    `excluded_dir_modified` and pass §84."""
    src = tmp_path / "src"
    (src / "hermes").mkdir(parents=True)
    f = src / "hermes" / "state.log"
    f.write_text("a", encoding="utf-8")
    guard = SafetyGuard([str(src)], str(tmp_path / "ws"),
                        excluded_dirs=("hermes",))
    snap = tmp_path / "ws" / "snap.jsonl"
    guard.snapshot(snap)
    # journal it as OUR write, then change it
    guard._audit("write", str(f), "test")
    f.write_text("b", encoding="utf-8")
    res = guard.verify_snapshot(snap)
    assert str(f) in res["rag_modified"], res
    assert res["pass"] is False, "a change we made to a source file is a breach"


# ------------------------------------------------------------------ R7-5
def test_a_file_named_like_an_excluded_dir_is_not_excused(tmp_path: Path):
    """R7-5: `_is_excluded_dir` iterated the parts of a FILE path, so a file
    literally named `build` in an ordinary project folder was treated as living
    inside an excluded directory."""
    src = tmp_path / "src"
    (src / "Dawson").mkdir(parents=True)
    f = src / "Dawson" / "build"
    f.write_text("real content", encoding="utf-8")
    guard = SafetyGuard([str(src)], str(tmp_path / "ws"),
                        excluded_dirs=("build",))
    snap = tmp_path / "ws" / "snap.jsonl"
    guard.snapshot(snap)
    f.write_text("clobbered", encoding="utf-8")
    res = guard.verify_snapshot(snap)
    assert res["pass"] is False, res["verdict"]
    assert str(f) in res["unexplained_modified"]


# ------------------------------------------------------------------ R7-6
def test_log_rotation_inside_an_excluded_dir_does_not_fail(tmp_path: Path):
    """R7-6: `moved` was the one category classify() never saw, so ordinary log
    rotation — heartbeat.log -> heartbeat.log.1, the exact case exclusions
    exist for — produced FAIL on every run against the shipped config."""
    src = tmp_path / "src"
    (src / "hermes").mkdir(parents=True)
    log = src / "hermes" / "heartbeat.log"
    log.write_text("tick", encoding="utf-8")
    guard = SafetyGuard([str(src)], str(tmp_path / "ws"),
                        excluded_dirs=("hermes",))
    snap = tmp_path / "ws" / "snap.jsonl"
    guard.snapshot(snap)
    log.rename(src / "hermes" / "heartbeat.log1")
    res = guard.verify_snapshot(snap)
    assert res["pass"] is True, (res["verdict"], res["moved"])
    assert res["excluded_dir_moved"], res


def test_a_document_rename_still_fails(tmp_path: Path):
    """The move classification must not excuse a real rename."""
    src = tmp_path / "src"
    (src / "Dawson").mkdir(parents=True)
    doc = src / "Dawson" / "Tender.pdf"
    doc.write_text("terms", encoding="utf-8")
    guard = SafetyGuard([str(src)], str(tmp_path / "ws"),
                        excluded_dirs=("hermes",))
    snap = tmp_path / "ws" / "snap.jsonl"
    guard.snapshot(snap)
    doc.rename(src / "Dawson" / "Tender_old.pdf")
    res = guard.verify_snapshot(snap)
    assert res["pass"] is False and res["moved"]


# ------------------------------------------------------------------ R7-7
def test_retrieval_finds_the_short_filename_from_a_full_sheet_number(tmp_path: Path):
    """R7-7: R6-3b expanded only the VERIFIER's helper, so retrieval still
    looked up the greedy whole token — and the verifier cannot rescue a
    document retrieval never returned."""
    idx = SparseIndex(tmp_path / "s.sqlite")
    idx.index_chunks([
        {"chunk_id": 1, "file_id": 1, "text": "Planting layout.",
         "filename": "L-201.pdf", "project": "D"},
        {"chunk_id": 2, "file_id": 2, "text": "Setting out plan.",
         "filename": "DWG-L-201-R03.pdf", "project": "D"},
    ])
    got = {h["chunk_id"] for h in idx.search_ids("what does DWG-L-201-R03 show?", k=10)}
    assert got == {1, 2}, got
    idx.close()


# ------------------------------------------------------------------ R7-8
def test_dates_and_bare_numbers_are_not_exact_ids(tmp_path: Path):
    """R7-8: `normalize_id(raw)` was added unconditionally BEFORE the
    letter+digit rule, so every date in every document body became an exact-ID
    row at RRF weight 2.0 across 662k mostly-dated documents."""
    assert code_variants("minutes dated 2024-03-12") == set()
    assert code_variants("12-34 grid") == set()
    idx = SparseIndex(tmp_path / "s.sqlite")
    idx.index_chunks([
        {"chunk_id": 1, "file_id": 1,
         "text": "Minutes of meeting dated 2024-03-12 regarding turf.",
         "filename": "MOM.pdf", "project": "D"},
        {"chunk_id": 2, "file_id": 2, "text": "Grid 12-34 shows the pump chamber.",
         "filename": "Grid.pdf", "project": "D"},
    ])
    assert idx.search_ids("what was decided on 2024-03-12?", k=10) == []
    assert idx.search_ids("what does grid 12-34 show?", k=10) == []
    idx.close()


# ------------------------------------------------------------------ RV7-16
def test_unseparated_codes_are_still_indexed(tmp_path: Path):
    """RV7-16: removing the whole-token line would kill codes with no
    separator — LAI003, NCR12 — which is most of what the §9 path is for."""
    idx = SparseIndex(tmp_path / "s.sqlite")
    idx.index_chunks([{"chunk_id": 1, "file_id": 1, "text": "See LAI003 for turf.",
                       "filename": "note.pdf", "project": "D"}])
    assert [h["chunk_id"] for h in idx.search_ids("find LAI003", k=5)] == [1]
    idx.close()


# ------------------------------------------------------------------ RV7-17
def test_pure_words_are_never_identifiers():
    """RV7-17: `harvest_ids` requires a digit. Without it every capitalised
    word becomes an exact-ID row."""
    # "LANDSCAPE" alone never matches ID_RE (no separator), so it could not
    # exercise the digit rule. A hyphenated pure-word token can.
    assert harvest_ids("SITE-PLAN and LANDSCAPE-DETAIL") == []
    assert harvest_ids("refer to LAI-003") == ["LAI-003"]


# ------------------------------------------------------------------ RV7-18
def test_exact_id_lookup_is_row_bounded(tmp_path: Path):
    """RV7-18: the k*8 LIMIT bounds index-time work on a corpus where one code
    can appear in tens of thousands of chunks (§88)."""
    idx = SparseIndex(tmp_path / "s.sqlite")
    idx.index_chunks([{"chunk_id": i, "file_id": i, "text": "Refer to LAI-003.",
                       "filename": f"n{i}.pdf", "project": "D"}
                      for i in range(1, 60)])
    assert len(idx.search_ids("find LAI-003", k=3)) <= 3
    idx.close()


# ------------------------------------------------------------------ RV7-6
def test_the_walk_never_descends_into_the_workspace(tmp_path: Path):
    """RV7-6: the workspace can legitimately sit inside a source root
    (E:\\ALI_RAG on E:\\). Walking it would report our own index writes as
    source changes on every run."""
    src = tmp_path / "src"
    ws = src / "ALI_RAG"
    ws.mkdir(parents=True)
    (src / "doc.txt").write_text("real", encoding="utf-8")
    (ws / "index.sqlite").write_text("ours", encoding="utf-8")
    guard = SafetyGuard([str(src)], str(ws))
    walked = {str(p) for p in guard._walk_sources()}
    assert str(src / "doc.txt") in walked
    assert not any("ALI_RAG" in p for p in walked), walked


# ------------------------------------------------------------------ RV7-7
def test_a_refused_write_is_not_attributed_to_us(tmp_path: Path):
    """RV7-7: `written_paths` matched on a suffix once, so a REFUSED attempt
    counted as our write and an innocent run could fail."""
    src = tmp_path / "src"
    src.mkdir()
    doc = src / "tender.txt"
    doc.write_text("terms", encoding="utf-8")
    guard = SafetyGuard([str(src)], str(tmp_path / "ws"))
    with pytest.raises(Exception):
        guard.guarded_write_path(doc, "should be refused")
    assert str(doc) not in guard.written_paths()


# ------------------------------------------------------------------ RV7-8
def test_cli_warns_that_volatile_patterns_are_ignored(tmp_path: Path, capsys):
    """RV7-8: a config line the operator believes is protecting them must not
    be silently discarded."""
    from alirag.cli import main
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("x", encoding="utf-8")
    cfg = Config(workspace=str(tmp_path / "ws"), source_roots=[str(src)],
                 embed=EmbedConfig(provider="hash", dim=256))
    cfg.volatile_patterns = ["*/hermes/*"]
    p = cfg.dir("config") / "config.yaml"
    cfg.save(p)
    main(["--config", str(p), "safety", "snapshot"])
    out = capsys.readouterr().out
    assert "volatile_patterns is no longer honoured" in out, out


# ------------------------------------------------------------------ RV7-13
def test_the_shape_check_reads_every_evidence_item():
    """RV7-13: inspecting only the first item would let a money answer in the
    second position go unseen, refusing a correct answer."""
    # BOTH items must survive the relevance floor, or the one without a figure
    # is dropped before the shape check runs and the test proves nothing.
    ev = [_ev("The claim amount certified was recorded in the interim "
              "certificate register.", chunk_id=1),
          _ev("The claim amount certified is RM50,569.30 in certificate 11.",
              chunk_id=2, filename="claim.pdf")]
    v = verify(ev, query="what is the claim amount certified?")
    assert v.status != "INSUFFICIENT", (v.status, v.flags)


# ------------------------------------------------------------------ F5-5b/c
# These isolate the EVIDENCE trigger from the query trigger. The round-6 tests
# used questions containing "date"/"period", which SENSITIVE_INTENT matches on
# its own — so removing DATE_RE or QUANTITY_RE from _sensitive_evidence changed
# nothing and the matrix reported both unguarded. The queries here contain no
# sensitive vocabulary at all, so only the evidence can trigger escalation.
def _unattributed(text, cid):
    return {"chunk_id": cid, "text": text, "filename": f"scan_{cid}.pdf",
            "project": "UNKNOWN", "revision": "R01", "superseded_by": None,
            "sources": ["dense"]}


def test_a_date_in_the_evidence_escalates_without_a_sensitive_question():
    from alirag.verify import SENSITIVE_INTENT
    q = "which turf species was laid in the boulevard planting?"
    assert not SENSITIVE_INTENT.search(q), "query must not trigger on its own"
    ev = [_unattributed("Zoysia matrella turf was laid in the boulevard "
                        "planting on 12/08/2026.", 1),
          _unattributed("Cow grass turf was laid in the boulevard planting "
                        "on 03/09/2026.", 2)]
    assert verify(ev, query=q).status == "AMBIGUOUS_PROJECT"


def test_a_quantity_in_the_evidence_escalates_without_a_sensitive_question():
    from alirag.verify import SENSITIVE_INTENT
    q = "which turf species was laid in the boulevard planting?"
    assert not SENSITIVE_INTENT.search(q)
    ev = [_unattributed("Zoysia matrella turf was laid across 1200 sqm of "
                        "boulevard planting.", 1),
          _unattributed("Cow grass turf was laid across 800 sqm of boulevard "
                        "planting.", 2)]
    assert verify(ev, query=q).status == "AMBIGUOUS_PROJECT"


# ------------------------------------------------------------------ R7-6b
def test_a_document_renamed_inside_an_excluded_dir_still_fails(tmp_path: Path):
    """R7-6 excuses log ROTATION inside an excluded directory. It must not
    excuse a document being renamed there — the rotation test alone left that
    unguarded, because its document lived outside the excluded tree."""
    src = tmp_path / "src"
    (src / "models").mkdir(parents=True)
    doc = src / "models" / "Site Model.skp"
    doc.write_text("geometry", encoding="utf-8")
    guard = SafetyGuard([str(src)], str(tmp_path / "ws"),
                        excluded_dirs=("models",))
    snap = tmp_path / "ws" / "snap.jsonl"
    guard.snapshot(snap)
    doc.rename(src / "models" / "Site Model OLD.skp")
    res = guard.verify_snapshot(snap)
    assert res["pass"] is False, (res["verdict"], res["excluded_dir_moved"])
    assert res["moved"], res


# ------------------------------------------------------------------ R7-8b
def test_retrieval_refuses_non_identifier_tokens_against_a_legacy_index(tmp_path: Path):
    """R7-8 stops bare numbers being INDEXED. R7-8b is the other half: an index
    built before that fix is already on disk, so the query side must refuse
    non-identifier tokens too. Simulated by inserting the legacy row directly."""
    idx = SparseIndex(tmp_path / "s.sqlite")
    idx.index_chunks([{"chunk_id": 1, "file_id": 1,
                       "text": "Minutes dated 2024-03-12 regarding turf.",
                       "filename": "MOM.pdf", "project": "D"}])
    # a row an older build would have written
    idx.con.execute("INSERT INTO ids(norm, raw, chunk_id, file_id, in_filename) "
                    "VALUES('20240312','2024-03-12',1,1,0)")
    idx.con.commit()
    assert idx.search_ids("what was decided on 2024-03-12?", k=10) == []
    idx.close()


# ------------------------------------------------------------------ RV7-18
def test_exact_id_lookup_applies_a_sql_row_limit(tmp_path: Path):
    """RV7-18: the k*8 LIMIT bounds work at query time on a corpus where one
    code appears in tens of thousands of chunks. The result list is truncated
    to k regardless, so only the SQL itself shows whether the cap is applied."""
    idx = SparseIndex(tmp_path / "s.sqlite")
    idx.index_chunks([{"chunk_id": 1, "file_id": 1, "text": "Refer to LAI-003.",
                       "filename": "n.pdf", "project": "D"}])
    seen = []
    real = idx.con.execute

    class Rec:
        def execute(self, sql, *a):
            seen.append((sql, a))
            return real(sql, *a)

    idx.con = Rec()
    idx.search_ids("find LAI-003", k=3)
    limits = [a[0][-1] for sql, a in seen if "FROM ids" in sql and a]
    assert limits and all(0 < lim <= 3 * 8 for lim in limits), limits
    idx.con = real.__self__
    idx.close()


# ------------------------------------------------------------------ §88 cost
def test_snapshot_sample_reports_throughput_without_touching_the_real_snapshot(
        tmp_path: Path, capsys):
    """§88 gap named by the round-7 reviewer: the cost of hashing 662,244 files
    has never been measured, and DATA_SAFETY acceptance depends on that run
    completing. `--sample` measures it on the operator's own machine and labels
    the result an extrapolation."""
    from alirag.cli import main
    src = tmp_path / "src"
    src.mkdir()
    for i in range(12):
        (src / f"f{i}.txt").write_text("x" * 100, encoding="utf-8")
    cfg = Config(workspace=str(tmp_path / "ws"), source_roots=[str(src)],
                 embed=EmbedConfig(provider="hash", dim=256))
    p = cfg.dir("config") / "config.yaml"
    cfg.save(p)

    main(["--config", str(p), "safety", "snapshot", "--sample", "5"])
    out = json.loads(capsys.readouterr().out)
    assert out["files_sampled"] == 5
    assert out["files_under_source_roots"] == 12
    assert out["files_per_second"] > 0
    assert "EXTRAPOLATION" in out["note"]
    # the real snapshot must not have been created or clobbered by a sample
    assert not (cfg.dir("reports") / "safety_snapshot.jsonl").exists()


# ------------------------------------------------------------------ CSV path
def test_csv_question_sheet_round_trips_and_keeps_every_gate(ingested,
                                                             tmp_path: Path):
    """The CSV exists so the person who knows the documents can write the
    questions in Excel. It must not become a way around the honesty gates —
    the same validation runs on both formats."""
    import csv

    from alirag.bench import (BenchmarkError, _load_questions,
                              make_csv_template, run_retrieval_bench)
    cfg, _ = ingested
    sheet = make_csv_template(cfg, tmp_path / "q.csv", n=5)
    rows = list(csv.DictReader(open(sheet, encoding="utf-8-sig")))
    assert rows and rows[0]["reviewed"] == "no"
    assert rows[0]["expected_file"], "the document name must be pre-filled"

    # untouched template -> refused, and the message says what to do
    with pytest.raises(BenchmarkError) as e:
        _load_questions(sheet)
    assert "reviewed" in str(e.value).lower()

    # a filled-in sheet scores
    from conftest import BENCH_QUESTIONS
    with open(sheet, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["question", "expected_file", "project", "mode", "reviewed",
                    "expected_page", "_file_hint", "_snippet_hint"])
        for q in BENCH_QUESTIONS:
            w.writerow([q["q"], q["expect_file"], q["project"], "", "yes",
                        "", "", ""])
    rep = run_retrieval_bench(cfg, sheet, label="adhoc", use_llm=False)
    assert rep["questions"] == len(BENCH_QUESTIONS)

    # ...and duplicates are still refused through this path
    with open(sheet, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["question", "expected_file", "project", "mode", "reviewed",
                    "expected_page", "_file_hint", "_snippet_hint"])
        for i in range(6):
            w.writerow(["which document is the LAI-003 turf instruction?" + "." * i,
                        "LAI-003 turf instruction.txt", "Dawson", "", "yes",
                        "", "", ""])
    with pytest.raises(BenchmarkError) as e:
        _load_questions(sheet)
    assert "more than once" in str(e.value)


# ------------------------------------------------------------------ chat path
def test_marking_an_answer_over_chat_builds_a_reviewed_benchmark_row(ingested,
                                                                     monkeypatch):
    """The §43 review step was a spreadsheet, which is why it never happened.
    Marking an answer in chat is the same review — a human judging an answer
    they saw, to a question they actually asked — and it must produce a row the
    benchmark harness accepts, with every gate still applied."""
    import csv as _csv

    from alirag import mcp_server
    cfg, _ = ingested
    monkeypatch.setattr(mcp_server, "_cfg", cfg)
    monkeypatch.setattr(mcp_server, "_engine", None)
    mcp_server._last.clear()

    # nothing to mark yet
    assert "Tiada jawapan" in mcp_server.mark_answer(True)

    out = mcp_server.answer_question("find LAI-003")
    assert "LAI-003" in out
    assert "Betul tak?" in out, "the operator must be asked, or no review happens"

    msg = mcp_server.mark_answer(True)
    assert "Direkod" in msg

    path = Path(cfg.dir("benchmark")) / "questions.csv"
    rows = list(_csv.DictReader(open(path, encoding="utf-8-sig")))
    assert len(rows) == 1
    assert rows[0]["question"] == "find LAI-003"
    assert rows[0]["reviewed"] == "yes"
    assert "LAI-003" in rows[0]["expected_file"]


def test_a_wrong_answer_needs_the_right_file_named(ingested, monkeypatch):
    """'salah' on its own records nothing: without the correct document there
    is no expectation to score against, and inventing one would fabricate the
    review."""
    from alirag import mcp_server
    cfg, _ = ingested
    monkeypatch.setattr(mcp_server, "_cfg", cfg)
    monkeypatch.setattr(mcp_server, "_engine", None)
    mcp_server._last.clear()
    mcp_server.answer_question("find LAI-003")
    msg = mcp_server.mark_answer(False)
    assert "sepatutnya" in msg.lower()
    assert not (Path(cfg.dir("benchmark")) / "questions.csv").exists()
