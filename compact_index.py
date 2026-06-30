r"""Compact vectors.f32: keep ONLY vectors referenced by a live chunk, renumber contiguously,
rewrite chunks.vec_row to match. Drops orphaned vectors left behind by re-indexing so queries
are accurate and fast. Run with NO ingest/MCP holding the file."""
import os, sys, shutil
import numpy as np
sys.path.insert(0, r"P:\RAG Database\pipeline")
import rag

con = rag.connect()
dim = rag.EMBED_DIM
n_old = rag.vec_count()
old = np.memmap(rag.VEC_PATH, dtype="float32", mode="r", shape=(n_old, dim))

rows = con.execute("SELECT id, vec_row FROM chunks ORDER BY id").fetchall()
print(f"old vectors={n_old}  chunks={len(rows)}")

tmp = rag.VEC_PATH + ".compact.tmp"
updates = []
skipped = 0
with open(tmp, "wb") as f:
    new_row = 0
    for cid, vr in rows:
        if vr is None or vr < 0 or vr >= n_old:
            skipped += 1
            continue
        f.write(np.asarray(old[vr], dtype="float32").tobytes())
        updates.append((new_row, cid))
        new_row += 1

con.executemany("UPDATE chunks SET vec_row=? WHERE id=?", updates)
con.commit()
con.close()
del old  # release the memmap before replacing the file

bak = os.path.join(rag.INDEX_DIR, "vectors.f32.preCompact.bak")
shutil.copy2(rag.VEC_PATH, bak)
os.replace(tmp, rag.VEC_PATH)

new_n = rag.vec_count()
print(f"new vectors={new_n}  (wrote {len(updates)}, bad-ref skipped {skipped})")
print(f"old file backed up to {bak}")
print("OK" if new_n == len(updates) else "MISMATCH!")
