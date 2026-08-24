from alirag.chunk import chunk_segments, split_text
from alirag.extract import seg


def test_short_text_single_chunk():
    assert split_text("hello world", 1800, 360) == ["hello world"]


def test_long_text_overlaps_and_covers():
    text = " ".join(f"word{i}" for i in range(2000))
    pieces = split_text(text, 500, 100)
    assert len(pieces) > 3
    # coverage: last words present
    assert "word1999" in pieces[-1]
    # every piece within size bound
    assert all(len(p) <= 500 for p in pieces)


def test_hierarchy_parent_child():
    long_text = "para. " * 800
    chunks = chunk_segments([seg(long_text, page=3, locator="p.3")], size=500, overlap=100)
    parents = [c for c in chunks if c["parent_ord"] is None]
    children = [c for c in chunks if c["parent_ord"] is not None]
    assert parents and children
    assert all(c["page"] == 3 for c in chunks)
    assert all(c["locator"] == "p.3" for c in chunks)
    parent_ords = {p["ord"] for p in parents}
    assert all(c["parent_ord"] in parent_ords for c in children)


def test_empty_segment_dropped():
    assert chunk_segments([seg("   \n  ")]) == []
