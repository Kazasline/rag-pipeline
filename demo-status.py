# demo-status.py — proof the RAG system is running + indexed, at the file level.
# Run:  .\.venv\Scripts\python.exe demo-status.py
import sys, os
sys.path.insert(0, r"P:\RAG Database\pipeline")
import rag

con = rag.connect()
n_files  = con.execute("SELECT COUNT(*) FROM files").fetchone()[0]
n_ok     = con.execute("SELECT COUNT(*) FROM files WHERE status='ok'").fetchone()[0]
n_docl   = con.execute("SELECT COUNT(*) FROM files WHERE note LIKE 'docling%'").fetchone()[0]
n_chunks = con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
n_vec    = rag.vec_count()

print(f"\n  RAG DATABASE — live, local")
print(f"  index: {rag.INDEX_DIR}")
print(f"  {'='*56}")
print(f"  Documents indexed ........ {n_files:>8,}")
print(f"     successfully parsed ... {n_ok:>8,}")
print(f"     via Docling (OCR) ..... {n_docl:>8,}")
print(f"  Searchable SECTIONS ...... {n_chunks:>8,}   (chunks)")
print(f"  Vector embeddings ........ {n_vec:>8,}   x {rag.EMBED_DIM}-dim  [{rag.EMBED_MODEL}, local]")
print(f"\n  Sample of indexed files (file level):")
for path, nch, note in con.execute(
        "SELECT path, n_chunks, note FROM files WHERE status='ok' AND n_chunks>0 "
        "ORDER BY n_chunks DESC LIMIT 7").fetchall():
    print(f"    - {os.path.basename(path)[:52]:52} {nch:>4} sections  [{(note or '').strip()[:7]}]")
print()
