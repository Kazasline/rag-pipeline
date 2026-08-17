r"""Incremental ingestion pipeline (spec §22–§24, §31–§32, §46, §78).

Flow per file (only files whose hash changed since last ingest):

  CLASSIFIED -> EXTRACTING -> chunk -> sparse index -> dense embed -> graph
             -> INDEXED   (or FAILED / UNSUPPORTED, quarantined, never deleted)

Failure containment (§46): one bad document records FAILED with the error and
the run continues. Nothing at query time triggers ingestion; query-time work
is retrieval only (§11).
"""

from __future__ import annotations

import time
import traceback

from .chunk import chunk_segments
from .config import Config
from .dense import make_dense
from .embed import make_embedder
from .extract import ExtractionError, extract_any, render_page_images
from .graph import Graph
from .manifest import Manifest
from .safety import SafetyGuard
from .sparse import SparseIndex

EMBED_BATCH = 48   # V1-proven batch size for Ollama embed calls


class Ingestor:
    def __init__(self, cfg: Config, mf: Manifest | None = None):
        self.cfg = cfg
        self.guard = SafetyGuard(cfg.source_roots, cfg.workspace)
        self.mf = mf or Manifest(cfg.manifest_db)
        self.sparse = SparseIndex(cfg.sparse_db)
        self.dense = make_dense(cfg)
        self.graph = Graph(cfg.graph_db)
        self.embedder = make_embedder(cfg)

    def close(self):
        self.mf.close()
        self.sparse.close()
        self.graph.close()

    # ------------------------------------------------------------ one file
    def ingest_file(self, file_row, *, use_docling: bool = False,
                    ocr: bool = False, render_pages: bool = False) -> str:
        fid = file_row["file_id"]
        path = file_row["original_path"]
        ext = file_row["extension"]
        stype = file_row["source_type"]
        if stype in ("garbage", "legacy", "unsupported"):
            self.mf.set_state(fid, "UNSUPPORTED" if stype == "unsupported" else "SKIPPED",
                              f"source_type={stype}")
            return "skipped"
        if stype == "cad" and ext != ".dxf":
            self.mf.set_state(fid, "SKIPPED",
                              "DWG needs external read-only export to DXF/PDF (§24)")
            return "skipped"

        self.mf.set_state(fid, "EXTRACTING")
        try:
            segments, parser = extract_any(
                path, ext, ocr=ocr, ocr_lang=self.cfg.ingest.ocr_lang,
                use_docling=use_docling)
        except (ExtractionError, Exception) as e:  # noqa: BLE001 — quarantine, never crash the run
            self.mf.set_state(fid, "FAILED", f"extract: {e}")
            self.mf.con.execute(
                "UPDATE files SET parser_used=?, extraction_confidence=0 WHERE file_id=?",
                (type(e).__name__, fid))
            self.mf.commit()
            return "failed"

        if not segments:
            self.mf.set_state(fid, "INDEXED", "no extractable text")
            self.mf.con.execute(
                "UPDATE files SET parser_used=?, page_count=0 WHERE file_id=?",
                (parser, fid))
            self.mf.commit()
            return "empty"

        chunks = chunk_segments(segments, self.cfg.ingest.chunk_chars,
                                self.cfg.ingest.chunk_overlap)
        # replace prior derived data for this file across all indexes
        old_ids = [r[0] for r in self.mf.con.execute(
            "SELECT chunk_id FROM chunks WHERE file_id=?", (fid,))]
        if old_ids:
            self.sparse.delete_file(old_ids)
            try:
                self.dense.remove(old_ids)
            except Exception:
                pass
            self.graph.delete_file_edges(fid)
        chunk_ids = self.mf.replace_chunks(fid, chunks)

        self.sparse.index_chunks([
            {"chunk_id": cid, "file_id": fid, "text": ch["text"],
             "filename": file_row["filename"], "project": file_row["project"]}
            for cid, ch in zip(chunk_ids, chunks)])

        # dense embeddings, precomputed now so query time stays light (§11)
        for b in range(0, len(chunks), EMBED_BATCH):
            batch_ids = chunk_ids[b:b + EMBED_BATCH]
            vecs = self.embedder.embed([c["text"] for c in chunks[b:b + EMBED_BATCH]])
            try:
                self.dense.add(batch_ids, vecs,
                               payloads=[{"project": file_row["project"],
                                          "file_id": fid}] * len(batch_ids))
            except TypeError:  # memmap backend takes no payloads
                self.dense.add(batch_ids, vecs)
        self.mf.con.executemany("UPDATE chunks SET embedded=1 WHERE chunk_id=?",
                                [(c,) for c in chunk_ids])

        self.graph.extract_from_file(file_row, list(zip(chunk_ids, chunks)))

        pages = sorted({c["page"] for c in chunks if c["page"]})
        if render_pages and ext == ".pdf":
            render_page_images(path, self.cfg.dir("page_images"), fid,
                               max_px=self.cfg.ingest.page_image_max_px,
                               pages=pages[:200])
        self.mf.con.execute(
            "UPDATE files SET parser_used=?, page_count=?, extraction_confidence=? "
            "WHERE file_id=?",
            (parser, max(pages) if pages else len(segments),
             0.9 if "ocr" not in parser.lower() else 0.6, fid))
        self.mf.set_state(fid, "INDEXED")
        self.mf.commit()
        return "ok"

    # ------------------------------------------------------------ run
    def run(self, *, states: tuple = ("CLASSIFIED", "UPDATED"),
            limit: int | None = None, use_docling: bool = False,
            ocr: bool = False, render_pages: bool = False,
            project: str | None = None, path_prefix: str | None = None) -> dict:
        todo = self.mf.by_state(*states)
        # Scoping matters for pilots: without it a limited run just takes the
        # first N by discovery order, which on a large drive is whatever sorts
        # first rather than the documents worth indexing.
        if project:
            todo = [r for r in todo if (r["project"] or "").lower() == project.lower()]
        if path_prefix:
            pre = path_prefix.lower()
            todo = [r for r in todo if r["original_path"].lower().startswith(pre)]
        if limit:
            todo = todo[:limit]
        counts = {"ok": 0, "failed": 0, "skipped": 0, "empty": 0}
        t0 = time.time()
        for i, row in enumerate(todo, 1):
            try:
                r = self.ingest_file(row, use_docling=use_docling, ocr=ocr,
                                     render_pages=render_pages)
            except Exception as e:  # noqa: BLE001 — last-resort quarantine
                self.mf.set_state(row["file_id"], "FAILED",
                                  f"pipeline: {e}\n{traceback.format_exc()[:300]}")
                r = "failed"
            counts[r] = counts.get(r, 0) + 1
            if i % 25 == 0 or i == len(todo):
                print(f"[ingest] {i}/{len(todo)} {counts} "
                      f"({time.time()-t0:.0f}s)", flush=True)
        self.graph.link_supersedes(self.mf.con)
        counts["elapsed_s"] = round(time.time() - t0, 1)
        return counts
