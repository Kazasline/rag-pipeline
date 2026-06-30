r"""Pre-render every matchable PDF page to the LOCAL render cache, so file_rag image retrieval
is instant and never times out fetching/rendering from the pCloud P: drive at query time.
Resumable (skips pages already rendered). CPU + disk only (no GPU) — safe to run alongside the
WhatsApp agent. Same size-cap and cache-key as query.render_pages so the cache is shared."""
import os, sys, hashlib, time, ctypes
import fitz
sys.path.insert(0, r"P:\RAG Database\pipeline")
import rag
from query import RENDER_MAX_PX


def keep_awake():
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
    except Exception:
        pass


def render_one(path, page):
    h = hashlib.md5(path.encode("utf-8")).hexdigest()[:10]
    out = os.path.join(rag.RENDER_DIR, f"{h}_p{page}_x{RENDER_MAX_PX}.png")
    if os.path.exists(out):
        return "skip"
    try:
        doc = fitz.open(path)
        pg = doc[page - 1]
        maxdim = max(pg.rect.width, pg.rect.height) or 1.0
        zoom = min(2.0, RENDER_MAX_PX / maxdim)
        pg.get_pixmap(matrix=fitz.Matrix(zoom, zoom)).save(out)
        doc.close()
        return "ok"
    except Exception:
        return "err"


def main():
    keep_awake()
    con = rag.connect()
    rows = con.execute(
        "SELECT DISTINCT f.path, c.page FROM chunks c JOIN files f ON f.id=c.file_id "
        "WHERE f.ext='.pdf' AND c.page>0 ORDER BY f.path, c.page").fetchall()
    con.close()
    total = len(rows)
    print(f"[prerender] {total} distinct PDF pages to cache", flush=True)
    counts = {"ok": 0, "skip": 0, "err": 0}
    t0 = time.time()
    for i, (path, page) in enumerate(rows, 1):
        if not os.path.exists(path):
            counts["err"] += 1
        else:
            counts[render_one(path, page)] += 1
        if i % 50 == 0 or i == total:
            print(f"  {i}/{total}  ok={counts['ok']} skip={counts['skip']} err={counts['err']}  "
                  f"{time.time()-t0:.0f}s", flush=True)
    print(f"[done] {counts}  total_pages={total}  {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
