r"""
ingest.py — build / update the index.

Usage:
  python ingest.py <root> [<root> ...]   index these folders (recursive)
  python ingest.py --all                 index all of P:\ (minus EXCLUDE_DIRS)
  python ingest.py --loose               index only loose files in P:\ root
  python ingest.py --reset               wipe the index first (add before roots)

Resumable: unchanged files (same path+mtime+size, status ok) are skipped.
Per-file errors are logged to the files table and do not stop the run.
"""

import os, sys, time
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass
import rag

EMBED_BATCH = 48     # texts per Ollama embed call


def keep_system_awake():
    """Tell Windows not to sleep while ingest runs. Auto-cleared when the process exits.
    The overnight run kept getting suspended (Win11 modern standby) which froze indexing
    for hours; this keeps the system (not the display) awake for the duration of the run."""
    try:
        import ctypes
        ES_CONTINUOUS = 0x80000000
        ES_SYSTEM_REQUIRED = 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        print("[awake] system sleep suppressed for the duration of this run")
    except Exception as e:
        print(f"[awake] could not suppress sleep: {e}")


def _wanted(fn):
    # skip Office lock/temp files like "~$report.docx" (not real documents)
    if fn.startswith("~$"):
        return False
    ext = os.path.splitext(fn)[1].lower()
    return ext in rag.TEXT_EXTS or ext in rag.SKIP_EXTS


def iter_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d.lower() not in rag.EXCLUDE_DIRS]
        for fn in filenames:
            if _wanted(fn):
                yield os.path.join(dirpath, fn), os.path.splitext(fn)[1].lower()


def loose_files(root):
    for fn in os.listdir(root):
        p = os.path.join(root, fn)
        if os.path.isfile(p) and _wanted(fn):
            yield p, os.path.splitext(fn)[1].lower()


def already_done(con, path, mtime, size):
    r = con.execute("SELECT mtime,size,status FROM files WHERE path=?", (path,)).fetchone()
    return r is not None and r[0] == mtime and r[1] == size and r[2] == "ok"


def index_file(con, path, ext, force=False, note_tag=""):
    st = os.stat(path)
    mtime, size = st.st_mtime, st.st_size
    if not force and already_done(con, path, mtime, size):
        return "skip"
    name = os.path.basename(path)
    pfx = (note_tag + " ") if note_tag else ""   # marks the row as processed by this pass (resumable)

    # remove any prior rows for this path (re-index); orphaned vectors are ignored at query time
    old = con.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()
    if old:
        con.execute("DELETE FROM chunks WHERE file_id=?", (old[0],))
        con.execute("DELETE FROM files WHERE id=?", (old[0],))

    if ext in rag.SKIP_EXTS:
        con.execute("INSERT INTO files(path,name,ext,mtime,size,status,note,n_chunks,indexed_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?)",
                    (path, name, ext, mtime, size, "skipped", "legacy format (v1)", 0, time.time()))
        con.commit()
        return "skipped"

    try:
        pages = rag.extract(path, ext)
    except Exception as e:
        con.execute("INSERT INTO files(path,name,ext,mtime,size,status,note,n_chunks,indexed_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?)",
                    (path, name, ext, mtime, size, "error", (pfx + f"extract: {e}")[:300], 0, time.time()))
        con.commit()
        return "error"

    # build (page, chunk_text) list
    items = []
    for page, text in pages:
        for ch in rag.chunk_text(text):
            items.append((page, ch))

    if not items:
        con.execute("INSERT INTO files(path,name,ext,mtime,size,status,note,n_chunks,indexed_at)"
                    " VALUES(?,?,?,?,?,?,?,?,?)",
                    (path, name, ext, mtime, size, "ok", (pfx + "no extractable text"), 0, time.time()))
        con.commit()
        return "empty"

    cur = con.execute("INSERT INTO files(path,name,ext,mtime,size,status,note,n_chunks,indexed_at)"
                      " VALUES(?,?,?,?,?,?,?,?,?)",
                      (path, name, ext, mtime, size, "ok", (pfx.strip()), len(items), time.time()))
    file_id = cur.lastrowid

    # embed in batches, append vectors, insert chunk rows
    ordn = 0
    for b in range(0, len(items), EMBED_BATCH):
        batch = items[b:b + EMBED_BATCH]
        vecs = rag.embed([t for _, t in batch])
        first_row = rag.append_vectors(vecs)
        for k, (page, ch) in enumerate(batch):
            con.execute("INSERT INTO chunks(file_id,ord,page,vec_row,text) VALUES(?,?,?,?,?)",
                        (file_id, ordn, page, first_row + k, ch))
            ordn += 1
    con.commit()
    return "ok"


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    if not rag.acquire_lock():
        print("[lock] another ingest is already running — exiting (no work done)")
        return
    keep_system_awake()
    con = rag.connect()
    if "--reset" in args:
        args.remove("--reset")
        con.execute("DELETE FROM chunks")
        con.execute("DELETE FROM files")
        con.commit()
        if os.path.exists(rag.VEC_PATH):
            os.remove(rag.VEC_PATH)
        print("[reset] index wiped")

    rag.save_meta({"dim": rag.EMBED_DIM, "embed_model": rag.EMBED_MODEL})

    # build the work list
    files = []
    force_mode = False
    tag = ""
    attempt_log = None
    current_marker = None
    if "--redocling" in args:
        # Full re-parse of every PDF with Docling (structured/layout + OCR). Resumable:
        # files already tagged 'docling' are skipped; each file is reprocessed in place
        # (old rows replaced) so the index stays usable during the multi-hour/day run.
        # ROBUSTNESS: Docling/RapidOCR can hang forever on a pathological scan with no internal
        # timeout. We record each file in an attempt-log BEFORE processing it; if the run is then
        # killed mid-file (by the health-monitor's stall restart) the relaunch SKIPS that file
        # instead of re-hanging on it. So a poison file costs one stall cycle, not an infinite loop.
        args.remove("--redocling")
        rag.DOCLING_ENABLED = True
        force_mode = True
        tag = "docling"
        attempt_log = os.path.join(rag.INDEX_DIR, "docling_attempted.txt")
        attempted = set()
        if os.path.exists(attempt_log):
            with open(attempt_log, encoding="utf-8") as f:
                attempted = {ln.strip() for ln in f if ln.strip()}
        rows = con.execute(
            "SELECT path FROM files WHERE ext='.pdf' "
            "AND (note IS NULL OR note NOT LIKE 'docling%')").fetchall()
        files = [(p, ".pdf") for (p,) in rows if os.path.exists(p) and p not in attempted]
        print(f"[redocling] {len(files)} PDFs to (re)parse with Docling "
              f"(skipped {len(attempted)} already-attempted; DOCLING_ENABLED={rag.DOCLING_ENABLED})")
    elif "--reocr" in args:
        # Second pass: re-process only the scanned/image PDFs that yielded no text
        # (status ok, 0 chunks). Requires OCR; we force it on for this run.
        args.remove("--reocr")
        rag.OCR_ENABLED = True
        rows = con.execute(
            "SELECT path FROM files WHERE status='ok' AND n_chunks=0").fetchall()
        for (p,) in rows:                       # drop old rows so they get re-indexed
            old = con.execute("SELECT id FROM files WHERE path=?", (p,)).fetchone()
            if old:
                con.execute("DELETE FROM chunks WHERE file_id=?", (old[0],))
                con.execute("DELETE FROM files WHERE id=?", (old[0],))
        con.commit()
        files = [(p, os.path.splitext(p)[1].lower()) for (p,) in rows if os.path.exists(p)]
        print(f"[reocr] {len(files)} no-text files to OCR (RAG_OCR={'1' if rag.OCR_ENABLED else '0'})")
    elif "--refresh" in args:
        # Nightly incremental update: walk ALL of P:\ (minus rag.EXCLUDE_DIRS), Docling-parse new
        # or changed files (unchanged ones skip via already_done's path+mtime+size check). HANG
        # PROTECTION for unattended runs: skip any file on the poison list, and write the current
        # file to a marker BEFORE touching it. The nightly watchdog, on a stall, reads that marker,
        # adds the file to the poison list, kills only this ingest, and relaunches -> it skips the
        # bad file next time instead of re-hanging forever.
        args.remove("--refresh")
        rag.DOCLING_ENABLED = True
        tag = "docling"
        poison_log = os.path.join(rag.INDEX_DIR, "docling_poison.txt")
        current_marker = os.path.join(rag.INDEX_DIR, "refresh_current.txt")
        poison = set()
        if os.path.exists(poison_log):
            with open(poison_log, encoding="utf-8") as f:
                poison = {ln.strip() for ln in f if ln.strip()}
        # Walk the whole drive. Print a heartbeat every few thousand files (flushed) so the log
        # keeps growing during the slow pCloud enumeration — otherwise the nightly watchdog's
        # "log stopped growing" stall check would false-trigger before the first file is indexed.
        files, seen = [], 0
        for (p, e) in iter_files(rag.DRIVE_ROOT):
            seen += 1
            if seen % 3000 == 0:
                print(f"[refresh] scanning... {seen} files seen", flush=True)
            if p not in poison:
                files.append((p, e))
        print(f"[refresh] {len(files)} candidate files under {rag.DRIVE_ROOT} "
              f"(skipping {len(poison)} known-poison; DOCLING_ENABLED={rag.DOCLING_ENABLED})", flush=True)
    elif "--all" in args:
        files = list(iter_files(rag.DRIVE_ROOT))
    elif "--loose" in args:
        files = list(loose_files(rag.DRIVE_ROOT))
    else:
        for root in args:
            if os.path.isdir(root):
                files.extend(iter_files(root))
            elif os.path.isfile(root):
                ext = os.path.splitext(root)[1].lower()
                files.append((root, ext))
            else:
                print(f"[warn] not found: {root}")

    total = len(files)
    print(f"[ingest] {total} candidate files; embed_model={rag.EMBED_MODEL}")
    counts = {"ok": 0, "skip": 0, "skipped": 0, "empty": 0, "error": 0}
    t0 = time.time()
    for i, (path, ext) in enumerate(files, 1):
        if attempt_log is not None:
            # mark this file as attempted BEFORE we touch it, so a hang+kill won't re-hang on it
            with open(attempt_log, "a", encoding="utf-8") as f:
                f.write(path + "\n")
                f.flush()
        if current_marker is not None:
            # record the in-progress file (overwrite); the nightly watchdog reads this on a stall
            # to learn which file hung, so it can be poison-listed and skipped on relaunch
            with open(current_marker, "w", encoding="utf-8") as f:
                f.write(path)
                f.flush()
        try:
            r = index_file(con, path, ext, force=force_mode, note_tag=tag)
        except Exception as e:
            r = "error"
            print(f"  [error] {path}: {e}")
        counts[r] = counts.get(r, 0) + 1
        if i % 10 == 0 or i == total:
            el = time.time() - t0
            print(f"  {i}/{total}  ok={counts['ok']} skip={counts['skip']} "
                  f"empty={counts['empty']} legacy={counts['skipped']} err={counts['error']} "
                  f"vecs={rag.vec_count()}  {el:.0f}s")
    con.close()
    print(f"[done] {counts}  total_vectors={rag.vec_count()}  {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
