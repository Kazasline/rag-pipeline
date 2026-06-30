r"""GPU timing test for the PRODUCTION Docling path (rag.extract_docling, CUDA auto).
Samples a few text PDFs and a few scanned PDFs, times each, reports pages/chars/seconds."""
import os, sys, sqlite3, time
os.environ["RAG_DOCLING"] = "1"
sys.path.insert(0, r"P:\RAG Database\pipeline")
import rag

import torch
print("torch", torch.__version__, "cuda", torch.cuda.is_available(),
      torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU")

con = sqlite3.connect(rag.DB_PATH); con.execute("PRAGMA query_only=1")
text_pdfs = [r[0] for r in con.execute(
    "SELECT path FROM files WHERE ext='.pdf' AND n_chunks>0 LIMIT 3")]
scan_pdfs = [r[0] for r in con.execute(
    "SELECT path FROM files WHERE ext='.pdf' AND n_chunks=0 AND status='ok' LIMIT 3")]
con.close()

samples = [("text", p) for p in text_pdfs] + [("scan", p) for p in scan_pdfs]
print(f"warming up models (first convert loads layout/table/OCR onto GPU)...")
tot_pages = 0
tot_secs = 0.0
for kind, p in samples:
    t0 = time.time()
    try:
        out = rag.extract_docling(p)
        dt = time.time() - t0
        pages = len(out)
        chars = sum(len(t) for _, t in out)
        tot_pages += pages; tot_secs += dt
        print(f"  [{kind}] {pages}p {chars}c {dt:.1f}s ({dt/max(pages,1):.1f}s/pg) | {p.split(chr(92))[-1][:50]}")
    except Exception as e:
        print(f"  [{kind}] ERROR {e} | {p.split(chr(92))[-1][:50]}")
if tot_pages:
    print(f"\nAVG {tot_secs/tot_pages:.2f}s/page over {tot_pages} pages")
print("[gpu test complete]")
