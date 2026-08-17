"""Regressions from the first real E:\\ run (2026-08-17).

Both bugs below were invisible on synthetic data and only appeared against
662k real files. See v3/docs/FAILURES.md F-V3-03 and F-V3-04.
"""

import json
from pathlib import Path

from alirag.inventory import infer_metadata
from alirag.safety import SafetyGuard


# ---------------------------------------------------------------- F-V3-04
def test_memory_folder_is_not_a_memo():
    """'AI MAIN MEMORY' must not classify 13k files as MEMO."""
    meta = infer_metadata(r"/src/AI MAIN MEMORY/notes/session log.txt", "/src")
    assert meta["document_type"] != "MEMO"
    assert meta["project"] == "AI MAIN MEMORY"


def test_short_abbreviations_need_word_boundaries():
    """'lain'/'volume'/'bqueue' must not trigger LAI/VO/BQ."""
    for name, wrong in ((r"/src/P/dokumen lain sahaja.txt", "LAI"),
                        (r"/src/P/volume calculation.xlsx", "VO"),
                        (r"/src/P/bquery results.csv", "BQ")):
        assert infer_metadata(name, "/src")["document_type"] != wrong, name


def test_real_doctypes_still_detected():
    """The fix must not break genuine matches."""
    cases = {
        r"/src/Dawson/LAI-003 turf instruction.pdf": "LAI",
        r"/src/Dawson/VO 12 revised planting.pdf": "VO",
        r"/src/Dawson/Landscape Tender Spec.pdf": "TENDER",
        r"/src/Dawson/NCR-004 defect.pdf": "NCR",
        r"/src/Dawson/internal memo 3 Aug.docx": "MEMO",
        r"/src/Dawson/BQ pricing.xlsx": "BQ",
    }
    for path, expect in cases.items():
        assert infer_metadata(path, "/src")["document_type"] == expect, path


def test_doctype_comes_from_filename_not_parent_folder():
    """A project folder named after a doc type must not label every file in it."""
    meta = infer_metadata(r"/src/Tender Documents/site photograph 12.jpg", "/src")
    assert meta["document_type"] != "TENDER"


# ---------------------------------------------------------------- F-V3-03
def test_verify_attributes_external_writers(tmp_path):
    """A live service rewriting its own log must NOT fail the §84 check,
    but must still be reported."""
    src = tmp_path / "src"
    (src / "svc").mkdir(parents=True)
    doc = src / "important.pdf"
    doc.write_text("original", encoding="utf-8")
    heartbeat = src / "svc" / "ticker.heartbeat"
    heartbeat.write_text("t0", encoding="utf-8")

    guard = SafetyGuard([str(src)], str(tmp_path / "ws"))
    snap = tmp_path / "ws" / "17_REPORTS" / "snap.jsonl"
    snap.parent.mkdir(parents=True, exist_ok=True)
    guard.snapshot(snap)

    # another process (not us) rewrites its heartbeat
    heartbeat.write_text("t1 much later", encoding="utf-8")

    res = guard.verify_snapshot(snap)
    assert res["pass"] is True, "external writer must not fail the RAG safety check"
    assert res["pass_strict"] is False, "strict view must still show the change"
    assert str(heartbeat) in res["external_modified"]
    assert res["rag_modified"] == []


def test_verify_still_fails_on_rag_attributable_change(tmp_path):
    """If a path we actually wrote is a source file, that IS a breach."""
    src = tmp_path / "src"
    src.mkdir()
    doc = src / "report.txt"
    doc.write_text("original", encoding="utf-8")

    ws = tmp_path / "ws"
    guard = SafetyGuard([str(src)], str(ws))
    snap = ws / "17_REPORTS" / "snap.jsonl"
    snap.parent.mkdir(parents=True, exist_ok=True)
    guard.snapshot(snap)

    # simulate a breach: audit log claims WE wrote that source file
    logs = ws / "16_LOGS"
    logs.mkdir(parents=True, exist_ok=True)
    with open(logs / "safety_audit.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": 0, "action": "write",
                            "path": str(doc), "purpose": "simulated breach"}) + "\n")
    doc.write_text("tampered by us", encoding="utf-8")

    res = guard.verify_snapshot(snap)
    assert res["pass"] is False
    assert str(doc) in res["rag_modified"]


def test_written_paths_reads_audit_log(tmp_path):
    guard = SafetyGuard([str(tmp_path / "src")], str(tmp_path / "ws"))
    target = guard.guarded_write_path(tmp_path / "ws" / "out.txt", "test")
    assert str(target) in guard.written_paths()
