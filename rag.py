r"""
rag.py — core engine for the local P:\ RAG.

Fully local. No cloud. Stores:
  index/rag.sqlite   metadata + chunk text  (transactional, resumable)
  index/vectors.f32  raw float32 embeddings, L2-normalized, row i <-> chunks.vec_row
  index/meta.json    {dim, embed_model}

Design notes:
- Cosine similarity == dot product because every stored vector is L2-normalized.
- vectors.f32 is a flat binary (no header); N = filesize / (dim*4). memmap = instant load.
- Embeddings come from a local Ollama embedding model (default bge-m3, multilingual:
  the source docs mix English + Malay).
"""

import os, sys, json, sqlite3, struct, time, urllib.request, urllib.error

# ---------------------------------------------------------------- config
DRIVE_ROOT  = "P:\\"            # what we scan/index (source docs live here)
BASE        = r"P:\RAG Database" # scripts + source docs (this is a pCloud virtual drive)

# CRITICAL: the index + render cache MUST live on a real LOCAL disk, never on P:\.
# P:\ is a pCloud Drive virtual mount; its background sync engine rewrites files
# underneath open handles and corrupts SQLite + the memmap'd vectors.f32 mid-write.
# Storing the index on P:\ destroyed it twice (2026-06-26, "database disk image is
# malformed"). LOCALAPPDATA is a fixed local disk and is not OneDrive-synced.
# Index lives at C:\RAGData (fast local disk, NOT pCloud P:). It must NOT be under
# %LOCALAPPDATA%: the OpenClaw gateway runs its child processes (the file_rag MCP server)
# behind a copy-on-write filesystem overlay that hides the real contents of
# C:\Users\User\AppData\Local\RAGDatabase\index (listdir returns [] / vectors.f32 not found),
# so the server read an empty index. C:\RAGData is outside that overlay and fully shared
# between the interactive tooling and the gateway-spawned MCP server.
LOCAL_BASE  = r"C:\RAGData"
INDEX_DIR   = os.path.join(LOCAL_BASE, "index")
RENDER_DIR  = os.path.join(LOCAL_BASE, "renders")
DB_PATH     = os.path.join(INDEX_DIR, "rag.sqlite")
VEC_PATH    = os.path.join(INDEX_DIR, "vectors.f32")
META_PATH   = os.path.join(INDEX_DIR, "meta.json")

OLLAMA_URL  = "http://127.0.0.1:11434"
EMBED_MODEL = "bge-m3"          # 1024-dim, multilingual
EMBED_DIM   = 1024

# chunking: ~1800 chars per chunk with 20% overlap so nothing is lost across boundaries
CHUNK_SIZE  = 1800
CHUNK_OVERLAP = 360            # 20% of CHUNK_SIZE

# OCR: a large share of the source PDFs are scanned/image-only (signed letters, checklists,
# claims). When a PDF page has no text layer we render it and run Tesseract. OCR is OFF by
# default (env RAG_OCR=1 turns it on) so the main text pass stays fast; the dedicated OCR pass
# (ingest.py --reocr via run_ocr.bat) sets RAG_OCR=1. Each OCR'd chunk keeps its page number,
# so query.py can still render that exact page as an image for OpenClaw.
OCR_ENABLED = os.environ.get("RAG_OCR") == "1"
OCR_DPI     = 300
OCR_LANG    = "eng+msa"        # English + Malay (docs are bilingual); falls back to eng

# Docling: structured, layout-aware parsing (tables, reading order) with built-in OCR
# (RapidOCR). Off by default; the Docling pass (ingest.py --redocling via run_docling.bat)
# sets RAG_DOCLING=1. Uses the GPU automatically if a CUDA torch build is installed.
DOCLING_ENABLED = os.environ.get("RAG_DOCLING") == "1"

# which file types we extract text from
TEXT_EXTS   = {".pdf", ".docx", ".xlsx", ".txt", ".md", ".csv"}
# legacy formats we intentionally skip in v1 (logged, never silently dropped)
SKIP_EXTS   = {".doc", ".xls", ".ppt"}
# directories we never descend into (asset libraries / caches / noise)
EXCLUDE_DIRS = {
    "rag database", "sketchup installer", "photoshop", "pcloud backup",
    "system volume information", "screenshots", "$recycle.bin",
    "catalog", "psd library", "sketch up 3d warehouse", ".git", "node_modules",
}

os.makedirs(INDEX_DIR, exist_ok=True)
os.makedirs(RENDER_DIR, exist_ok=True)


# ---------------------------------------------------------------- storage
def connect():
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("""CREATE TABLE IF NOT EXISTS files(
        id INTEGER PRIMARY KEY,
        path TEXT UNIQUE,
        name TEXT, ext TEXT,
        mtime REAL, size INTEGER,
        status TEXT,          -- ok | skipped | error
        note TEXT,
        n_chunks INTEGER DEFAULT 0,
        indexed_at REAL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS chunks(
        id INTEGER PRIMARY KEY,
        file_id INTEGER,
        ord INTEGER,          -- chunk order within file
        page INTEGER,         -- source page/sheet (0 if n/a)
        vec_row INTEGER,      -- row index into vectors.f32
        text TEXT)""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_chunks_file ON chunks(file_id)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_chunks_vecrow ON chunks(vec_row)")
    return con


def load_meta():
    if os.path.exists(META_PATH):
        with open(META_PATH) as f:
            return json.load(f)
    return {"dim": EMBED_DIM, "embed_model": EMBED_MODEL}


def save_meta(meta):
    with open(META_PATH, "w") as f:
        json.dump(meta, f)


def vec_count():
    if not os.path.exists(VEC_PATH):
        return 0
    return os.path.getsize(VEC_PATH) // (EMBED_DIM * 4)


def append_vectors(vecs):
    """Append a list of equal-length float lists to vectors.f32. Returns first row index."""
    start = vec_count()
    with open(VEC_PATH, "ab") as f:
        for v in vecs:
            f.write(struct.pack(f"<{len(v)}f", *v))
    return start


# ---------------------------------------------------------------- single-instance lock
# Why: two concurrent ingest processes both read the same vec_count() offset and append
# to vectors.f32 at once, interleaving rows and corrupting the vec_row<->vector mapping
# (and the SQLite btree). This OS-level byte-range lock guarantees only one ingest runs;
# the lock is released automatically when the process exits, even on a crash.
_LOCK_PATH = os.path.join(INDEX_DIR, "ingest.lock")
_lock_fh = None

def acquire_lock():
    """Return True if this process got the exclusive ingest lock, False if another holds it."""
    global _lock_fh
    import msvcrt
    _lock_fh = open(_LOCK_PATH, "a+")
    try:
        msvcrt.locking(_lock_fh.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        _lock_fh.close()
        _lock_fh = None
        return False
    return True


# ---------------------------------------------------------------- embeddings
def _post(path, payload, timeout=600):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(OLLAMA_URL + path, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def embed(texts):
    """Embed a list of texts via Ollama; returns list of L2-normalized float lists."""
    import numpy as np
    if isinstance(texts, str):
        texts = [texts]
    out = _post("/api/embed", {"model": EMBED_MODEL, "input": texts})
    arr = np.asarray(out["embeddings"], dtype="float32")
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    arr = arr / norms
    return arr.tolist()


# ---------------------------------------------------------------- chunking
def chunk_text(text):
    """Sliding window over chars with overlap; trims whitespace; drops empties."""
    text = text.replace("\r\n", "\n").strip()
    if not text:
        return []
    if len(text) <= CHUNK_SIZE:
        return [text]
    step = CHUNK_SIZE - CHUNK_OVERLAP
    chunks = []
    i = 0
    while i < len(text):
        piece = text[i:i + CHUNK_SIZE].strip()
        if piece:
            chunks.append(piece)
        if i + CHUNK_SIZE >= len(text):
            break
        i += step
    return chunks


# ---------------------------------------------------------------- OCR
_tess_ready = False

def _ensure_tesseract():
    """Point pytesseract at the Tesseract binary if it isn't on PATH (UB-Mannheim default dir)."""
    global _tess_ready
    if _tess_ready:
        return
    import shutil, pytesseract
    if not shutil.which("tesseract"):
        for c in (r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                  r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
                  os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Tesseract-OCR\tesseract.exe")):
            if c and os.path.exists(c):
                pytesseract.pytesseract.tesseract_cmd = c
                break
    _tess_ready = True


def ocr_pdf_page(page):
    """Render a PDF page to an image and OCR it. Returns text ('' on any failure)."""
    try:
        import io, pytesseract
        from PIL import Image
    except Exception:
        return ""
    _ensure_tesseract()
    try:
        pix = page.get_pixmap(dpi=OCR_DPI)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
    except Exception:
        return ""
    for lang in (OCR_LANG, "eng"):     # fall back to English if Malay data isn't installed
        try:
            return pytesseract.image_to_string(img, lang=lang)
        except Exception:
            continue
    return ""


# ---------------------------------------------------------------- Docling
_docling_conv = None

def _get_docling():
    """Build (once per process) a Docling converter: OCR on, table structure on, GPU if available."""
    global _docling_conv
    if _docling_conv is not None:
        return _docling_conv
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    opts = PdfPipelineOptions()
    opts.do_ocr = True
    opts.do_table_structure = True
    try:
        from docling.datamodel.pipeline_options import RapidOcrOptions
        opts.ocr_options = RapidOcrOptions()
    except Exception:
        pass
    try:    # use CUDA automatically when a GPU torch build is present
        from docling.datamodel.pipeline_options import AcceleratorOptions, AcceleratorDevice
        opts.accelerator_options = AcceleratorOptions(device=AcceleratorDevice.AUTO)
    except Exception:
        pass
    _docling_conv = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
    return _docling_conv


def extract_docling(path):
    """Parse a document with Docling -> list of (page_no, text), keeping page numbers so
    query.py can still render the matching page image for OpenClaw."""
    from collections import defaultdict
    res = _get_docling().convert(path)
    doc = res.document
    pages = defaultdict(list)
    for item, _lvl in doc.iterate_items():
        txt = getattr(item, "text", None)
        if not txt or not txt.strip():
            continue
        pno = 0
        prov = getattr(item, "prov", None)
        if prov:
            pno = getattr(prov[0], "page_no", 0)
        pages[pno].append(txt)
    return [(p, "\n".join(t)) for p, t in sorted(pages.items())]


# --- per-file hard timeout -------------------------------------------------------------
# Docling/RapidOCR can hang indefinitely on a pathological scanned PDF (no internal timeout),
# which froze the whole run twice on 2026-06-27. We run each file's Docling parse in a worker
# process with a hard wall-clock cap; on timeout the worker is killed and the caller falls back
# to the fast text-layer path, so one bad file can never stall the pass.
DOCLING_TIMEOUT = 360       # seconds per file
_docling_pool = None

def _docling_worker(path):
    return extract_docling(path)

def extract_docling_safe(path):
    global _docling_pool
    import concurrent.futures
    if _docling_pool is None:
        _docling_pool = concurrent.futures.ProcessPoolExecutor(max_workers=1)
    fut = _docling_pool.submit(_docling_worker, path)
    try:
        return fut.result(timeout=DOCLING_TIMEOUT)
    except concurrent.futures.TimeoutError:
        try:
            for p in list(_docling_pool._processes.values()):
                p.terminate()         # kill the hung worker so it can't keep holding the GPU
        except Exception:
            pass
        try:
            _docling_pool.shutdown(wait=False)
        except Exception:
            pass
        _docling_pool = None          # rebuilt (models reload) on the next file
        raise TimeoutError(f"docling timeout >{DOCLING_TIMEOUT}s")


# ---------------------------------------------------------------- extraction
def extract(path, ext):
    """Return list of (page_no, text). page_no is 1-based for PDFs, else 0."""
    if ext == ".pdf":
        if DOCLING_ENABLED:
            try:
                out = extract_docling(path)
                if out:
                    return out
            except Exception:
                pass            # any Docling error -> fall through to the fast text-layer path
            # NOTE: a hang (no exception) is handled at the run level by the attempt-log in
            # ingest.py (the file is recorded BEFORE processing, so a kill+restart skips it)
            # plus the health-monitor that restarts a stalled pass.
        import fitz
        doc = fitz.open(path)
        out = []
        for i, page in enumerate(doc):
            t = page.get_text("text")
            if t and t.strip():
                out.append((i + 1, t))
            elif OCR_ENABLED:
                # no text layer -> scanned page; OCR it (keeps the page number so query.py
                # can still render this exact page as an image for OpenClaw)
                ocr = ocr_pdf_page(page)
                if ocr and ocr.strip():
                    out.append((i + 1, ocr))
        doc.close()
        return out
    if ext == ".docx":
        import docx
        d = docx.Document(path)
        parts = [p.text for p in d.paragraphs if p.text and p.text.strip()]
        for tbl in d.tables:
            for row in tbl.rows:
                cells = [c.text.strip() for c in row.cells if c.text and c.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))
        return [(0, "\n".join(parts))] if parts else []
    if ext == ".xlsx":
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        out = []
        for si, ws in enumerate(wb.worksheets):
            rows = []
            for row in ws.iter_rows(values_only=True):
                vals = [str(c) for c in row if c is not None]
                if vals:
                    rows.append(" | ".join(vals))
            if rows:
                out.append((si + 1, f"[Sheet: {ws.title}]\n" + "\n".join(rows)))
        wb.close()
        return out
    if ext in (".txt", ".md", ".csv"):
        with open(path, "rb") as f:
            raw = f.read()
        return [(0, raw.decode("utf-8", errors="replace"))]
    return []
