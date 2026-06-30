"""
query.py — retrieve from the index (and optionally render images / synthesize an answer).

Usage:
  python query.py "your question"
  python query.py "..." --k 8 --json
  python query.py "..." --render        also render matching PDF pages to PNG
  python query.py "..." --answer        synthesize an answer with the local chat model

Designed to be called by the OpenClaw skill. Default output is compact JSON on stdout.
"""

import os, sys, json, sqlite3, hashlib
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass
import rag

CHAT_MODEL = "gemma4:latest"     # local answer synthesis (only with --answer)


def search(question, k=6, pool=1500):   # large pool so live chunks surface past orphaned vectors
    import numpy as np
    n = rag.vec_count()
    if n == 0:
        return []
    dim = rag.load_meta().get("dim", rag.EMBED_DIM)
    vectors = np.memmap(rag.VEC_PATH, dtype="float32", mode="r", shape=(n, dim))
    q = np.asarray(rag.embed([question])[0], dtype="float32")
    scores = vectors @ q                       # cosine (all normalized)
    pool = min(pool, n)
    top = np.argpartition(-scores, pool - 1)[:pool]
    top = top[np.argsort(-scores[top])]        # sort the pool by score desc

    con = rag.connect()
    qmarks = ",".join("?" * len(top))
    rows = con.execute(
        f"SELECT c.vec_row,c.page,c.text,f.name,f.path,f.ext "
        f"FROM chunks c JOIN files f ON f.id=c.file_id "
        f"WHERE c.vec_row IN ({qmarks})", [int(x) for x in top]).fetchall()
    con.close()
    by_row = {r[0]: r for r in rows}

    results = []
    for row in top:
        r = by_row.get(int(row))
        if not r:
            continue                            # orphaned vector (re-indexed) -> skip
        results.append({
            "score": round(float(scores[row]), 4),
            "page": r[1], "text": r[2],
            "name": r[3], "path": r[4], "ext": r[5],
        })
        if len(results) >= k:
            break
    return results


def group_by_file(results):
    files = {}
    for r in results:
        f = files.setdefault(r["path"], {
            "name": r["name"], "path": r["path"], "ext": r["ext"],
            "best_score": r["score"], "pages": [], "snippets": []})
        f["best_score"] = max(f["best_score"], r["score"])
        if r["page"] and r["page"] not in f["pages"]:
            f["pages"].append(r["page"])
        f["snippets"].append(r["text"][:400])
    return sorted(files.values(), key=lambda x: -x["best_score"])


RENDER_MAX_PX = 1600   # cap longest side; huge A0/A1 sheets at fixed DPI made 18MB PNGs that
                       # overwhelmed the MCP image transport (timeouts). Pixel-budget keeps every
                       # render small (~0.2-0.8MB) and fast, while staying readable on a phone.

def render_pages(results, max_imgs=4):
    """Render matching PDF pages to PNG (size-capped); returns list of image paths."""
    import fitz
    imgs, seen = [], set()
    for r in results:
        if r["ext"] != ".pdf" or not r["page"]:
            continue
        key = (r["path"], r["page"])
        if key in seen:
            continue
        seen.add(key)
        h = hashlib.md5(r["path"].encode("utf-8")).hexdigest()[:10]
        out = os.path.join(rag.RENDER_DIR, f"{h}_p{r['page']}_x{RENDER_MAX_PX}.png")
        if not os.path.exists(out):
            try:
                doc = fitz.open(r["path"])
                page = doc[r["page"] - 1]
                maxdim = max(page.rect.width, page.rect.height) or 1.0
                zoom = min(2.0, RENDER_MAX_PX / maxdim)   # shrink big sheets, modestly upscale small
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
                pix.save(out)
                doc.close()
            except Exception:
                continue
        imgs.append(out)
        if len(imgs) >= max_imgs:
            break
    return imgs


def synthesize(question, results):
    ctx = "\n\n".join(f"[{i+1}] {r['name']} (p{r['page']}):\n{r['text']}"
                      for i, r in enumerate(results))
    prompt = (
        "Answer the question using ONLY the context below. Cite sources as [n]. "
        "If the answer isn't in the context, say you couldn't find it.\n\n"
        f"Context:\n{ctx}\n\nQuestion: {question}\nAnswer:")
    out = rag._post("/api/generate",
                    {"model": CHAT_MODEL, "prompt": prompt, "stream": False})
    return out.get("response", "").strip()


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__); return
    flags = {"--json", "--render", "--answer"}
    opts = [a for a in args if a in flags or a == "--k"]
    k = 6
    if "--k" in args:
        i = args.index("--k")
        k = int(args[i + 1]); del args[i:i + 2]
    question = " ".join(a for a in args if a not in flags)

    results = search(question, k=k)
    files = group_by_file(results)
    payload = {"query": question, "files": files, "results": results, "images": []}

    if "--render" in args:
        payload["images"] = render_pages(results)
    if "--answer" in args:
        payload["answer"] = synthesize(question, results) if results else "No matches found."

    if "--json" in args:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if not files:
            print("No matches."); return
        print(f"Top matches for: {question}\n")
        for f in files[:k]:
            pages = f"  pages {f['pages']}" if f["pages"] else ""
            print(f"• {f['name']}  (score {f['best_score']}){pages}\n  {f['path']}")
            print(f"  …{f['snippets'][0][:200]}…\n")
        if payload["images"]:
            print("Rendered images:")
            for p in payload["images"]:
                print("  " + p)
        if "answer" in payload:
            print("\nANSWER:\n" + payload["answer"])


if __name__ == "__main__":
    main()
