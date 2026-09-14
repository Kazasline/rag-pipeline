from alirag.sparse import SparseIndex, harvest_ids, normalize_id


def test_harvest_ids_codes():
    text = "Refer LAI-003 and drawing L-201 R03, also KP-980ASPEN-CS-LANDSCAPE-26."
    ids = [normalize_id(i) for i in harvest_ids(text)]
    assert "LAI003" in ids
    assert "L201" in ids
    assert "KP980ASPENCSLANDSCAPE26" in ids


def test_harvest_ignores_plain_words_and_noise():
    assert harvest_ids("the quick brown fox jumps") == []
    assert "A4" not in [i.upper() for i in harvest_ids("printed on a4 paper")]


def test_fts_and_exact_search(tmp_path):
    idx = SparseIndex(tmp_path / "sparse.sqlite")
    idx.index_chunks([
        {"chunk_id": 1, "file_id": 10, "filename": "LAI-003 instruction.txt",
         "project": "Dawson", "text": "replace cow grass with Zoysia matrella"},
        {"chunk_id": 2, "file_id": 11, "filename": "spec.txt",
         "project": "Meridian", "text": "Ficus microcarpa trunk diameter 100mm"},
    ])
    # BM25 finds the right chunk
    hits = idx.search("Zoysia matrella turf", k=5)
    assert hits and hits[0]["chunk_id"] == 1
    # project filter isolates (§60)
    assert all(h["chunk_id"] != 1 for h in idx.search("Zoysia", k=5, project="Meridian"))
    # exact ID: code in the QUERY matches code harvested from the FILENAME
    ex = idx.search_ids("where is LAI-003?")
    assert ex and ex[0]["chunk_id"] == 1
    # punctuation in query must not crash FTS
    assert idx.search('weird "quote (paren* AND OR', k=3) is not None
    idx.close()
