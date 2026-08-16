r"""Sparse / lexical retrieval (spec §17).

Two complementary structures in one SQLite DB (09_SPARSE_INDEX):

1. FTS5 (BM25, unicode61) over chunk text + filename — handles words,
   phrases, Malay/English tokens.
2. An identifier table of exact codes harvested from text and filenames
   (LAI-003, L-201, R03, KP-980ASPEN-CS-LANDSCAPE-26, T12, clause 14.2 …).
   FTS tokenizers split hyphenated codes apart, so exact-ID lookup gets its
   own normalized index — this is what makes FAST's exact-lexical-first
   policy (§9) deterministic instead of hoping BM25 ranks the code highly.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

# doc/drawing/clause codes: letters+digits joined by - _ / .  (min 2 chars each side)
ID_RE = re.compile(
    r"\b([A-Za-z]{1,10}(?:[-_/][A-Za-z0-9]{1,12}){1,6}|[A-Za-z]{1,6}\d{1,6}[A-Za-z]?|"
    r"\d{1,4}[-/]\d{1,4}(?:[-/][A-Za-z0-9]{1,8})?)\b")
# tokens that look like codes but are noise
ID_STOP = {"a4", "a3", "a1", "a0", "no1", "v1", "v2", "p1", "p2", "x2"}


def normalize_id(tok: str) -> str:
    return re.sub(r"[-_/.]", "", tok).upper()


def harvest_ids(text: str, limit: int = 200) -> list[str]:
    out, seen = [], set()
    for m in ID_RE.finditer(text):
        raw = m.group(0)
        if raw.lower() in ID_STOP or raw.isdigit() or len(raw) < 3:
            continue
        # require at least one digit (pure words are FTS territory)
        if not any(c.isdigit() for c in raw):
            continue
        norm = normalize_id(raw)
        if norm not in seen:
            seen.add(norm)
            out.append(raw)
        if len(out) >= limit:
            break
    return out


SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
    text, filename, project UNINDEXED, chunk_id UNINDEXED,
    tokenize='unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS ids(
    norm TEXT NOT NULL,
    raw  TEXT NOT NULL,
    chunk_id INTEGER NOT NULL,
    file_id INTEGER NOT NULL,
    in_filename INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_ids_norm ON ids(norm);
CREATE INDEX IF NOT EXISTS ix_ids_chunk ON ids(chunk_id);
"""


class SparseIndex:
    def __init__(self, db_path: str | Path):
        # check_same_thread=False: the sparse leg runs in the retrieval thread
        # pool (§74). Python's sqlite3 is built in serialized threading mode,
        # and our cross-thread use is read-only searches, so this is safe.
        self.con = sqlite3.connect(str(db_path), check_same_thread=False)
        self.con.executescript(SCHEMA)

    def close(self):
        self.con.close()

    # ------------------------------------------------------------ write
    def index_chunks(self, rows: list[dict]):
        """rows: {chunk_id, file_id, text, filename, project}"""
        for r in rows:
            self.con.execute(
                "INSERT INTO fts(text, filename, project, chunk_id) VALUES(?,?,?,?)",
                (r["text"], r["filename"], r.get("project", ""), r["chunk_id"]))
            for raw in harvest_ids(r["text"]):
                self.con.execute(
                    "INSERT INTO ids(norm, raw, chunk_id, file_id, in_filename) "
                    "VALUES(?,?,?,?,0)",
                    (normalize_id(raw), raw, r["chunk_id"], r["file_id"]))
            for raw in harvest_ids(r["filename"]):
                self.con.execute(
                    "INSERT INTO ids(norm, raw, chunk_id, file_id, in_filename) "
                    "VALUES(?,?,?,?,1)",
                    (normalize_id(raw), raw, r["chunk_id"], r["file_id"]))
        self.con.commit()

    def delete_file(self, chunk_ids: list[int]):
        if not chunk_ids:
            return
        q = ",".join("?" * len(chunk_ids))
        self.con.execute(f"DELETE FROM fts WHERE chunk_id IN ({q})", chunk_ids)
        self.con.execute(f"DELETE FROM ids WHERE chunk_id IN ({q})", chunk_ids)
        self.con.commit()

    # ------------------------------------------------------------ read
    @staticmethod
    def _fts_query(query: str) -> str:
        """Escape a free-text query into an FTS5 OR-of-terms expression."""
        toks = re.findall(r"[^\s\"'()*:^]+", query)
        toks = [t for t in toks if t]
        if not toks:
            return '""'
        return " OR ".join(f'"{t}"' for t in toks[:24])

    def search(self, query: str, k: int = 20,
               project: str | None = None) -> list[dict]:
        """BM25 search; optional exact project filter. Returns
        [{chunk_id, score, source:'sparse'}] best-first."""
        sql = ("SELECT chunk_id, bm25(fts) AS rank FROM fts WHERE fts MATCH ?")
        args: list = [self._fts_query(query)]
        if project:
            sql += " AND project = ?"
            args.append(project)
        sql += " ORDER BY rank LIMIT ?"
        args.append(k)
        try:
            rows = self.con.execute(sql, args).fetchall()
        except sqlite3.OperationalError:
            return []
        # bm25() is lower-is-better; convert to a positive score
        return [{"chunk_id": int(cid), "score": -float(rank), "source": "sparse"}
                for cid, rank in rows]

    def search_ids(self, query: str, k: int = 20) -> list[dict]:
        """Exact identifier lookup: any code-like token in the query that
        matches a harvested code is a top-priority hit (§9 exact path)."""
        hits: dict[int, float] = {}
        for raw in harvest_ids(query, limit=8):
            norm = normalize_id(raw)
            for cid, in_fn in self.con.execute(
                    "SELECT chunk_id, in_filename FROM ids WHERE norm=? LIMIT ?",
                    (norm, k * 4)):
                # filename matches outrank body mentions
                hits[cid] = max(hits.get(cid, 0.0), 2.0 if in_fn else 1.0)
        ranked = sorted(hits.items(), key=lambda x: -x[1])[:k]
        return [{"chunk_id": cid, "score": s, "source": "exact"} for cid, s in ranked]

    def stats(self) -> dict:
        return {
            "fts_rows": self.con.execute("SELECT COUNT(*) FROM fts").fetchone()[0],
            "id_rows": self.con.execute("SELECT COUNT(*) FROM ids").fetchone()[0],
        }
