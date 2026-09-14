"""Data-safety and inventory behavior — the §1/§84 guarantees, executed."""

import os
from pathlib import Path

import pytest

from alirag.inventory import infer_metadata, organization_report, scan
from alirag.manifest import Manifest
from alirag.safety import SafetyGuard, SourceWriteViolation


def _snapshot(root: Path) -> dict:
    out = {}
    for dp, _, fns in os.walk(root):
        for fn in fns:
            p = Path(dp) / fn
            st = p.stat()
            out[str(p)] = (st.st_size, st.st_mtime_ns)
    return out


def test_guard_refuses_source_writes(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    guard = SafetyGuard([str(src)], str(tmp_path / "ws"))
    with pytest.raises(SourceWriteViolation):
        guard.guarded_write_path(src / "evil.txt", "test")
    # workspace writes are fine
    guard.guarded_write_path(tmp_path / "ws" / "ok.txt", "test")


def test_inventory_is_read_only(cfg, corpus):
    before = _snapshot(corpus)
    guard = SafetyGuard(cfg.source_roots, cfg.workspace)
    mf = Manifest(cfg.manifest_db)
    scan(cfg, guard, mf, progress_every=0)
    mf.close()
    assert _snapshot(corpus) == before, "inventory modified the source tree!"


def test_snapshot_verify_detects_tamper(cfg, corpus, tmp_path):
    guard = SafetyGuard(cfg.source_roots, cfg.workspace)
    snap = Path(cfg.workspace) / "17_REPORTS" / "snap.jsonl"
    snap.parent.mkdir(parents=True, exist_ok=True)
    guard.snapshot(snap)
    clean = guard.verify_snapshot(snap)
    assert clean["pass"]
    assert not clean["deleted"] and not clean["modified"]
    # Any change to a real document must fail, whoever made it. The check
    # cannot distinguish a breach from an unexplained third-party edit, so it
    # refuses to guess in the system's own favour.
    victim = corpus / "Meridian" / "Specs" / "Landscape Spec.txt"
    victim.write_text("tampered", encoding="utf-8")
    dirty = guard.verify_snapshot(snap)
    assert str(victim) in dirty["modified"]
    assert str(victim) in dirty["unexplained_modified"]
    assert dirty["pass"] is False, "a modified document must fail §84"
    assert dirty["rag_modified"] == [], "not attributable to us: no audited write"


def test_inventory_classification_and_metadata(ingested):
    cfg, mf = ingested
    # garbage never becomes knowledge
    row = mf.con.execute(
        "SELECT source_type, index_status FROM files WHERE extension='.gguf'").fetchone()
    assert row["source_type"] == "garbage" and row["index_status"] == "SKIPPED"
    # project inferred from top-level folder
    row = mf.con.execute(
        "SELECT project FROM files WHERE filename LIKE 'LAI-003%' LIMIT 1").fetchone()
    assert row["project"] == "Dawson"


def test_duplicates_tagged_not_deleted(ingested, corpus):
    cfg, mf = ingested
    dups = mf.con.execute(
        "SELECT COUNT(*) FROM files WHERE duplicate_tag='EXACT_DUPLICATE'").fetchone()[0]
    assert dups == 1
    # both physical copies still exist on disk (§28: never delete)
    assert (corpus / "Dawson" / "LAI-003 turf instruction.txt").exists()
    assert (corpus / "Dawson" / "Instructions" / "LAI-003 turf instruction.txt").exists()


def test_revision_family_linked(ingested):
    cfg, mf = ingested
    r00 = mf.con.execute(
        "SELECT superseded_by FROM files WHERE filename LIKE '%R00%'").fetchone()
    r01 = mf.con.execute(
        "SELECT file_id, supersedes FROM files WHERE filename LIKE '%R01%'").fetchone()
    assert r00["superseded_by"] == r01["file_id"]
    assert r01["supersedes"] is not None


def test_metadata_never_invented():
    meta = infer_metadata("/src/loosefile.txt", "/src")
    assert meta["project"] == "UNKNOWN"
    assert meta["document_type"] == "UNKNOWN"
    assert meta["revision"] == "UNKNOWN"


def test_incremental_rescan_skips_unchanged(cfg, corpus):
    guard = SafetyGuard(cfg.source_roots, cfg.workspace)
    mf = Manifest(cfg.manifest_db)
    first = scan(cfg, guard, mf, progress_every=0)
    second = scan(cfg, guard, mf, progress_every=0)
    assert second["new"] == 0
    assert second["unchanged"] == first["new"] + first["unchanged"]
    mf.close()


def test_organization_report(ingested):
    cfg, mf = ingested
    rep = organization_report(mf)
    assert rep["projects"].get("Dawson", 0) >= 3
    assert rep["exact_duplicates"] == 1
