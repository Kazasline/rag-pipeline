r"""Graph store + evidence-grounded extraction (spec §25–§27).

The graph is for relationships, not decoration: nodes and edges exist only
when there is recorded evidence (a chunk that mentions the code, a filename
that carries the revision, a manifest revision link). Every edge carries
provenance (file_id, chunk_id) so a multi-hop answer can cite its path.

Storage: SQLite at 11_GRAPH/graph.sqlite — durable, restart-safe, zero
services. LightRAG/HippoRAG-style associative expansion is implemented as
seeded k-hop traversal from retrieval hits; heavier graph frameworks are a
benchmark-gated upgrade (§48), not a default dependency.

Node keys are 'TYPE:normalized_name', e.g. 'DOCUMENT:LAI003', 'PROJECT:dawson'.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from .sparse import harvest_ids, normalize_id

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes(
    node_id INTEGER PRIMARY KEY,
    key     TEXT NOT NULL UNIQUE,     -- TYPE:norm
    type    TEXT NOT NULL,
    name    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS edges(
    edge_id  INTEGER PRIMARY KEY,
    src      INTEGER NOT NULL REFERENCES nodes(node_id),
    dst      INTEGER NOT NULL REFERENCES nodes(node_id),
    relation TEXT NOT NULL,
    file_id  INTEGER,                 -- provenance (§25)
    chunk_id INTEGER,
    evidence TEXT
);
CREATE INDEX IF NOT EXISTS ix_edges_src ON edges(src);
CREATE INDEX IF NOT EXISTS ix_edges_dst ON edges(dst);
CREATE INDEX IF NOT EXISTS ix_edges_file ON edges(file_id);
"""

# code prefixes -> node type (extend as the corpus teaches us)
CODE_TYPES = [
    (re.compile(r"^LAI", re.I), "LAI"),
    (re.compile(r"^NCR", re.I), "NCR"),
    (re.compile(r"^VO", re.I), "VO"),
    (re.compile(r"^RFI", re.I), "RFI"),
    (re.compile(r"^(DWG|L-?\d|LD)", re.I), "DRAWING"),
    (re.compile(r"^(BQ)", re.I), "BQITEM"),
]


def _like_escape(s: str) -> str:
    """Escape SQL LIKE wildcards so a seed cannot match the whole graph."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def code_node_type(code: str) -> str:
    for rx, t in CODE_TYPES:
        if rx.match(code):
            return t
    return "DOCUMENT"


class Graph:
    def __init__(self, db_path: str | Path):
        self.con = sqlite3.connect(str(db_path), check_same_thread=False)
        self.con.executescript(SCHEMA)

    def close(self):
        self.con.close()

    # ------------------------------------------------------------ write
    def node(self, ntype: str, name: str) -> int:
        key = f"{ntype}:{normalize_id(name) if any(c.isdigit() for c in name) else name.strip().lower()}"
        row = self.con.execute("SELECT node_id FROM nodes WHERE key=?", (key,)).fetchone()
        if row:
            return row[0]
        cur = self.con.execute("INSERT INTO nodes(key,type,name) VALUES(?,?,?)",
                               (key, ntype, name.strip()))
        return cur.lastrowid

    def edge(self, src: int, dst: int, relation: str, file_id: int | None = None,
             chunk_id: int | None = None, evidence: str = ""):
        # dedupe identical assertions from the same provenance
        row = self.con.execute(
            "SELECT edge_id FROM edges WHERE src=? AND dst=? AND relation=? "
            "AND IFNULL(file_id,-1)=IFNULL(?,-1) AND IFNULL(chunk_id,-1)=IFNULL(?,-1)",
            (src, dst, relation, file_id, chunk_id)).fetchone()
        if row:
            return
        self.con.execute(
            "INSERT INTO edges(src,dst,relation,file_id,chunk_id,evidence) "
            "VALUES(?,?,?,?,?,?)", (src, dst, relation, file_id, chunk_id, evidence[:300]))

    def delete_file_edges(self, file_id: int):
        self.con.execute("DELETE FROM edges WHERE file_id=?", (file_id,))
        self.con.commit()

    # ------------------------------------------------------------ extraction
    def extract_from_file(self, file_row, chunks: list[tuple[int, dict]]):
        """Build evidence-grounded nodes/edges for one ingested file.
        file_row: manifest files row; chunks: [(chunk_id, chunk_dict)]."""
        fid = file_row["file_id"]
        doc_node = self.node("DOCUMENT", file_row["filename"])
        if file_row["project"] and file_row["project"] != "UNKNOWN":
            proj = self.node("PROJECT", file_row["project"])
            self.edge(doc_node, proj, "BELONGS_TO", file_id=fid,
                      evidence=f"path: {file_row['original_path']}")
        if file_row["revision"] and file_row["revision"] != "UNKNOWN":
            rev = self.node("REVISION", f"{file_row['filename']}@{file_row['revision']}")
            self.edge(doc_node, rev, "REVISES", file_id=fid,
                      evidence=f"revision {file_row['revision']} in filename")
        # codes mentioned in content -> REFERENCES edges with chunk provenance
        own_codes = {normalize_id(c) for c in harvest_ids(file_row["filename"])}
        for chunk_id, ch in chunks:
            for raw in harvest_ids(ch["text"], limit=40):
                norm = normalize_id(raw)
                if norm in own_codes:
                    continue
                ref = self.node(code_node_type(raw), raw)
                self.edge(doc_node, ref, "REFERENCES", file_id=fid,
                          chunk_id=chunk_id,
                          evidence=ch["text"][max(0, ch["text"].find(raw) - 60):
                                              ch["text"].find(raw) + 60])
        self.con.commit()

    def link_supersedes(self, mf_con: sqlite3.Connection):
        """Mirror manifest revision chains (§27) into SUPERSEDES edges."""
        for r in mf_con.execute(
                "SELECT file_id, filename, supersedes FROM files "
                "WHERE supersedes IS NOT NULL"):
            older = mf_con.execute("SELECT filename FROM files WHERE file_id=?",
                                   (r["supersedes"],)).fetchone()
            if not older:
                continue
            newer_n = self.node("DOCUMENT", r["filename"])
            older_n = self.node("DOCUMENT", older["filename"])
            self.edge(newer_n, older_n, "SUPERSEDES", file_id=r["file_id"],
                      evidence="revision chain from manifest")
        self.con.commit()

    # ------------------------------------------------------------ read
    def neighborhood(self, seed_names: list[str], hops: int = 1,
                     limit: int = 200) -> dict:
        """K-hop expansion from seed entity names/codes. Returns
        {nodes: [...], edges: [...], chunk_ids: [...]} — chunk_ids feed back
        into retrieval as graph-sourced candidates."""
        seeds = []
        for name in seed_names:
            norm = normalize_id(name) if any(c.isdigit() for c in name) else name.strip().lower()
            # Escape LIKE wildcards in BOTH patterns. Escaping only the name
            # left `key LIKE '%:%'`, which matches every node — keys are all
            # "TYPE:value" — so a seed of "%" still pulled in the whole graph.
            safe_name = _like_escape(name.strip())
            safe_norm = _like_escape(norm)
            rows = self.con.execute(
                "SELECT node_id FROM nodes WHERE key LIKE ? ESCAPE '\\' "
                "OR name LIKE ? ESCAPE '\\'",
                (f"%:{safe_norm}", f"%{safe_name}%")).fetchall()
            seeds.extend(r[0] for r in rows)
        frontier, visited = set(seeds), set(seeds)
        # Track how many hops from a seed each node is, so results can be
        # ordered by graph distance rather than by insertion order.
        depth = {n: 0 for n in seeds}
        edges_out = []
        for hop in range(max(0, hops)):
            if not frontier or len(edges_out) >= limit:
                break
            q = ",".join("?" * len(frontier))
            rows = self.con.execute(
                f"SELECT edge_id, src, dst, relation, file_id, chunk_id, evidence "
                f"FROM edges WHERE src IN ({q}) OR dst IN ({q})",
                [*frontier, *frontier]).fetchall()
            nxt = set()
            for eid, src, dst, rel, fid, cid, ev in rows:
                edges_out.append({"src": src, "dst": dst, "relation": rel,
                                  "file_id": fid, "chunk_id": cid,
                                  "evidence": ev, "hop": hop + 1})
                for n in (src, dst):
                    if n not in visited:
                        nxt.add(n)
                        visited.add(n)
                        depth[n] = hop + 1
                if len(edges_out) >= limit:
                    break
            frontier = nxt
        node_rows = []
        if visited:
            q = ",".join("?" * len(visited))
            node_rows = [dict(zip(("node_id", "type", "name"), r))
                         for r in self.con.execute(
                             f"SELECT node_id, type, name FROM nodes WHERE node_id IN ({q})",
                             list(visited))]
        # Rank by graph distance, then by how many edges support the chunk.
        #
        # Previously this returned `sorted(set(...))` — chunk_ids ascending,
        # i.e. INGESTION ORDER. Since RRF scores by list position, the graph leg
        # was injecting a fixed positional bias rather than a relevance signal,
        # which is not something that can earn its place under §89.
        best_hop: dict[int, int] = {}
        support: dict[int, int] = {}
        for e in edges_out:
            cid = e["chunk_id"]
            if not cid:
                continue
            hop = e.get("hop", 1)
            best_hop[cid] = min(best_hop.get(cid, hop), hop)
            support[cid] = support.get(cid, 0) + 1
        chunk_ids = sorted(best_hop, key=lambda c: (best_hop[c], -support[c], c))
        return {"nodes": node_rows, "edges": edges_out, "chunk_ids": chunk_ids,
                "chunk_hops": best_hop}

    def stats(self) -> dict:
        return {"nodes": self.con.execute("SELECT COUNT(*) FROM nodes").fetchone()[0],
                "edges": self.con.execute("SELECT COUNT(*) FROM edges").fetchone()[0]}
