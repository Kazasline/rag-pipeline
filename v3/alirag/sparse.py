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

from .terms import STOPWORDS

# doc/drawing/clause codes: letters+digits joined by - _ / .  (min 2 chars each side)
ID_RE = re.compile(
    r"\b([A-Za-z]{1,10}(?:[-_/][A-Za-z0-9]{1,12}){1,6}|[A-Za-z]{1,6}\d{1,6}[A-Za-z]?|"
    r"\d{1,4}[-/]\d{1,4}(?:[-/][A-Za-z0-9]{1,8})?)\b")
# tokens that look like codes but are noise
ID_STOP = {"a4", "a3", "a1", "a0", "no1", "v1", "v2", "p1", "p2", "x2"}

# Revision markers. They match ID_RE and are everywhere — nearly every drawing
# sheet carries one — so treating them as document identifiers means "revision
# R01" names tens of thousands of files at once (round-3 reviewer R3-2).
REV_TOKEN_RE = re.compile(r"^R(?:EV)?[-_. ]?\d{1,2}[A-Z]?$", re.IGNORECASE)


def is_document_code(tok: str) -> bool:
    """Does this token plausibly IDENTIFY a document, rather than merely look
    code-shaped?

    `LAI-003`, `L-201`, `KP-980ASPEN-CS` identify. `R01`, `D7`, `L2` do not:
    they are revision and detail markers shared across the whole corpus, and
    admitting them let an off-corpus question ride into evidence on the back of
    a revision number.
    """
    if REV_TOKEN_RE.match(tok):
        return False
    has_sep = any(c in tok for c in "-_/")
    letters = sum(c.isalpha() for c in tok)
    digits = sum(c.isdigit() for c in tok)
    if has_sep:
        # a separated code still has to carry enough on each side: "L-2" does not
        return letters >= 1 and digits >= 2 and len(tok) >= 4
    # unseparated: needs real length, e.g. "LAI003" but not "D7"/"L2"/"T12"
    return letters >= 2 and digits >= 3


def normalize_id(tok: str) -> str:
    return re.sub(r"[-_/.]", "", tok).upper()


# Hard ceiling on variants returned for one chunk. The per-code window bound
# (8 parts -> at most 36 windows) bounds each code, but a chunk can contain
# many codes, and every variant becomes a row in the ids table at index time
# over 662k files. The revert matrix found the per-code bound unguarded; this
# is the bound that actually limits the work.
MAX_CODE_VARIANTS = 400


def code_variants(text: str, limit: int = 60) -> set:
    """Normalized codes in *text*, INCLUDING contiguous sub-sequences of
    separator-joined parts.

    `harvest_ids` is greedy across hyphen runs, so "L-201-RevB" yields the
    single token `L201REVB` and an equality test against the query's `L201`
    fails. Round-4 reviewer N4-3: `find L-201` returned INSUFFICIENT while
    `L-201-RevB.pdf` sat in the nearest-match list — and revision-suffixed and
    prefix-qualified sheet names ("DWG-L-201-R03", "Dawson-L-201-planting")
    are the norm in this corpus, so the §9 exact path was broken for most real
    drawing filenames.

    Splitting into parts and re-joining windows recovers the identifier
    wherever it sits inside a longer name.
    """
    out: set = set()
    for raw in harvest_ids(text, limit=limit):
        out.add(normalize_id(raw))
        parts = [p for p in re.split(r"[-_/.]", raw) if p]
        if len(parts) < 2:
            continue
        # No per-code slice is needed: ID_RE allows at most 6 separator
        # repetitions, so a harvested code has at most 7 parts and the window
        # count per code is bounded at 7*8/2 = 28. An earlier `parts[:8]` here
        # was unreachable — the revert matrix reported it as an unguarded fix,
        # and it was unguardable, because no input can reach it. The bound that
        # does real work is MAX_CODE_VARIANTS on the total.
        for i in range(len(parts)):
            for j in range(i + 1, len(parts) + 1):
                window = "".join(parts[i:j]).upper()
                # a single bare part is not an identifier on its own
                if j - i > 1 or len(window) >= 4:
                    out.add(window)
        if len(out) >= MAX_CODE_VARIANTS:
            break
    return out


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
        self._df_cache: dict[str, int] = {}

    def close(self):
        self.con.close()

    # ------------------------------------------------------------ write
    def index_chunks(self, rows: list[dict]):
        """rows: {chunk_id, file_id, text, filename, project}"""
        for r in rows:
            self.con.execute(
                "INSERT INTO fts(text, filename, project, chunk_id) VALUES(?,?,?,?)",
                (r["text"], r["filename"], r.get("project", ""), r["chunk_id"]))
            # Index every code VARIANT, not just the greedy whole token.
            #
            # Round-5 reviewer F5-2: `code_variants()` was added to the
            # verifier only, so the ids table still stored `L201REVB` for
            # `L-201-RevB` and `search_ids` looked up `norm = 'L201'` — the §9
            # exact leg found nothing for the majority of real drawing
            # filenames, and FAST fell back to hoping BM25 ranked the right
            # sheet into the top 20 across 662k files. Exactly what the exact
            # path exists to avoid.
            for norm in code_variants(r["text"]):
                self.con.execute(
                    "INSERT INTO ids(norm, raw, chunk_id, file_id, in_filename) "
                    "VALUES(?,?,?,?,0)",
                    (norm, norm, r["chunk_id"], r["file_id"]))
            for norm in code_variants(r["filename"]):
                self.con.execute(
                    "INSERT INTO ids(norm, raw, chunk_id, file_id, in_filename) "
                    "VALUES(?,?,?,?,1)",
                    (norm, norm, r["chunk_id"], r["file_id"]))
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
        r"""Escape a free-text query into an FTS5 OR-of-terms expression.

        Stopwords are dropped first. This is an OR, so every retained token can
        pull in documents on its own: leaving `the` in meant a question about a
        pump warranty matched documents whose only commonality was the word
        "the", and the sparse leg reported a lexical match on them (§39).

        Short/symbolic tokens that are not stopwords are kept — codes like
        `L-201` and units like `50mm` are exactly what this leg is for.
        """
        toks = [t for t in re.findall(r"[^\s\"'()*:^]+", query) if t]
        content = [t for t in toks if t.lower().strip(".,;:!?") not in STOPWORDS]
        # If the query is nothing BUT stopwords there is no topical term to
        # search for. Falling back to the raw tokens would match the whole
        # corpus, so the honest result is no lexical hits at all.
        if not content:
            return '""'
        return " OR ".join(f'"{t}"' for t in content[:24])

    def total_chunks(self) -> int:
        return self.con.execute("SELECT COUNT(*) FROM fts").fetchone()[0]

    def doc_freq(self, terms) -> dict:
        """How many indexed chunks contain each term.

        This is what makes the verifier's relevance floor MEASURED rather than
        guessed: a term appearing in a quarter of the corpus discriminates
        nothing, and only the corpus can say which terms those are. Cached per
        connection because query terms repeat heavily.
        """
        out: dict[str, int] = {}
        for t in terms:
            if t in self._df_cache:
                out[t] = self._df_cache[t]
                continue
            try:
                n = self.con.execute(
                    "SELECT COUNT(*) FROM fts WHERE fts MATCH ?",
                    (f'"{t}"',)).fetchone()[0]
            except sqlite3.OperationalError:
                continue          # unparseable term: no statistic, not a zero
            self._df_cache[t] = n
            out[t] = n
        return out

    def search(self, query: str, k: int = 20, project: str | None = None,
               trace=None) -> list[dict]:
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
        except sqlite3.OperationalError as e:
            # An unparseable FTS expression is a DEGRADED RUN, not an empty
            # result. Returning [] silently made a dead lexical leg look
            # identical to "no documents matched" (round-3 reviewer R3-9).
            if trace is not None:
                trace.set("sparse_error", str(e)[:200])
            return []
        # bm25() is lower-is-better; convert to a positive score
        return [{"chunk_id": int(cid), "score": -float(rank), "source": "sparse"}
                for cid, rank in rows]

    def search_ids(self, query: str, k: int = 20,
                   allowed_chunks: set | None = None) -> list[dict]:
        """Exact identifier lookup: any code-like token in the query that
        matches a harvested code is a top-priority hit (§9 exact path).

        `allowed_chunks` scopes the leg to one project. It carries the heaviest
        RRF weight (2.0), so leaving it unscoped let a matching code from
        another project outrank everything — an isolation hole (§60).
        """
        hits: dict[int, float] = {}
        for raw in harvest_ids(query, limit=8):
            norm = normalize_id(raw)
            for cid, in_fn in self.con.execute(
                    "SELECT chunk_id, in_filename FROM ids WHERE norm=? LIMIT ?",
                    (norm, k * 8)):
                if allowed_chunks is not None and cid not in allowed_chunks:
                    continue
                # filename matches outrank body mentions
                hits[cid] = max(hits.get(cid, 0.0), 2.0 if in_fn else 1.0)
        ranked = sorted(hits.items(), key=lambda x: -x[1])[:k]
        return [{"chunk_id": cid, "score": s, "source": "exact"} for cid, s in ranked]

    def stats(self) -> dict:
        return {
            "fts_rows": self.con.execute("SELECT COUNT(*) FROM fts").fetchone()[0],
            "id_rows": self.con.execute("SELECT COUNT(*) FROM ids").fetchone()[0],
        }
