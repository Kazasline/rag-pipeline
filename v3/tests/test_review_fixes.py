import os
import json
from pathlib import Path

import pytest

from alirag.config import is_loopback_host
from alirag.inventory import scan
from alirag.safety import SafetyGuard


def _row(mf, name):
    return mf.con.execute(
        "SELECT * FROM files WHERE filename=?", (name,)).fetchone()


def _scan(cfg, mf, **kwargs):
    return scan(cfg, SafetyGuard(cfg.source_roots, cfg.workspace), mf,
                progress_every=0, **kwargs)


def test_deleted_file_becomes_missing_and_unsearchable(ingested):
    cfg, mf = ingested
    row = _row(mf, "LAI-003 turf instruction.txt")
    old_ids = [r[0] for r in mf.con.execute(
        "SELECT chunk_id FROM chunks WHERE file_id=?", (row["file_id"],))]
    Path(row["original_path"]).unlink()
    counts = _scan(cfg, mf)
    current = mf.get(row["file_id"])
    assert current["index_status"] == "MISSING"
    assert mf.con.execute("SELECT COUNT(*) FROM chunks WHERE file_id=?",
                          (row["file_id"],)).fetchone()[0] == 0
    from alirag.sparse import SparseIndex
    sparse = SparseIndex(cfg.sparse_db)
    try:
        q = ",".join("?" * len(old_ids))
        assert sparse.con.execute(
            f"SELECT COUNT(*) FROM fts WHERE chunk_id IN ({q})", old_ids
        ).fetchone()[0] == 0
    finally:
        sparse.close()
    from alirag.answer import Engine
    engine = Engine(cfg)
    try:
        resp = engine.query("find LAI-003", use_llm=False, use_cache=False)
        assert all(row["original_path"] not in s["file"] for s in resp["sources"])
    finally:
        engine.close()
    assert counts["reconciled"] is True
    assert counts["missing"] == 1


def test_scoped_scan_does_not_reconcile(ingested):
    cfg, mf = ingested
    row = _row(mf, "LAI-003 turf instruction.txt")
    Path(row["original_path"]).unlink()
    counts = _scan(cfg, mf, max_files=2)
    assert mf.get(row["file_id"])["index_status"] != "MISSING"
    assert counts["reconciled"] is False


def test_renamed_file_is_a_move_not_a_new_identity(ingested):
    cfg, mf = ingested
    old = Path(cfg.source_roots[0]) / "Dawson/Tender/Landscape Tender Spec R01.txt"
    new = old.with_name("Landscape Tender Spec R01 final.txt")
    row = _row(mf, old.name)
    fid = row["file_id"]
    old_chunks = mf.con.execute(
        "SELECT COUNT(*) FROM chunks WHERE file_id=?", (fid,)).fetchone()[0]
    total = mf.con.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    os.rename(old, new)
    counts = _scan(cfg, mf)
    current = mf.get(fid)
    assert current["original_path"] == str(new)
    assert current["index_status"] == "INDEXED"
    assert mf.con.execute("SELECT COUNT(*) FROM chunks WHERE file_id=?",
                          (fid,)).fetchone()[0] == old_chunks
    assert mf.con.execute("SELECT COUNT(*) FROM files").fetchone()[0] == total
    assert counts["moved"] == 1


def test_missing_file_returning_is_reingested(ingested):
    cfg, mf = ingested
    row = _row(mf, "LAI-003 turf instruction.txt")
    path = Path(row["original_path"])
    content = path.read_text(encoding="utf-8")
    path.unlink()
    _scan(cfg, mf)
    assert mf.get(row["file_id"])["index_status"] == "MISSING"
    path.write_text(content, encoding="utf-8")
    _scan(cfg, mf)
    assert mf.get(row["file_id"])["index_status"] == "UPDATED"
    from alirag.ingest import Ingestor
    ing = Ingestor(cfg, mf=mf)
    try:
        assert ing.run()["ok"] >= 1
    finally:
        ing.sparse.close()
        ing.graph.close()
    assert mf.get(row["file_id"])["index_status"] == "INDEXED"
    from alirag.answer import Engine
    engine = Engine(cfg)
    try:
        resp = engine.query("find LAI-003", use_llm=False, use_cache=False)
        assert any("LAI-003" in s["file"] for s in resp["sources"])
    finally:
        engine.close()


def test_failed_reingest_leaves_no_partial_index(ingested, monkeypatch):
    cfg, mf = ingested
    row = _row(mf, "Landscape Tender Spec R01.txt")
    fid = row["file_id"]
    old_ids = [r[0] for r in mf.con.execute(
        "SELECT chunk_id FROM chunks WHERE file_id=?", (fid,))]
    from alirag.ingest import Ingestor
    ing = Ingestor(cfg, mf=mf)
    monkeypatch.setattr(ing.embedder, "embed",
                        lambda texts: (_ for _ in ()).throw(RuntimeError("boom")))
    try:
        assert ing.ingest_file(row) == "failed"
    finally:
        ing.sparse.close()
        ing.graph.close()
    assert mf.get(fid)["index_status"] == "FAILED"
    assert mf.con.execute("SELECT COUNT(*) FROM chunks WHERE file_id=?",
                          (fid,)).fetchone()[0] == 0
    q = ",".join("?" * len(old_ids))
    from alirag.sparse import SparseIndex
    sparse = SparseIndex(cfg.sparse_db)
    try:
        assert sparse.con.execute(
            f"SELECT COUNT(*) FROM fts WHERE chunk_id IN ({q})", old_ids
        ).fetchone()[0] == 0
    finally:
        sparse.close()
    from alirag.answer import Engine
    engine = Engine(cfg)
    try:
        resp = engine.query("Samanea rain tree", use_llm=False, use_cache=False)
        assert all(s["file_id"] != fid for s in resp["sources"])
    finally:
        engine.close()


def test_reingest_keeps_dense_rowmap_consistent(ingested):
    cfg, mf = ingested
    row = _row(mf, "Landscape Tender Spec R01.txt")
    fid = row["file_id"]
    old_ids = {r[0] for r in mf.con.execute(
        "SELECT chunk_id FROM chunks WHERE file_id=?", (fid,))}
    from alirag.ingest import Ingestor
    ing = Ingestor(cfg, mf=mf)
    try:
        assert ing.ingest_file(row) == "ok"
        disk_map = json.loads((cfg.dense_dir / "rowmap.json").read_text())
        assert ing.dense._rowmap == disk_map
        assert not old_ids.intersection(disk_map)
    finally:
        ing.sparse.close()
        ing.graph.close()


def test_empty_update_purges_old_content(ingested):
    cfg, mf = ingested
    row = _row(mf, "Landscape Spec.txt")
    fid = row["file_id"]
    Path(row["original_path"]).write_text("", encoding="utf-8")
    _scan(cfg, mf)
    assert mf.get(fid)["index_status"] == "UPDATED"
    from alirag.ingest import Ingestor
    ing = Ingestor(cfg, mf=mf)
    try:
        result = ing.run()
    finally:
        ing.sparse.close()
        ing.graph.close()
    assert result["empty"] == 1
    assert mf.get(fid)["index_status"] == "INDEXED"
    assert mf.con.execute("SELECT COUNT(*) FROM chunks WHERE file_id=?",
                          (fid,)).fetchone()[0] == 0
    from alirag.answer import Engine
    engine = Engine(cfg)
    try:
        resp = engine.query("Ficus microcarpa", use_llm=False, use_cache=False)
        assert all(s["file_id"] != fid for s in resp["sources"])
    finally:
        engine.close()


def test_state_guard_excludes_non_indexed_chunks(ingested):
    cfg, mf = ingested
    row = mf.con.execute(
        "SELECT * FROM files WHERE index_status='INDEXED' LIMIT 1").fetchone()
    cid = mf.con.execute(
        "SELECT chunk_id FROM chunks WHERE file_id=? LIMIT 1",
        (row["file_id"],)).fetchone()[0]
    mf.con.execute("UPDATE files SET index_status='UPDATED' WHERE file_id=?",
                   (row["file_id"],))
    mf.commit()
    from alirag.answer import Engine
    engine = Engine(cfg)
    try:
        assert engine.retriever.hydrate([{"chunk_id": cid}]) == []
    finally:
        engine.close()


@pytest.fixture()
def api_client(ingested):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from alirag.api import create_app
    cfg, mf = ingested
    with TestClient(create_app(cfg)) as client:
        yield client, cfg, mf


def test_api_default_token_disables_mutations(api_client):
    client, _, _ = api_client
    assert client.post("/ingest").status_code == 403
    assert client.post("/reindex?file_id=1").status_code == 403
    assert client.get("/source/1").status_code == 403
    assert client.get("/health").status_code == 200
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/docs").status_code == 200
    assert client.post("/query", json={"query": "LAI-003", "use_llm": False}).status_code == 200


def test_api_token_protects_and_curates_source(api_client):
    client, cfg, mf = api_client
    cfg.api_token = "s3cret"
    fid = mf.con.execute("SELECT file_id FROM files LIMIT 1").fetchone()[0]
    assert client.get("/health").status_code == 401
    assert client.get("/health",
                      headers={"Authorization": "Bearer s3cret"}).status_code == 200
    resp = client.get(f"/source/{fid}",
                      headers={"Authorization": "Bearer s3cret"})
    expected = {"file_id", "filename", "original_path", "extension", "project",
                "project_source", "document_type", "discipline", "revision",
                "page_count", "index_status", "supersedes", "superseded_by",
                "duplicate_tag", "duplicate_of"}
    assert set(resp.json()) == expected
    assert "content_hash" not in resp.json()
    assert "mtime_ns" not in resp.json()
    assert client.get("/health",
                      headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/health",
                      headers={"Authorization": "bearer s3cret"}).status_code == 401
    for raw in (b"Bearer s\xe9cret", b"Bearer s3cret\xc3\xa9"):
        assert client.get("/health", headers={b"Authorization": raw}).status_code == 401
    from fastapi.testclient import TestClient
    from alirag.api import create_app
    with TestClient(create_app(cfg)) as docs_client:
        for path in ("/openapi.json", "/docs"):
            assert docs_client.get(path).status_code == 401
            assert docs_client.get(
                path, headers={"Authorization": "Bearer s3cret"}).status_code == 200


def test_api_query_length_bounded(api_client):
    client, _, _ = api_client
    assert client.post("/query", json={"query": "", "use_llm": False}).status_code == 422
    assert client.post("/query",
                       json={"query": "x" * 4001, "use_llm": False}).status_code == 422


def test_api_serve_refuses_non_loopback_without_token(ingested, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("uvicorn")
    from alirag.api import serve
    cfg, _ = ingested
    cfg.api_host = "0.0.0.0"
    cfg.api_token = ""
    called = []
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: called.append(True))
    with pytest.raises(RuntimeError):
        serve(cfg)
    assert not called


def test_api_serve_passes_tls_files(ingested, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("uvicorn")
    from alirag.api import serve
    cfg, _ = ingested
    cfg.api_host = "0.0.0.0"
    cfg.api_token = "test-token"
    cfg.api_ssl_certfile = "/tmp/cert.pem"
    cfg.api_ssl_keyfile = "/tmp/key.pem"
    captured = {}
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: captured.update(k))

    serve(cfg)

    assert captured["ssl_certfile"] == "/tmp/cert.pem"
    assert captured["ssl_keyfile"] == "/tmp/key.pem"


def test_api_serve_plaintext_omits_tls_files(ingested, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("uvicorn")
    from alirag.api import serve
    cfg, _ = ingested
    cfg.api_host = "0.0.0.0"
    cfg.api_token = "test-token"
    captured = {}
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: captured.update(k))

    serve(cfg)

    assert "ssl_certfile" not in captured
    assert "ssl_keyfile" not in captured


@pytest.mark.parametrize("cert,key", [("/tmp/cert.pem", ""), ("", "/tmp/key.pem")])
def test_api_serve_requires_both_tls_files(ingested, monkeypatch, cert, key):
    pytest.importorskip("fastapi")
    pytest.importorskip("uvicorn")
    from alirag.api import serve
    cfg, _ = ingested
    cfg.api_host = "127.0.0.1"
    cfg.api_ssl_certfile = cert
    cfg.api_ssl_keyfile = key
    called = []
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: called.append(True))

    with pytest.raises(RuntimeError,
                       match="api_ssl_certfile and api_ssl_keyfile must be set together"):
        serve(cfg)
    assert not called


@pytest.mark.parametrize("host,expected", [
    ("127.0.0.1", True), ("localhost", True), ("::1", True),
    ("127.0.0.5", True), ("0.0.0.0", False),
    ("192.168.1.5", False), ("", False),
])
def test_is_loopback_host(host, expected):
    assert is_loopback_host(host) is expected
