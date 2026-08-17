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
    # hermes/sci_ai_library are the operator's live services, already excluded
    # from indexing — the only kind of directory a volatile declaration may
    # point at (round-4 reviewer N4-6).
    return SafetyGuard([str(src)], str(tmp_path / "ws"),
                       excluded_dirs=("hermes", "sci_ai_library")), src


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
def test_excluded_live_service_dirs_are_classified_not_skipped(tmp_path):
    """Real machines have services writing their own logs continuously.

    Round-5 F5-3 made the walk SKIP them, and round-6 R6-1 showed that was a
    regression: §84 then reported PASS while a source document inside such a
    directory was modified, deleted and renamed. The walk covers everything
    again; exclusion now changes how a change is LABELLED, never whether it is
    looked at.
    """
    guard, src = _guard(tmp_path)
    (src / "hermes").mkdir()
    beat = src / "hermes" / "ticker.heartbeat"
    beat.write_text("t0", encoding="utf-8")
    doc = src / "tender.pdf"
    doc.write_text("terms", encoding="utf-8")
    snap = _snap(guard, tmp_path)

    beat.write_text("t1", encoding="utf-8")

    res = guard.verify_snapshot(snap)
    assert res["pass"] is True, res["unexplained_modified"]
    assert res["verdict"] == "PASS_WITH_EXCLUSIONS"
    assert str(beat) in res["modified"], "the change must still be SEEN"
    assert str(beat) in res["excluded_dir_modified"]

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
    # An exact-code question is legitimately answered on one term — but only
    # because the code is verified to be present in the evidence, not because
    # the retriever labelled the hit "exact".
    coded = [{**ev[0], "filename": "LAI-003 turf instruction.pdf",
              "text": "LAI-003 replaces the turf specification.",
              "sources": ["exact"]}]
    assert verify(coded, query="find LAI-003").status != "INSUFFICIENT"


def test_relevance_floor_is_not_waived_by_a_sparse_hit():
    """Round-2 reviewer N1. The FTS expression ORs every token, so a document
    sharing only a stopword comes back from the sparse leg. Reading that as
    "a term was matched" waived the floor and reproduced the fabrication: an
    off-corpus question answered from rain-tree chunks."""
    from alirag.verify import verify
    off_corpus = "What is the warranty period for the pump?"
    body = ("Supply and plant Samanea saman rain trees with root barrier "
            "along the planter edge.")
    for legs in (["sparse"], ["exact"], ["sparse", "dense"],
                 ["exact", "sparse", "dense"]):
        ev = [{"chunk_id": 1, "text": body, "filename": "spec.pdf",
               "project": "Dawson", "revision": "R01", "superseded_by": None,
               "sources": legs}]
        assert verify(ev, query=off_corpus).status == "INSUFFICIENT", \
            f"off-corpus question passed the floor via legs={legs}"


def test_exact_leg_label_alone_does_not_pass_the_floor():
    """The verifier must check the code itself. If it trusts the leg's label,
    it certifies whatever the retriever claims — and the retriever is one of
    the things it exists to check."""
    from alirag.verify import verify
    ev = [{"chunk_id": 1, "text": "Rain trees and root barrier.",
           "filename": "spec.pdf", "project": "Dawson", "revision": "R01",
           "superseded_by": None, "sources": ["exact"]}]
    # query carries a code; the evidence does not contain it
    assert verify(ev, query="what does LAI-003 say?").status == "INSUFFICIENT"


def test_fts_query_drops_stopwords():
    """The other half of N1: an OR-of-terms query containing 'the' matches
    documents that share nothing topical with the question."""
    from alirag.sparse import SparseIndex
    q = SparseIndex._fts_query("What is the warranty period for the pump?")
    lowered = q.lower()
    for stop in ('"the"', '"for"', '"is"', '"what"'):
        assert stop not in lowered, f"{stop} survived into the FTS query: {q}"
    assert '"warranty"' in q and "pump" in q
    # codes and units are not stopwords and must survive
    assert '"L-201"' in SparseIndex._fts_query("drawing L-201 zone B")
    # a query of pure stopwords has no topical term to search for
    assert SparseIndex._fts_query("what is the") == '""'


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


# ---------------------------------------------------------------- reviewer F21-F23
def _write_questions(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")


def test_bench_refuses_unreviewed_questions(cfg, tmp_path):
    """Deleting the REVIEW-ME prefix used to make a placeholder count."""
    from alirag.bench import BenchmarkError, run_retrieval_bench
    qf = tmp_path / "q.jsonl"
    _write_questions(qf, [{"q": "ask about the tender specification",
                           "expect_file": "Landscape Tender Spec R01.txt",
                           "project": "Dawson"}])          # no reviewed flag
    try:
        run_retrieval_bench(cfg, qf)
        raise AssertionError("must refuse a question not marked reviewed")
    except BenchmarkError as e:
        assert "reviewed" in str(e)


def test_bench_refuses_a_bare_extension_as_expected_file(cfg, tmp_path):
    """expect_file ".txt" matched every file and scored Recall@5 = 1.0."""
    from alirag.bench import BenchmarkError, run_retrieval_bench
    qf = tmp_path / "q.jsonl"
    _write_questions(qf, [{"q": "which tender covers zone B?",
                           "expect_file": ".txt", "project": "Dawson",
                           "reviewed": True}])
    try:
        run_retrieval_bench(cfg, qf)
        raise AssertionError("must refuse an extension as an expected source")
    except BenchmarkError as e:
        assert "extension" in str(e) or "too short" in str(e)


def test_bench_refuses_questions_without_a_project(cfg, tmp_path):
    """Missing project => wrong_project_rate 0.0 => reviewer gate passes on
    something that was never measured. The most dangerous accidental bypass."""
    from alirag.bench import BenchmarkError, run_retrieval_bench
    qf = tmp_path / "q.jsonl"
    _write_questions(qf, [{"q": "which tender covers zone B?",
                           "expect_file": "Landscape Tender Spec R01.txt",
                           "reviewed": True}])
    try:
        run_retrieval_bench(cfg, qf)
        raise AssertionError("must refuse a question with no project")
    except BenchmarkError as e:
        assert "project" in str(e)


def test_bench_refuses_a_mislabelled_run(ingested, tmp_path):
    """--label fast on DEEP questions satisfied the FAST reviewer gate."""
    from alirag.bench import BenchmarkError, run_retrieval_bench
    cfg, _ = ingested
    qf = tmp_path / "q.jsonl"
    from conftest import BENCH_QUESTIONS
    _write_questions(qf, [{**q, "mode": "DEEP"} for q in BENCH_QUESTIONS])
    try:
        run_retrieval_bench(cfg, qf, label="fast", use_llm=False)
        raise AssertionError("must refuse a FAST label on a DEEP run")
    except BenchmarkError as e:
        assert "label" in str(e).lower() and "DEEP" in str(e)


def test_bench_report_records_real_hardware_and_sample_size(ingested, tmp_path):
    from alirag.bench import run_retrieval_bench
    cfg, _ = ingested
    qf = tmp_path / "q.jsonl"
    from conftest import BENCH_QUESTIONS
    _write_questions(qf, BENCH_QUESTIONS)
    rep = run_retrieval_bench(cfg, qf, label="adhoc", use_llm=False)
    assert isinstance(rep["machine"], dict)
    assert "gpu" in rep["machine"] and "ram" in rep["machine"]
    assert rep["wrong_project_measured"] == rep["questions"]
    assert rep["latency_ms"]["percentiles_meaningful"] is False, \
        "n=5 must not be presented as a meaningful percentile"


# ---------------------------------------------------------------- reviewer F15
def test_graph_ranks_by_hop_distance_not_ingestion_order(tmp_path):
    """chunk_ids were returned sorted ascending — i.e. insertion order — and
    RRF ranks by position, so the graph leg injected positional bias."""
    from alirag.graph import Graph
    g = Graph(tmp_path / "g.sqlite")
    seed = g.node("DOCUMENT", "LAI-003.pdf")
    near = g.node("DOCUMENT", "near.pdf")
    far = g.node("DOCUMENT", "far.pdf")
    # deliberately give the CLOSE chunk a HIGHER id than the distant one, so
    # ingestion order and hop order disagree
    g.edge(seed, near, "REFERENCES", file_id=1, chunk_id=999)
    g.edge(near, far, "REFERENCES", file_id=2, chunk_id=100)
    g.con.commit()

    hood = g.neighborhood(["LAI-003"], hops=2)
    ids = hood["chunk_ids"]
    assert ids.index(999) < ids.index(100), \
        f"1-hop chunk must outrank 2-hop chunk, got {ids}"
    assert hood["chunk_hops"][999] < hood["chunk_hops"][100]
    g.close()


def test_graph_seed_wildcards_are_escaped(tmp_path):
    """An unescaped LIKE seed matched unrelated nodes wholesale."""
    from alirag.graph import Graph
    g = Graph(tmp_path / "g.sqlite")
    a = g.node("DOCUMENT", "tender spec.pdf")
    b = g.node("DOCUMENT", "unrelated.pdf")
    g.edge(a, b, "REFERENCES", file_id=1, chunk_id=1)
    g.con.commit()
    hood = g.neighborhood(["%"], hops=1)      # would match everything unescaped
    assert not hood["edges"], "a bare % must not match every node"
    g.close()


# ---------------------------------------------------------------- reviewer F18
def test_lexical_tiebreak_uses_whole_words_and_strips_punctuation():
    """"cost" matched "costume"; "amount?" matched nothing."""
    from alirag.retrieve import _lexical_tiebreak
    hits = [{"chunk_id": 1, "score": 1.0, "text": "the costume budget"},
            {"chunk_id": 2, "score": 1.0, "text": "the cost of works"}]
    out = _lexical_tiebreak("what is the cost?", hits)
    by_id = {h["chunk_id"]: h for h in out}
    assert by_id[2]["lexical_overlap"] > by_id[1]["lexical_overlap"], \
        "substring matching made 'costume' score like 'cost'"
    # punctuation-attached query terms must still match
    assert _lexical_tiebreak("amount?", [{"chunk_id": 3, "score": 1.0,
                                          "text": "total amount due"}])[0][
        "lexical_overlap"] == 1.0


# ---------------------------------------------------------------- reviewer F13/F14
def test_cache_invalidated_by_retrieval_policy_and_prompt(ingested, monkeypatch):
    """Halving evidence_k, or rewriting the system prompt, still served the
    old cached answer."""
    import alirag.answer as answer_mod
    from alirag.answer import Engine
    cfg, _ = ingested

    fake = lambda *a, **k: {"text": "jawapan", "ttft_ms": 5.0, "gen_ms": 5.0,
                            "tokens": 1, "reasoning_tokens": 0,
                            "finish_reason": "stop", "empty_reason": None,
                            "tokens_per_s": 1.0}
    eng = Engine(cfg)
    monkeypatch.setattr(eng.llm, "chat", fake)
    eng.query("find LAI-003", use_cache=True)
    assert eng.query("find LAI-003", use_cache=True)["cached"] is True
    eng.close()

    cfg.policies["FAST"].evidence_k = 1          # different retrieval shape
    eng2 = Engine(cfg)
    monkeypatch.setattr(eng2.llm, "chat", fake)
    assert eng2.query("find LAI-003", use_cache=True)["cached"] is False, \
        "changing the retrieval policy must invalidate cached answers"
    eng2.close()

    monkeypatch.setattr(answer_mod, "SYSTEM_PROMPT", "A completely new prompt.")
    eng3 = Engine(cfg)
    monkeypatch.setattr(eng3.llm, "chat", fake)
    assert eng3.query("find LAI-003", use_cache=True)["cached"] is False, \
        "changing the system prompt must invalidate cached answers"
    eng3.close()


def test_cache_hits_do_not_pollute_latency_metrics(ingested, monkeypatch):
    """Cache-hit traces (~0ms) were aggregated into the p50 quoted as system
    latency, and cached responses replayed their original timings."""
    from alirag.answer import Engine
    from alirag.instrument import percentiles
    cfg, _ = ingested

    eng = Engine(cfg)
    monkeypatch.setattr(eng.llm, "chat", lambda *a, **k: {
        "text": "jawapan", "ttft_ms": 5.0, "gen_ms": 5.0, "tokens": 1,
        "reasoning_tokens": 0, "finish_reason": "stop",
        "empty_reason": None, "tokens_per_s": 1.0})
    first = eng.query("find LAI-003", use_cache=True)
    second = eng.query("find LAI-003", use_cache=True)
    assert second["cached"] is True
    assert "cache_hit" in second["latency_ms"], \
        "a cache hit must not replay the original request's timings"
    assert second["latency_ms"] != first["latency_ms"]

    pct = percentiles(cfg.dir("query_history") / "query_traces.jsonl")
    assert pct["cache_hits_excluded"] >= 1
    assert pct["total_ms"]["percentiles_meaningful"] is False
    eng.close()
