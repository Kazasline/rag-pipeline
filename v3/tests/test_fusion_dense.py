from alirag.dense import MemmapDense
from alirag.embed import HashEmbedder
from alirag.retrieve import rrf_fuse


def test_rrf_prioritizes_multi_source_agreement():
    sparse = [{"chunk_id": 1, "score": 9.0, "source": "sparse"},
              {"chunk_id": 2, "score": 5.0, "source": "sparse"}]
    dense = [{"chunk_id": 2, "score": 0.9, "source": "dense"},
             {"chunk_id": 3, "score": 0.8, "source": "dense"}]
    fused = rrf_fuse([sparse, dense])
    assert fused[0]["chunk_id"] == 2                 # appears in both legs
    assert set(fused[0]["sources"]) == {"sparse", "dense"}


def test_rrf_exact_weight_wins():
    exact = [{"chunk_id": 7, "score": 2.0, "source": "exact"}]
    dense = [{"chunk_id": 8, "score": 0.99, "source": "dense"},
             {"chunk_id": 9, "score": 0.98, "source": "dense"}]
    fused = rrf_fuse([exact, dense])
    assert fused[0]["chunk_id"] == 7                 # exact outranks rank-1 dense


def test_memmap_roundtrip_and_orphans(tmp_path):
    emb = HashEmbedder(dim=64)
    store = MemmapDense(tmp_path, dim=64)
    texts = ["rain tree samanea saman", "root barrier planter detail",
             "ficus microcarpa trunk"]
    store.add([101, 102, 103], emb.embed(texts))
    q = emb.embed(["samanea saman rain tree"])[0]
    hits = store.search(q, k=2)
    assert hits[0]["chunk_id"] == 101
    # orphan a row (file re-ingested) -> it disappears from results
    store.remove([101])
    hits2 = store.search(q, k=3)
    assert all(h["chunk_id"] != 101 for h in hits2)
    # persistence: fresh handle sees the same state (§85 in miniature)
    store2 = MemmapDense(tmp_path, dim=64)
    assert store2.count() == 3
    assert all(h["chunk_id"] != 101 for h in store2.search(q, k=3))


def test_memmap_dim_mismatch_refuses(tmp_path):
    MemmapDense(tmp_path, dim=64)
    import pytest
    with pytest.raises(RuntimeError, match="dim mismatch"):
        MemmapDense(tmp_path, dim=128)
