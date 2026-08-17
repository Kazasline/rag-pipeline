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


# ---------------------------------------------------------------- F-V3-06
def test_cli_print_survives_non_cp1252_characters(capsys):
    """Retrieved text containing arrows/box chars must not crash the CLI."""
    from alirag.cli import _print
    _print({"answer": "zon A → zon B ✓ «detail» 建築", "n": 1})
    assert "→" in capsys.readouterr().out


# ---------------------------------------------------------------- re-inference
def test_reinfer_updates_existing_rows_without_rehash(cfg, corpus):
    """A fix to the inference rules must reach rows already in the manifest,
    since scan() skips unchanged files."""
    from alirag.inventory import reinfer_metadata, scan
    from alirag.manifest import Manifest
    from alirag.safety import SafetyGuard

    guard = SafetyGuard(cfg.source_roots, cfg.workspace)
    mf = Manifest(cfg.manifest_db)
    scan(cfg, guard, mf, progress_every=0)

    # simulate rows carrying stale/incorrect metadata from an older rule set
    mf.con.execute("UPDATE files SET document_type='MEMO', project='WRONG'")
    mf.commit()

    res = reinfer_metadata(cfg, mf)
    assert res["rows_updated"] > 0
    row = mf.con.execute(
        "SELECT project, document_type FROM files WHERE filename LIKE 'LAI-003%' LIMIT 1"
    ).fetchone()
    assert row["project"] == "Dawson"
    assert row["document_type"] == "LAI"
    mf.close()


def test_scan_can_be_scoped_to_one_root(cfg, corpus):
    """--root must narrow the walk so a pilot indexes the right folders."""
    from alirag.inventory import scan
    from alirag.manifest import Manifest
    from alirag.safety import SafetyGuard

    guard = SafetyGuard(cfg.source_roots, cfg.workspace)
    mf = Manifest(cfg.manifest_db)
    scan(cfg, guard, mf, progress_every=0, roots=[str(corpus / "Meridian")])
    paths = [r[0] for r in mf.con.execute("SELECT original_path FROM files")]
    assert paths, "scoped scan found nothing"
    assert all("Meridian" in p for p in paths), paths
    mf.close()


def test_ingest_can_be_scoped_by_path(cfg, corpus):
    """--path must restrict which files a limited ingest actually processes."""
    from alirag.ingest import Ingestor
    from alirag.inventory import scan
    from alirag.manifest import Manifest
    from alirag.safety import SafetyGuard

    guard = SafetyGuard(cfg.source_roots, cfg.workspace)
    mf = Manifest(cfg.manifest_db)
    scan(cfg, guard, mf, progress_every=0)
    ing = Ingestor(cfg, mf=mf)
    ing.run(path_prefix=str(corpus / "Meridian"))
    indexed = [r[0] for r in mf.con.execute(
        "SELECT original_path FROM files WHERE index_status='INDEXED'")]
    assert indexed and all("Meridian" in p for p in indexed), indexed
    ing.close()


# ---------------------------------------------------------------- F-V3-09
def test_empty_answer_is_reported_not_presented_as_supported(cfg, corpus, monkeypatch):
    """A model that streams only reasoning and no content must NOT yield an
    empty answer carrying a high confidence."""
    from alirag.answer import Engine
    from alirag.ingest import Ingestor
    from alirag.inventory import scan
    from alirag.manifest import Manifest
    from alirag.safety import SafetyGuard

    guard = SafetyGuard(cfg.source_roots, cfg.workspace)
    mf = Manifest(cfg.manifest_db)
    scan(cfg, guard, mf, progress_every=0)
    ing = Ingestor(cfg, mf=mf)
    ing.run()
    ing.close()

    eng = Engine(cfg)
    monkeypatch.setattr(eng.llm, "chat", lambda *a, **k: {
        "text": "", "ttft_ms": 0, "gen_ms": 22482.0, "tokens": 0,
        "reasoning_tokens": 400, "finish_reason": "length",
        "empty_reason": "model produced 400 reasoning tokens and hit the token limit",
        "tokens_per_s": None})
    resp = eng.query("find LAI-003", use_cache=False)

    assert resp["answer"], "must not return an empty answer string"
    assert resp["confidence"] == 0.0, "an empty generation cannot be high-confidence"
    assert resp["evidence_status"] != "SUPPORTED"
    assert "reasoning tokens" in resp["generation_error"]
    assert resp["sources"], "retrieved evidence must still be reported"
    eng.close()


def test_reasoning_deltas_counted_separately():
    """Thinking tokens must be tracked apart from answer content."""
    from alirag.llm import _empty_reason
    assert _empty_reason("", 400, "length") is not None
    assert "raise max_answer_tokens" in _empty_reason("", 400, "length")
    assert _empty_reason("real answer", 400, "stop") is None


# ---------------------------------------------------------------- F-V3-11
def test_ollama_native_stream_parsing(monkeypatch):
    """Native /api/chat streams newline-JSON with thinking split from content."""
    import io
    import alirag.llm as llm_mod
    from alirag.config import Config

    cfg = Config()
    cfg.llm.api_style = "ollama_native"
    cfg.llm.base_url = "http://127.0.0.1:11434/v1"   # /v1 must be stripped
    cfg.llm.model = "qwen3.5:9b"

    captured = {}

    class FakeResp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode())
        return FakeResp(b'\n'.join([
            b'{"message":{"thinking":"hmm"},"done":false}',
            b'{"message":{"content":"Jawapan "},"done":false}',
            b'{"message":{"content":"penuh."},"done":false}',
            b'{"done":true,"done_reason":"stop"}',
        ]))

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)
    out = llm_mod.LLMClient(cfg).chat("sys", "user", mode="FAST")

    assert captured["url"].endswith("/api/chat"), captured["url"]
    assert "/v1/" not in captured["url"]
    assert captured["body"]["think"] is False, "FAST must disable thinking"
    assert out["text"] == "Jawapan penuh."
    assert out["reasoning_tokens"] == 1
    assert out["finish_reason"] == "stop"
    assert out["empty_reason"] is None


def test_ollama_native_enables_thinking_for_deep(monkeypatch):
    import io
    import alirag.llm as llm_mod
    from alirag.config import Config

    cfg = Config()
    cfg.llm.api_style = "ollama_native"
    captured = {}

    class FakeResp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data.decode())
        return FakeResp(b'{"message":{"content":"x"},"done":true,"done_reason":"stop"}')

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)
    llm_mod.LLMClient(cfg).chat("s", "u", mode="DEEP")
    assert captured["body"]["think"] is True, "DEEP should think"


# ---------------------------------------------------------------- F-V3-12
def _ingest_corpus(cfg):
    from alirag.ingest import Ingestor
    from alirag.inventory import scan
    from alirag.manifest import Manifest
    from alirag.safety import SafetyGuard
    guard = SafetyGuard(cfg.source_roots, cfg.workspace)
    mf = Manifest(cfg.manifest_db)
    scan(cfg, guard, mf, progress_every=0)
    ing = Ingestor(cfg, mf=mf)
    ing.run()
    ing.close()


def test_failed_generation_is_never_cached(cfg, corpus, monkeypatch):
    """An empty answer must not be stored and replayed after the fix."""
    from alirag.answer import Engine
    _ingest_corpus(cfg)

    eng = Engine(cfg)
    monkeypatch.setattr(eng.llm, "chat", lambda *a, **k: {
        "text": "", "ttft_ms": 0, "gen_ms": 900.0, "tokens": 0,
        "reasoning_tokens": 921, "finish_reason": "length",
        "empty_reason": "hit the token limit", "tokens_per_s": None})
    first = eng.query("find LAI-003", use_cache=True)
    assert first["generation_error"]

    # the model now works; the earlier failure must not be replayed
    monkeypatch.setattr(eng.llm, "chat", lambda *a, **k: {
        "text": "RM 97,923.07 mengikut [1].", "ttft_ms": 120.0, "gen_ms": 900.0,
        "tokens": 12, "reasoning_tokens": 0, "finish_reason": "stop",
        "empty_reason": None, "tokens_per_s": 13.3})
    second = eng.query("find LAI-003", use_cache=True)
    assert second["cached"] is False, "a failed generation was cached"
    assert "97,923.07" in second["answer"]
    eng.close()


def test_config_change_invalidates_cache(cfg, corpus, monkeypatch):
    """Switching model/API must not serve answers produced by the old one."""
    from alirag.answer import Engine
    _ingest_corpus(cfg)

    eng = Engine(cfg)
    monkeypatch.setattr(eng.llm, "chat", lambda *a, **k: {
        "text": "jawapan lama", "ttft_ms": 10.0, "gen_ms": 10.0, "tokens": 2,
        "reasoning_tokens": 0, "finish_reason": "stop",
        "empty_reason": None, "tokens_per_s": 1.0})
    eng.query("find LAI-003", use_cache=True)
    assert eng.query("find LAI-003", use_cache=True)["cached"] is True
    eng.close()

    cfg.llm.model_fast = "different-model:9b"
    eng2 = Engine(cfg)
    monkeypatch.setattr(eng2.llm, "chat", lambda *a, **k: {
        "text": "jawapan baharu", "ttft_ms": 10.0, "gen_ms": 10.0, "tokens": 2,
        "reasoning_tokens": 0, "finish_reason": "stop",
        "empty_reason": None, "tokens_per_s": 1.0})
    fresh = eng2.query("find LAI-003", use_cache=True)
    assert fresh["cached"] is False, "model change must invalidate the cache"
    assert fresh["answer"] == "jawapan baharu"
    eng2.close()


def test_keep_alive_sent_on_native_requests(monkeypatch):
    """Weights must be asked to stay resident; a reload dominates TTFT."""
    import io
    import alirag.llm as llm_mod
    from alirag.config import Config

    cfg = Config()
    cfg.llm.api_style = "ollama_native"
    captured = {}

    class FakeResp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data.decode())
        return FakeResp(b'{"message":{"content":"x"},"done":true,"done_reason":"stop"}')

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)
    llm_mod.LLMClient(cfg).chat("s", "u", mode="FAST")
    # numeric, not the string "-1" — Ollama rejects the latter (F-V3-14)
    assert captured["body"]["keep_alive"] == -1


# ---------------------------------------------------------------- F-V3-14
def test_keep_alive_is_sent_as_a_number_not_a_string():
    """Ollama rejects the bare string "-1"; it needs a number or a duration."""
    from alirag.llm import _keep_alive_value
    assert _keep_alive_value("-1") == -1
    assert _keep_alive_value(-1) == -1
    assert _keep_alive_value("3600") == 3600
    assert _keep_alive_value("10m") == "10m"      # duration strings pass through
    assert _keep_alive_value("24h") == "24h"


def test_native_request_keep_alive_is_numeric(monkeypatch):
    import io
    import alirag.llm as llm_mod
    from alirag.config import Config

    cfg = Config()
    cfg.llm.api_style = "ollama_native"
    captured = {}

    class FakeResp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data.decode())
        return FakeResp(b'{"message":{"content":"x"},"done":true,"done_reason":"stop"}')

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)
    llm_mod.LLMClient(cfg).chat("s", "u", mode="FAST")
    assert captured["body"]["keep_alive"] == -1
    assert not isinstance(captured["body"]["keep_alive"], str)


def test_rejected_request_reports_the_server_reason(monkeypatch):
    """A 400 must not be reported as 'unreachable' — the body names the cause."""
    import io
    import urllib.error
    import alirag.llm as llm_mod
    from alirag.config import Config

    cfg = Config()
    cfg.llm.api_style = "ollama_native"

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 400, "Bad Request", {},
            io.BytesIO(b'{"error":"invalid keep_alive"}'))

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)
    try:
        llm_mod.LLMClient(cfg).chat("s", "u", mode="FAST")
        raise AssertionError("should have raised")
    except llm_mod.LLMError as e:
        msg = str(e)
        assert "refused" in msg and "400" in msg
        assert "invalid keep_alive" in msg, "server's reason must be surfaced"
        assert "unreachable" not in msg
