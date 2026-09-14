from alirag.safety import SafetyGuard


def _snapshot(guard, tmp_path):
    snap = tmp_path / "ws" / "17_REPORTS" / "snapshot.jsonl"
    snap.parent.mkdir(parents=True, exist_ok=True)
    guard.snapshot(snap)
    return snap


def test_rag_attributable_addition_fails_snapshot_check(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    guard = SafetyGuard([str(src)], str(tmp_path / "ws"))
    snap = _snapshot(guard, tmp_path)
    added = src / "created.txt"
    added.write_text("content", encoding="utf-8")
    guard._audit("write", str(added), "test")

    result = guard.verify_snapshot(snap)

    assert result["pass"] is False
    assert str(added) in result["rag_added"]


def test_unexplained_addition_is_reported_without_failing(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    guard = SafetyGuard([str(src)], str(tmp_path / "ws"))
    snap = _snapshot(guard, tmp_path)
    added = src / "created.txt"
    added.write_text("content", encoding="utf-8")

    result = guard.verify_snapshot(snap)

    assert result["pass"] is True
    assert str(added) in result["unexplained_added"]
    assert result["unexplained_added_count"] == 1


def test_excluded_log_addition_passes(tmp_path):
    src = tmp_path / "src"
    (src / "runtime").mkdir(parents=True)
    guard = SafetyGuard([str(src)], str(tmp_path / "ws"),
                        excluded_dirs=("runtime",))
    snap = _snapshot(guard, tmp_path)
    added = src / "runtime" / "service.log"
    added.write_text("log", encoding="utf-8")

    result = guard.verify_snapshot(snap)

    assert result["pass"] is True
    assert result["verdict"] == "PASS_WITH_EXCLUSIONS"
    assert str(added) in result["excluded_dir_added"]


def test_excluded_document_addition_fails(tmp_path):
    src = tmp_path / "src"
    (src / "runtime").mkdir(parents=True)
    guard = SafetyGuard([str(src)], str(tmp_path / "ws"),
                        excluded_dirs=("runtime",))
    snap = _snapshot(guard, tmp_path)
    added = src / "runtime" / "document.pdf"
    added.write_bytes(b"document")

    result = guard.verify_snapshot(snap)

    assert result["pass"] is False
    assert str(added) in result["excluded_dir_added"]
    assert str(added) in result["excluded_dir_documents_added"]
