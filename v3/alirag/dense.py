r"""Dense semantic retrieval (spec §18, §35).

Two interchangeable backends behind one interface:

* QdrantDense — the primary candidate (§18): dense vectors + payload
  filtering, persistent local service. Used when qdrant_client is installed
  and the server responds. Its fitness must still be proven by benchmark on
  the target machine before the Reviewer signs off.

* MemmapDense — the V1-proven fallback: flat float32 file + NumPy dot
  product. Zero services, restart-safe, and fast enough at the current corpus
  scale (cosine over a few hundred thousand rows is milliseconds). This keeps
  the whole system usable when Qdrant is absent, and doubles as the baseline
  ("plain dense RAG", §86) the fancy stack must beat.

Vectors are L2-normalized at write time; cosine == dot product everywhere.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path


class MemmapDense:
    """Append-only f32 matrix + row->chunk_id map. Deleting a file's chunks
    orphans rows (ignored at query time); compaction is a maintenance task,
    mirroring V1's proven compact_index approach."""

    def __init__(self, dir_path: str | Path, dim: int):
        self.dir = Path(dir_path)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.vec_path = self.dir / "vectors.f32"
        self.map_path = self.dir / "rowmap.json"
        self.meta_path = self.dir / "meta.json"
        self.dim = dim
        self._rowmap: list[int | None] = []
        if self.map_path.exists():
            self._rowmap = json.loads(self.map_path.read_text())
        if self.meta_path.exists():
            meta = json.loads(self.meta_path.read_text())
            if meta.get("dim") != dim:
                raise RuntimeError(
                    f"dense index dim mismatch: index={meta.get('dim')} config={dim} "
                    "— reindex required (embedding model changed?)")
        else:
            self.meta_path.write_text(json.dumps({"dim": dim}))

    def count(self) -> int:
        if not self.vec_path.exists():
            return 0
        return self.vec_path.stat().st_size // (self.dim * 4)

    def add(self, chunk_ids: list[int], vectors: list[list[float]]):
        assert len(chunk_ids) == len(vectors)
        with open(self.vec_path, "ab") as f:
            for v in vectors:
                f.write(struct.pack(f"<{len(v)}f", *v))
        self._rowmap.extend(chunk_ids)
        self.map_path.write_text(json.dumps(self._rowmap))

    def remove(self, chunk_ids: list[int]):
        dead = set(chunk_ids)
        self._rowmap = [None if c in dead else c for c in self._rowmap]
        self.map_path.write_text(json.dumps(self._rowmap))

    def search(self, qvec: list[float], k: int = 20,
               allowed_chunks: set[int] | None = None) -> list[dict]:
        import numpy as np
        n = self.count()
        if n == 0:
            return []
        vecs = np.memmap(self.vec_path, dtype="float32", mode="r", shape=(n, self.dim))
        q = np.asarray(qvec, dtype="float32")
        scores = vecs @ q

        # Apply the project filter BEFORE selecting top-k, not after.
        #
        # Filtering afterwards silently empties the dense leg at scale: over a
        # 662k-file corpus where one project is a small fraction, the top few
        # hundred rows by similarity can contain zero in-project chunks, so
        # every project-scoped query would return nothing from dense — and the
        # 5-file test fixture could never reveal it.
        if allowed_chunks is not None:
            mask = np.zeros(n, dtype=bool)
            for row, cid in enumerate(self._rowmap[:n]):
                if cid is not None and cid in allowed_chunks:
                    mask[row] = True
            if not mask.any():
                return []
            scores = np.where(mask, scores, -np.inf)

        pool = min(max(k * 6, 64), n)   # over-fetch to skate past orphaned rows
        top = np.argpartition(-scores, pool - 1)[:pool]
        top = top[np.argsort(-scores[top])]
        out = []
        for row in top:
            if not np.isfinite(scores[row]):
                break                    # everything below here is filtered out
            cid = self._rowmap[row] if row < len(self._rowmap) else None
            if cid is None:
                continue
            if allowed_chunks is not None and cid not in allowed_chunks:
                continue
            out.append({"chunk_id": int(cid), "score": float(scores[row]),
                        "source": "dense"})
            if len(out) >= k:
                break
        return out


class QdrantDense:
    # Declares that search() accepts `project=` — the orchestrator dispatches
    # on this rather than probing with a try/except (see retrieve.py).
    supports_project_filter = True

    def __init__(self, url: str, collection: str, dim: int):
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, VectorParams
        self.client = QdrantClient(url=url, timeout=10)
        self.collection = collection
        self.dim = dim
        existing = {c.name for c in self.client.get_collections().collections}
        if collection not in existing:
            self.client.create_collection(
                collection_name=collection,
                vectors_config=VectorParams(size=dim, distance=Distance.COSINE))

    def count(self) -> int:
        return self.client.count(self.collection, exact=True).count

    def add(self, chunk_ids: list[int], vectors: list[list[float]],
            payloads: list[dict] | None = None):
        from qdrant_client.models import PointStruct
        pts = [PointStruct(id=cid, vector=v,
                           payload=(payloads[i] if payloads else {}))
               for i, (cid, v) in enumerate(zip(chunk_ids, vectors))]
        self.client.upsert(self.collection, pts)

    def remove(self, chunk_ids: list[int]):
        from qdrant_client.models import PointIdsList
        self.client.delete(self.collection, points_selector=PointIdsList(points=chunk_ids))

    def search(self, qvec: list[float], k: int = 20,
               project: str | None = None) -> list[dict]:
        from qdrant_client.models import FieldCondition, Filter, MatchValue
        flt = None
        if project:
            flt = Filter(must=[FieldCondition(key="project",
                                              match=MatchValue(value=project))])
        res = self.client.query_points(self.collection, query=qvec, limit=k,
                                       query_filter=flt)
        return [{"chunk_id": int(p.id), "score": float(p.score), "source": "dense"}
                for p in res.points]


def make_dense(cfg) -> object:
    """auto: Qdrant if importable+reachable, else memmap. The chosen backend
    is reported in /status so nothing silently pretends."""
    backend = cfg.dense.backend
    if backend in ("auto", "qdrant"):
        try:
            return QdrantDense(cfg.dense.qdrant_url, cfg.dense.qdrant_collection,
                               cfg.embed.dim)
        except Exception:
            if backend == "qdrant":
                raise
    return MemmapDense(cfg.dense_dir, cfg.embed.dim)
