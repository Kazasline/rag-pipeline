"""Coverage for the modules the independent reviewer found untested:
extract.py, api.py, bench.compare_layers, reviewer.audit.

TEST_COVERAGE was one of the three categories the reviewer failed. These are
not smoke tests: each one asserts a behaviour that is load-bearing for a
promise the system makes (read-only extraction, localhost-only API, layered
comparison honesty, and the audit's refusal to certify without evidence).
"""

import json
import time
from pathlib import Path

import pytest

from alirag import extract, reviewer
from alirag.config import Config, EmbedConfig
from alirag.inventory import _family_dir, link_revision_families, scan
from alirag.manifest import Manifest
from alirag.safety import SafetyGuard

EML = """From: pm@example.com
To: qs@example.com
Subject: Dawson claim certification
Date: Tue, 12 Aug 2026 09:00:00 +0800
Content-Type: text/plain; charset="utf-8"

Final claim RM50,569.30 certified per LAI-003.
"""


# ------------------------------------------------------------------ extract.py
def test_extract_any_dispatch_and_parser_names(tmp_path: Path):
    """The parser name is recorded in the manifest and shown to the operator,
    so a wrong name is a false provenance claim (§4)."""
    p = tmp_path / "spec.txt"
    p.write_text("root barrier 50mm TD", encoding="utf-8")
    segs, parser = extract.extract_any(str(p), ".txt")
    assert parser == "text"
    assert segs and "root barrier" in segs[0]["text"]

    j = tmp_path / "meta.json"
    j.write_text('{"project": "Dawson", "rev": "R01"}', encoding="utf-8")
    segs, parser = extract.extract_any(str(j), ".json")
    assert parser == "json" and "Dawson" in segs[0]["text"]


def test_extract_json_falls_back_to_raw_when_invalid(tmp_path: Path):
    """A malformed JSON file must still be indexed as text, not lost: §46
    containment means a bad file is degraded, never silently dropped."""
    j = tmp_path / "broken.json"
    j.write_text('{"project": "Dawson", oops', encoding="utf-8")
    segs = extract.extract_json(str(j))
    assert "Dawson" in segs[0]["text"]


def test_extract_eml_keeps_headers_and_locator(tmp_path: Path):
    e = tmp_path / "mail.eml"
    e.write_text(EML, encoding="utf-8")
    segs, parser = extract.extract_any(str(e), ".eml")
    assert parser == "eml"
    text = segs[0]["text"]
    assert "Subject: Dawson claim certification" in text
    assert "RM50,569.30" in text
    # §40: the citation for an email must identify the email, not just the file
    assert "Dawson claim certification" in segs[0]["locator"]


def test_extract_text_survives_undecodable_bytes(tmp_path: Path):
    """Real corpora contain mixed encodings. A UnicodeDecodeError here would
    fail the file (F-V3-06 was the same class of bug at the output end)."""
    p = tmp_path / "legacy.txt"
    p.write_bytes(b"Pokok \xff\xfe hujan 150mm")
    segs = extract.extract_text(str(p))
    assert "Pokok" in segs[0]["text"] and "150mm" in segs[0]["text"]


def test_unsupported_extension_raises_not_returns_empty(tmp_path: Path):
    """An empty result means 'no text in this file'; an unsupported format is
    a different fact and must be distinguishable — ingest maps it to
    UNSUPPORTED rather than INDEXED-with-no-text."""
    p = tmp_path / "model.gguf"
    p.write_bytes(b"\x00" * 8)
    with pytest.raises(extract.ExtractionError):
        extract.extract_any(str(p), ".gguf")


def test_extraction_never_writes_to_the_source(tmp_path: Path):
    """§1: extraction is read-only. Verified by evidence, not by claim —
    hash the file before and after."""
    from alirag.safety import hash_path
    p = tmp_path / "spec.txt"
    p.write_text("root barrier 50mm TD", encoding="utf-8")
    before = (hash_path(p), p.stat().st_mtime_ns)
    extract.extract_any(str(p), ".txt")
    assert (hash_path(p), p.stat().st_mtime_ns) == before


# ------------------------------------------------------------------ api.py
@pytest.fixture()
def client(ingested):
    fastapi = pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    cfg, _ = ingested
    from alirag.api import create_app
    with TestClient(create_app(cfg)) as c:
        yield c, cfg


def test_api_status_and_health_report_real_backends(client):
    c, cfg = client
    h = c.get("/health").json()
    assert h["ok"] is True
    # the backend actually in use must be named, so nothing silently pretends
    assert h["dense_backend"] == "MemmapDense"
    s = c.get("/status").json()
    assert s["manifest"]["state_INDEXED"] >= 5
    assert s["dense_count"] > 0
    assert s["workspace"] == cfg.workspace


def test_api_query_returns_cited_sources_without_internals(client):
    c, _ = client
    r = c.post("/query", json={"query": "find LAI-003", "use_llm": False}).json()
    assert r["mode"] == "FAST"
    assert any("LAI-003" in s["file"] for s in r["sources"])
    # §53: the cache fingerprint is an internal detail and must not leak
    assert "_fingerprint" not in r
    # §40: every citation is followable
    for s in r["sources"]:
        assert s["file_id"] is not None and s["chunk_id"] is not None


def test_api_source_endpoint_rejects_unknown_id(client):
    c, _ = client
    assert c.get("/source/999999").json() == {"error": "unknown file_id"}
    r = c.post("/query", json={"query": "find LAI-003", "use_llm": False}).json()
    fid = r["sources"][0]["file_id"]
    row = c.get(f"/source/{fid}").json()
    assert row["file_id"] == fid and row["original_path"]


def test_api_explain_bypasses_cache(client):
    """§41: /explain must show what THIS request did. Serving it from cache
    would return a trace of a different execution."""
    c, _ = client
    c.post("/query", json={"query": "root barrier detail", "use_llm": False})
    e = c.post("/explain", json={"query": "root barrier detail", "use_llm": False}).json()
    assert e["cached"] is False


def test_api_binds_localhost_by_default(ingested):
    """§52: the API must not be reachable from the network unless the operator
    explicitly says so — confidential corpus behind it."""
    cfg, _ = ingested
    assert cfg.api_host in ("127.0.0.1", "localhost", "::1")


# ------------------------------------------------------------------ bench.compare_layers
QUESTIONS = [
    {"q": "What trunk diameter is required for the rain tree?",
     "expect_file": "Landscape Tender Spec R01.txt", "project": "Dawson",
     "kind": "semantic", "mode": "FAST", "reviewed": True},
    {"q": "Which instruction replaces the cow grass turf in Zone B?",
     "expect_file": "LAI-003 turf instruction.txt", "project": "Dawson",
     "kind": "exact", "mode": "FAST", "reviewed": True},
]


@pytest.fixture()
def questions_file(tmp_path: Path) -> Path:
    p = tmp_path / "questions.jsonl"
    p.write_text("\n".join(json.dumps(q) for q in QUESTIONS) + "\n",
                 encoding="utf-8")
    return p


def test_compare_layers_runs_each_layer_and_reports_separately(ingested,
                                                               questions_file):
    """§86: every layer must earn its complexity, which requires the layers to
    be measured independently and each to leave its own report artifact."""
    from alirag.bench import compare_layers
    cfg, _ = ingested
    res = compare_layers(cfg, questions_file)
    assert set(res) == {"baseline_dense", "hybrid", "hybrid_graph"}
    paths = set()
    for label, r in res.items():
        assert r["recall"]["@5"] is not None
        assert Path(r["_report_path"]).exists()
        paths.add(r["_report_path"])
    assert len(paths) == 3, "layers overwrote each other's evidence"


def test_compare_layers_baseline_actually_disables_sparse(ingested,
                                                         questions_file):
    """A comparison where the 'baseline' secretly still uses the full stack
    would manufacture the conclusion that the stack is worth it."""
    from alirag.bench import compare_layers
    cfg, _ = ingested
    compare_layers(cfg, questions_file)
    base = json.loads(sorted(cfg.dir("benchmark").glob(
        "report_baseline_dense_*.json"))[-1].read_text(encoding="utf-8"))
    sources = {s for q in base["per_question"] for s in q.get("sources_used", [])}
    assert "sparse" not in sources, f"baseline used sparse retrieval: {sources}"


# ------------------------------------------------------------------ reviewer.audit
def _cfg_for_audit(tmp_path: Path) -> Config:
    return Config(workspace=str(tmp_path / "ALI_RAG"), source_roots=[str(tmp_path)],
                  embed=EmbedConfig(provider="hash", dim=256))


def test_audit_is_pending_without_evidence_and_never_pass(tmp_path: Path):
    """§51: 'everything works' with no artifact must be unrepresentable."""
    cfg = _cfg_for_audit(tmp_path)
    rep = reviewer.audit(cfg)
    assert rep["overall"] == "PENDING"
    assert all(v["status"] == "PENDING" for v in rep["items"].values())
    assert all(v["evidence"] is None for v in rep["items"].values())


def test_audit_fails_data_safety_when_snapshot_did_not_pass(tmp_path: Path):
    cfg = _cfg_for_audit(tmp_path)
    (cfg.dir("reports") / "safety_verify_1.json").write_text(
        json.dumps({"pass": False, "unexplained_modified": ["E:/x.docx"]}),
        encoding="utf-8")
    rep = reviewer.audit(cfg)
    assert rep["items"]["DATA_SAFETY"]["status"] == "FAIL"
    assert rep["overall"] == "FAIL"


def _bench_report(**over) -> dict:
    rep = {"questions": 6, "recall": {"@5": 0.8, "@10": 0.9}, "mrr": 0.7,
           "wrong_project_rate": 0.0, "wrong_project_measured": 6,
           "modes_run": ["FAST"], "citation_page_accuracy": 0.9,
           "latency_ms": {"p95": 400, "n": 30, "percentiles_meaningful": True}}
    rep.update(over)
    return rep


def _write_bench(cfg: Config, name: str, rep: dict):
    (cfg.dir("benchmark") / f"{name}_{int(time.time())}.json").write_text(
        json.dumps(rep), encoding="utf-8")


def test_audit_passes_fast_mode_on_a_complete_report(tmp_path: Path):
    cfg = _cfg_for_audit(tmp_path)
    _write_bench(cfg, "report_fast", _bench_report())
    rep = reviewer.audit(cfg)
    assert rep["items"]["FAST_MODE"]["status"] == "PASS"
    assert rep["items"]["LATENCY"]["status"] == "PASS"


def test_audit_rejects_wrong_project_rate_measured_on_nothing(tmp_path: Path):
    """A 0.0 wrong-project rate computed over zero questions is the most
    flattering number in the system and means nothing."""
    cfg = _cfg_for_audit(tmp_path)
    _write_bench(cfg, "report_fast", _bench_report(wrong_project_measured=0))
    assert reviewer.audit(cfg)["items"]["FAST_MODE"]["status"] == "FAIL"


def test_audit_rejects_report_labelled_for_a_mode_it_did_not_run(tmp_path: Path):
    cfg = _cfg_for_audit(tmp_path)
    _write_bench(cfg, "report_deep", _bench_report(modes_run=["FAST"]))
    assert reviewer.audit(cfg)["items"]["DEEP_MODE"]["status"] == "FAIL"


def test_audit_rejects_p95_from_too_few_samples(tmp_path: Path):
    """Below ~20 samples p95 is just the maximum; certifying §10 from it is
    false precision."""
    cfg = _cfg_for_audit(tmp_path)
    _write_bench(cfg, "report_fast", _bench_report(
        latency_ms={"p95": 120, "n": 4, "percentiles_meaningful": False}))
    assert reviewer.audit(cfg)["items"]["LATENCY"]["status"] == "FAIL"


def test_audit_fails_graph_when_it_regresses_hybrid(tmp_path: Path):
    """§86: the graph layer does not get to stay just because it exists."""
    cfg = _cfg_for_audit(tmp_path)
    (cfg.dir("benchmark") / "layer_compare_1.json").write_text(json.dumps({
        "hybrid": {"recall": {"@5": 0.80}},
        "hybrid_graph": {"recall": {"@5": 0.55}}}), encoding="utf-8")
    assert reviewer.audit(cfg)["items"]["GRAPH"]["status"] == "FAIL"


def test_write_report_lists_every_category_with_its_evidence(tmp_path: Path):
    cfg = _cfg_for_audit(tmp_path)
    _write_bench(cfg, "report_fast", _bench_report())
    result = reviewer.audit(cfg)
    out = reviewer.write_report(cfg, result)
    text = out.read_text(encoding="utf-8")
    for name in result["items"]:
        assert name in text
    # the countersignature requirement must survive into the document
    assert "Reviewer" in text


# ------------------------------------------------------------------ F19 revision families
def test_revision_family_links_across_superseded_subfolder(tmp_path: Path):
    """F-V3-21: the previous sheet is normally MOVED into a SUPERSEDED\\
    subfolder. Keying families on the literal parent directory broke the chain,
    so citing the obsolete sheet carried no §62 disclosure."""
    src = tmp_path / "sources" / "Dawson" / "Drawings"
    (src / "SUPERSEDED").mkdir(parents=True)
    (src / "Site Layout Plan R02.txt").write_text("current layout", encoding="utf-8")
    (src / "SUPERSEDED" / "Site Layout Plan R01.txt").write_text(
        "old layout", encoding="utf-8")

    cfg = Config(workspace=str(tmp_path / "ALI_RAG"),
                 source_roots=[str(tmp_path / "sources")],
                 embed=EmbedConfig(provider="hash", dim=256))
    mf = Manifest(cfg.manifest_db)
    scan(cfg, SafetyGuard(cfg.source_roots, cfg.workspace), mf, progress_every=0)

    rows = {Path(r["original_path"]).name: r for r in
            mf.con.execute("SELECT * FROM files").fetchall()}
    old = rows["Site Layout Plan R01.txt"]
    new = rows["Site Layout Plan R02.txt"]
    assert old["superseded_by"] == new["file_id"], \
        "R01 in SUPERSEDED\\ was not linked to R02 in the parent folder"
    assert new["supersedes"] == old["file_id"]
    mf.close()


def test_family_dir_folds_archive_folders_but_not_real_ones():
    assert _family_dir("E:/Dawson/Drawings/SUPERSEDED/L-201 R00.pdf") == \
           _family_dir("E:/Dawson/Drawings/L-201 R01.pdf")
    assert _family_dir("E:/Dawson/Drawings/OLD/2024/L-201 R00.pdf") == \
           _family_dir("E:/Dawson/Drawings/L-201 R01.pdf")
    # a genuinely different discipline folder is NOT folded together
    assert _family_dir("E:/Dawson/Drawings/Softscape/Detail R01.pdf") != \
           _family_dir("E:/Dawson/Drawings/Hardscape/Detail R01.pdf")


def test_same_named_revisions_in_different_projects_are_not_linked(tmp_path: Path):
    """§4: linking these would assert a supersede relation that does not exist."""
    src = tmp_path / "sources"
    (src / "Dawson").mkdir(parents=True)
    (src / "Meridian").mkdir(parents=True)
    (src / "Dawson" / "Layout Plan R01.txt").write_text("dawson", encoding="utf-8")
    (src / "Meridian" / "Layout Plan R02.txt").write_text("meridian", encoding="utf-8")

    cfg = Config(workspace=str(tmp_path / "ALI_RAG"), source_roots=[str(src)],
                 embed=EmbedConfig(provider="hash", dim=256))
    mf = Manifest(cfg.manifest_db)
    scan(cfg, SafetyGuard(cfg.source_roots, cfg.workspace), mf, progress_every=0)
    assert link_revision_families(mf) == 0
    for r in mf.con.execute("SELECT superseded_by, supersedes FROM files"):
        assert r["superseded_by"] is None and r["supersedes"] is None
    mf.close()
