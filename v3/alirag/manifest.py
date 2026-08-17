r"""Manifest storage — the identity backbone of the RAG (spec §4, §6, §27, §28, §78).

ORIGINAL FILE -> CONTENT HASH -> FILE ID -> DERIVED DATA.

SQLite (WAL) at 02_MANIFEST/manifest.sqlite.  Files are keyed by an integer
file_id; content_hash survives renames/moves of originals, so derived data
(chunks, vectors, graph nodes, page images) never dangles.  Metadata fields
that cannot be inferred with evidence stay 'UNKNOWN' — never invented.

Ingestion states (§78):
DISCOVERED -> CLASSIFIED -> EXTRACTING -> INDEXED
                         -> FAILED / UNSUPPORTED / SKIPPED / UPDATED
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

UNKNOWN = "UNKNOWN"

STATES = ("DISCOVERED", "CLASSIFIED", "EXTRACTING", "INDEXED",
          "FAILED", "UNSUPPORTED", "SKIPPED", "UPDATED")

DUP_TAGS = ("EXACT_DUPLICATE", "PROBABLE_DUPLICATE", "REVISION", "UNRELATED")

SCHEMA = """
CREATE TABLE IF NOT EXISTS files(
    file_id        INTEGER PRIMARY KEY,
    content_hash   TEXT NOT NULL,
    hash_kind      TEXT NOT NULL DEFAULT 'blake2b',   -- blake2b | partial
    original_path  TEXT NOT NULL UNIQUE,
    filename       TEXT NOT NULL,
    extension      TEXT NOT NULL,
    size           INTEGER NOT NULL,
    created_date   TEXT,
    modified_date  TEXT,
    mtime_ns       INTEGER,
    project        TEXT DEFAULT 'UNKNOWN',
    project_source TEXT DEFAULT 'UNKNOWN',   -- folder | content | manual (§4 provenance)
    client         TEXT DEFAULT 'UNKNOWN',
    document_type  TEXT DEFAULT 'UNKNOWN',
    discipline     TEXT DEFAULT 'UNKNOWN',
    revision       TEXT DEFAULT 'UNKNOWN',
    document_date  TEXT DEFAULT 'UNKNOWN',
    status         TEXT DEFAULT 'UNKNOWN',
    language       TEXT DEFAULT 'UNKNOWN',
    page_count     INTEGER,
    sheet_count    INTEGER,
    image_count    INTEGER,
    table_count    INTEGER,
    source_type    TEXT DEFAULT 'UNKNOWN',            -- knowledge|garbage|legacy|cad|unsupported
    index_status   TEXT DEFAULT 'DISCOVERED',
    index_note     TEXT,
    parser_used    TEXT,
    extraction_confidence REAL,
    duplicate_tag  TEXT,                              -- EXACT_DUPLICATE etc.
    duplicate_of   INTEGER,                           -- file_id of canonical copy
    supersedes     INTEGER,                           -- file_id (revision chain, §27)
    superseded_by  INTEGER,
    pipeline_version TEXT,
    discovered_at  REAL,
    indexed_at     REAL
);
CREATE INDEX IF NOT EXISTS ix_files_hash ON files(content_hash);
CREATE INDEX IF NOT EXISTS ix_files_state ON files(index_status);
CREATE INDEX IF NOT EXISTS ix_files_project ON files(project);

CREATE TABLE IF NOT EXISTS chunks(
    chunk_id   INTEGER PRIMARY KEY,
    file_id    INTEGER NOT NULL REFERENCES files(file_id),
    ord        INTEGER NOT NULL,           -- order within file
    level      TEXT DEFAULT 'paragraph',   -- document|section|paragraph|row|region
    parent_ord INTEGER,                    -- hierarchical chunking (§29)
    page       INTEGER,                    -- 1-based page/sheet/slide; NULL if n/a
    locator    TEXT,                       -- e.g. 'Sheet: BQ!A1:F40', 'Slide 3', 'p.27'
    text       TEXT NOT NULL,
    vec_row    INTEGER,                    -- dense index row (memmap backend)
    embedded   INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_chunks_file ON chunks(file_id);
CREATE INDEX IF NOT EXISTS ix_chunks_vecrow ON chunks(vec_row);

CREATE TABLE IF NOT EXISTS ingest_log(
    id INTEGER PRIMARY KEY,
    ts REAL, file_id INTEGER, event TEXT, detail TEXT
);
"""


class Manifest:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        # check_same_thread=False: hydration/metadata reads may occur from the
        # retrieval thread pool; sqlite3 serialized mode makes reads safe.
        self.con = sqlite3.connect(self.db_path, check_same_thread=False)
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA synchronous=NORMAL")
        self.con.executescript(SCHEMA)
        self.con.row_factory = sqlite3.Row

    def close(self):
        self.con.close()

    # ------------------------------------------------------------ files
    def upsert_file(self, rec: dict) -> tuple[int, str]:
        """Insert or refresh a discovered file. Returns (file_id, disposition)
        where disposition is 'new' | 'unchanged' | 'updated'."""
        row = self.con.execute(
            "SELECT file_id, content_hash, size, mtime_ns FROM files WHERE original_path=?",
            (rec["original_path"],)).fetchone()
        now = time.time()
        if row is None:
            cols = {**rec, "discovered_at": now}
            keys = ",".join(cols)
            q = ",".join("?" * len(cols))
            cur = self.con.execute(
                f"INSERT INTO files({keys}) VALUES({q})", list(cols.values()))
            return cur.lastrowid, "new"
        if (row["content_hash"] == rec["content_hash"]
                and row["size"] == rec["size"]):
            return row["file_id"], "unchanged"
        # content changed under the same path -> mark UPDATED; derived data
        # for the old hash is superseded and will be re-ingested
        sets = ", ".join(f"{k}=?" for k in rec)
        self.con.execute(
            f"UPDATE files SET {sets}, index_status='UPDATED' WHERE file_id=?",
            [*rec.values(), row["file_id"]])
        return row["file_id"], "updated"

    def set_state(self, file_id: int, state: str, note: str = ""):
        assert state in STATES, state
        self.con.execute(
            "UPDATE files SET index_status=?, index_note=?, indexed_at=? WHERE file_id=?",
            (state, note[:500], time.time(), file_id))
        self.con.execute(
            "INSERT INTO ingest_log(ts,file_id,event,detail) VALUES(?,?,?,?)",
            (time.time(), file_id, state, note[:500]))

    def get(self, file_id: int):
        return self.con.execute("SELECT * FROM files WHERE file_id=?", (file_id,)).fetchone()

    def by_state(self, *states: str) -> list:
        q = ",".join("?" * len(states))
        return self.con.execute(
            f"SELECT * FROM files WHERE index_status IN ({q})", states).fetchall()

    # ------------------------------------------------------------ duplicates (§28)
    def tag_duplicates(self) -> int:
        """Tag files sharing a content hash. Keeps ALL of them; the earliest
        discovered copy becomes canonical, the rest EXACT_DUPLICATE -> it."""
        n = 0
        rows = self.con.execute(
            "SELECT content_hash, COUNT(*) c FROM files WHERE hash_kind='blake2b' "
            "GROUP BY content_hash HAVING c > 1").fetchall()
        for r in rows:
            group = self.con.execute(
                "SELECT file_id FROM files WHERE content_hash=? ORDER BY discovered_at, file_id",
                (r["content_hash"],)).fetchall()
            canon = group[0]["file_id"]
            for g in group[1:]:
                self.con.execute(
                    "UPDATE files SET duplicate_tag='EXACT_DUPLICATE', duplicate_of=? "
                    "WHERE file_id=?", (canon, g["file_id"]))
                n += 1
        self.con.commit()
        return n

    # ------------------------------------------------------------ chunks
    def replace_chunks(self, file_id: int, chunks: list[dict]) -> list[int]:
        self.con.execute("DELETE FROM chunks WHERE file_id=?", (file_id,))
        ids = []
        for ch in chunks:
            cur = self.con.execute(
                "INSERT INTO chunks(file_id,ord,level,parent_ord,page,locator,text) "
                "VALUES(?,?,?,?,?,?,?)",
                (file_id, ch["ord"], ch.get("level", "paragraph"),
                 ch.get("parent_ord"), ch.get("page"), ch.get("locator"), ch["text"]))
            ids.append(cur.lastrowid)
        return ids

    def chunk(self, chunk_id: int):
        return self.con.execute(
            "SELECT c.*, f.original_path, f.filename, f.project, f.revision, "
            "f.document_type, f.superseded_by "
            "FROM chunks c JOIN files f ON f.file_id=c.file_id WHERE c.chunk_id=?",
            (chunk_id,)).fetchone()

    # ------------------------------------------------------------ stats
    def stats(self) -> dict:
        s = {"files_total": self.con.execute("SELECT COUNT(*) FROM files").fetchone()[0],
             "chunks_total": self.con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]}
        for st, c in self.con.execute(
                "SELECT index_status, COUNT(*) FROM files GROUP BY index_status"):
            s[f"state_{st}"] = c
        s["duplicates_tagged"] = self.con.execute(
            "SELECT COUNT(*) FROM files WHERE duplicate_tag IS NOT NULL").fetchone()[0]
        return s

    def commit(self):
        self.con.commit()
