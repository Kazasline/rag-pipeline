r"""Adversarial data-safety tests (reviewer findings F1–F7, 2026-08-17).

The independent reviewer proved the §84 check could not fail: overwriting,
deleting and renaming an original all reported pass:True, because attribution
asked "is this path in our write journal?" and the journal was effectively
empty — `guarded_write_path()` was called exactly once in the whole package,
by snapshot() itself.

These tests reproduce each breach through ORDINARY code paths — a plain
open()/unlink()/rename, exactly how the rest of the package writes — with no
forged journal entries. Every one FAILED against the pre-fix implementation.
"""

import json
import os
from pathlib import Path

from alirag.safety import SafetyGuard


def _guard(tmp_path):
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    return SafetyGuard([str(src)], str(tmp_path / "ws")), src


def _snap(guard, tmp_path):
    snap = tmp_path / "ws" / "17_REPORTS" / "snap.jsonl"
    snap.parent.mkdir(parents=True, exist_ok=True)
    guard.snapshot(snap)
    return snap


# ---------------------------------------------------------------- F2: overwrite
def test_unguarded_overwrite_of_an_original_fails_the_check(tmp_path):
    """The exact breach §84 exists to catch, via a plain open() — which is how
    every module in this package actually writes."""
    guard, src = _guard(tmp_path)
    doc = src / "tender.pdf"
    doc.write_text("original contract terms", encoding="utf-8")
    snap = _snap(guard, tmp_path)

    with open(doc, "w", encoding="utf-8") as f:      # no guard involved
        f.write("clobbered")

    res = guard.verify_snapshot(snap)
    assert res["pass"] is False, "an overwritten original must fail §84"
    assert str(doc) in res["unexplained_modified"] + res["rag_modified"]


# ---------------------------------------------------------------- F3: deletion
def test_deletion_of_an_original_fails_the_check(tmp_path):
    guard, src = _guard(tmp_path)
    doc = src / "claim.pdf"
    doc.write_text("RM 97,923.07", encoding="utf-8")
    snap = _snap(guard, tmp_path)

    os.unlink(doc)

    res = guard.verify_snapshot(snap)
    assert res["pass"] is False, "a deleted original must fail §84"
    assert str(doc) in res["deleted"]


# ---------------------------------------------------------------- F4: rename
def test_rename_of_an_original_is_detected_as_a_move(tmp_path):
    """§84 claims 0 moved. A rename is delete+add with identical content."""
    guard, src = _guard(tmp_path)
    doc = src / "LAI-003.pdf"
    doc.write_text("instruction body", encoding="utf-8")
    snap = _snap(guard, tmp_path)

    moved_to = src / "LAI-003-renamed.pdf"
    doc.rename(moved_to)

    res = guard.verify_snapshot(snap)
    assert res["moved"], "a rename must be reported as a move, not silence"
    assert any(m["from"] == str(doc) and m["to"] == str(moved_to)
               for m in res["moved"]), res["moved"]
    assert res["pass"] is False


# ---------------------------------------------------------------- F5: stealth edit
def test_content_change_with_restored_size_and_mtime_is_detected(tmp_path):
    """size+mtime alone cannot see an in-place edit; content hashing can."""
    guard, src = _guard(tmp_path)
    doc = src / "spec.txt"
    doc.write_text("AAAAAAAAAA", encoding="utf-8")
    st = doc.stat()
    snap = _snap(guard, tmp_path)

    doc.write_text("BBBBBBBBBB", encoding="utf-8")        # same length
    os.utime(doc, ns=(st.st_atime_ns, st.st_mtime_ns))    # same mtime

    res = guard.verify_snapshot(snap)
    assert res["pass"] is False, "a same-size, same-mtime edit must be caught"
    assert str(doc) in res["modified"]


# ---------------------------------------------------------------- F2: false accusation
def test_a_refused_write_is_not_counted_as_our_write(tmp_path):
    """"REFUSED_write".endswith("write") was True, so a BLOCKED attempt was
    attributed to us and an unrelated user edit then failed the check."""
    guard, src = _guard(tmp_path)
    doc = src / "notes.txt"
    doc.write_text("v1", encoding="utf-8")

    try:
        guard.guarded_write_path(doc, "attempted breach")   # refused + journaled
    except Exception:
        pass

    assert str(doc) not in guard.written_paths(), \
        "a refused write must never enter the written-paths set"


# ---------------------------------------------------------------- allow-list
def test_declared_live_service_paths_do_not_fail_the_check(tmp_path):
    """Real machines have services writing their own logs. Those are excused
    ONLY when explicitly declared — not by default."""
    guard, src = _guard(tmp_path)
    (src / "hermes").mkdir()
    beat = src / "hermes" / "ticker.heartbeat"
    beat.write_text("t0", encoding="utf-8")
    doc = src / "tender.pdf"
    doc.write_text("terms", encoding="utf-8")
    snap = _snap(guard, tmp_path)

    beat.write_text("t1", encoding="utf-8")

    strict = guard.verify_snapshot(snap)
    assert strict["pass"] is False, "undeclared changes must not be excused"

    guard.allow_volatile(["*/hermes/*"])
    excused = guard.verify_snapshot(snap)
    assert excused["pass"] is True, "declared live-service paths are excused"
    assert str(beat) in excused["allowlisted_modified"]

    # ...but the allow-list must not excuse a real document
    doc.write_text("clobbered", encoding="utf-8")
    after = guard.verify_snapshot(snap)
    assert after["pass"] is False, "allow-list must not cover real documents"
    assert str(doc) in after["unexplained_modified"]


# ---------------------------------------------------------------- guarded writes
def test_guarded_open_journals_and_blocks_source_writes(tmp_path):
    """The sanctioned write helper must journal workspace writes and refuse
    source writes — so attribution has something real to rest on."""
    from alirag.safety import SourceWriteViolation
    guard, src = _guard(tmp_path)

    target = tmp_path / "ws" / "12_CACHE" / "out.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    with guard.guarded_open(target, "w", purpose="test") as f:
        f.write("{}")
    assert str(target) in guard.written_paths()

    try:
        with guard.guarded_open(src / "doc.pdf", "w", purpose="breach"):
            pass
        raise AssertionError("guarded_open must refuse a source path")
    except SourceWriteViolation:
        pass


def test_snapshot_records_content_hashes(tmp_path):
    guard, src = _guard(tmp_path)
    (src / "a.txt").write_text("hello", encoding="utf-8")
    snap = _snap(guard, tmp_path)
    rec = json.loads(Path(snap).read_text(encoding="utf-8").splitlines()[0])
    assert rec.get("h"), "snapshot must store a content hash, not just size/mtime"


# ---------------------------------------------------------------- reviewer F8
def test_relevance_floor_rejects_an_off_corpus_question():
    """A question about something absent must not be SUPPORTED just because it
    shares a stopword. "the" used to be enough."""
    from alirag.verify import verify
    ev = [{"chunk_id": 1, "text": "Supply and plant Samanea saman rain trees "
                                  "with root barrier along the planter edge.",
           "filename": "spec.pdf", "project": "Dawson", "revision": "R01",
           "superseded_by": None, "sources": ["dense"]}]
    v = verify(ev, query="What is the warranty period for the pump?")
    assert v.status == "INSUFFICIENT", v.flags
    assert verify(ev, query="the").status == "INSUFFICIENT"
    assert verify(ev, query="apa yang ada dalam ini?").status == "INSUFFICIENT"


def test_relevance_floor_still_accepts_genuine_matches():
    """The floor must not reject real evidence."""
    from alirag.verify import verify
    ev = [{"chunk_id": 1, "text": "Supply and plant Samanea saman rain trees "
                                  "with root barrier along the planter edge.",
           "filename": "spec.pdf", "project": "Dawson", "revision": "R01",
           "superseded_by": None, "sources": ["dense"]}]
    assert verify(ev, query="root barrier planter detail").status != "INSUFFICIENT"
    assert verify(ev, query="Samanea saman planting").status != "INSUFFICIENT"
    # a lexical-leg hit is sufficient on its own
    lex = [{**ev[0], "sources": ["sparse"]}]
    assert verify(lex, query="anything at all here").status != "INSUFFICIENT"


# ---------------------------------------------------------------- reviewer: dense filter
def test_dense_filter_applied_before_topk(tmp_path):
    """At scale the in-project chunks can all fall outside the top-k window;
    filtering afterwards returns nothing."""
    from alirag.dense import MemmapDense
    from alirag.embed import HashEmbedder

    emb = HashEmbedder(dim=64)
    store = MemmapDense(tmp_path / "dense", dim=64)
    # 400 distractors that match the query well, then one weak in-project chunk
    texts = ["rain tree samanea saman planting"] * 400 + ["irrigation pump spec"]
    ids = list(range(1, 402))
    store.add(ids, emb.embed(texts))

    q = emb.embed(["rain tree samanea saman planting"])[0]
    hits = store.search(q, k=5, allowed_chunks={401})
    assert hits, "in-project chunk must be found even when it ranks low overall"
    assert all(h["chunk_id"] == 401 for h in hits), hits
