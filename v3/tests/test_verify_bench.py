import json

import pytest

from alirag.bench import BenchmarkError, make_template, run_retrieval_bench
from alirag.verify import verify


def _ev(**kw):
    base = {"chunk_id": 1, "text": "some evidence", "filename": "doc.txt",
            "project": "Dawson", "revision": "R01", "superseded_by": None,
            "page": 1, "locator": "p.1", "path": "/x/doc.txt"}
    base.update(kw)
    return base


def test_verify_empty_insufficient():
    v = verify([])
    assert v.status == "INSUFFICIENT"


def test_verify_project_filter():
    v = verify([_ev(project="Dawson"), _ev(chunk_id=2, project="Meridian")],
               project_hint="Dawson")
    assert all(e["project"] == "Dawson" for e in v.kept)
    assert any("other projects" in f for f in v.flags)


def test_verify_wrong_project_everything():
    v = verify([_ev(project="Meridian")], project_hint="Dawson")
    assert v.status == "INSUFFICIENT"


def test_verify_money_conflict():
    v = verify([_ev(text="claim RM50,569.30 certified"),
                _ev(chunk_id=2, filename="old.txt", text="claim RM48,000.00")])
    assert v.conflicts and v.status == "PARTIAL"


def test_verify_superseded_ordering():
    v = verify([_ev(chunk_id=1, filename="R00.txt", superseded_by=99),
                _ev(chunk_id=2, filename="R01.txt", superseded_by=None)])
    assert v.kept[0]["filename"] == "R01.txt"
    assert any("superseded" in f for f in v.flags)


# ---------------------------------------------------------------- benchmark honesty
def test_bench_refuses_empty_question_set(ingested, tmp_path):
    cfg, _ = ingested
    qf = tmp_path / "empty.jsonl"
    qf.write_text("", encoding="utf-8")
    with pytest.raises(BenchmarkError):
        run_retrieval_bench(cfg, qf)


def test_bench_refuses_unreviewed_template(ingested):
    cfg, _ = ingested
    draft = make_template(cfg, n=5)
    with pytest.raises(BenchmarkError, match="review"):
        run_retrieval_bench(cfg, draft)


def test_bench_real_run_produces_metrics(ingested):
    cfg, _ = ingested
    qf = cfg.dir("benchmark") / "questions.jsonl"
    # Every field the hardened gates require: a real question, a document-
    # identifying expectation, an explicit project so the wrong-project rate is
    # genuinely measured, and a human's reviewed flag.
    qs = [
        {"q": "which document is the LAI-003 turf instruction?",
         "expect_file": "LAI-003 turf instruction.txt", "kind": "exact",
         "mode": "", "project": "Dawson", "reviewed": True},
        {"q": "what trunk diameter is required for the rain trees?",
         "expect_file": "Landscape Tender Spec R01.txt",
         "kind": "semantic", "mode": "", "project": "Dawson", "reviewed": True},
        {"q": "what does the Meridian spec require for Ficus microcarpa?",
         "expect_file": "Landscape Spec.txt",
         "kind": "semantic", "mode": "", "project": "Meridian", "reviewed": True},
    ]
    qf.write_text("\n".join(json.dumps(q) for q in qs), encoding="utf-8")
    rep = run_retrieval_bench(cfg, qf, label="fast")
    assert rep["questions"] == 3
    assert 0.0 <= rep["recall"]["@5"] <= 1.0
    assert rep["recall"]["@5"] >= 0.66      # exact + at least one semantic must hit
    assert rep["latency_ms"]["p95"] > 0
    assert rep["machine"]                    # stamped with real machine identity
