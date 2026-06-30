r"""Quick test: Docling extraction on a few scanned PDFs from the index.
Verifies (a) OCR text quality and (b) that we can keep page numbers (for image render)."""
import sys, sqlite3, time
from collections import defaultdict
sys.path.insert(0, r"P:\RAG Database\pipeline")
import rag

from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
try:
    from docling.datamodel.pipeline_options import RapidOcrOptions
    ocr_opts = RapidOcrOptions()
except Exception as e:
    ocr_opts = None
    print("RapidOcrOptions unavailable, using default OCR:", e)

# sample scanned PDFs (status ok, no text extracted in main pass)
con = sqlite3.connect(rag.DB_PATH); con.execute("PRAGMA query_only=1")
samples = [r[0] for r in con.execute(
    "SELECT path FROM files WHERE status='ok' AND n_chunks=0 AND ext='.pdf' LIMIT 3")]
con.close()
print("samples:", len(samples))

opts = PdfPipelineOptions()
opts.do_ocr = True
opts.do_table_structure = True
if ocr_opts is not None:
    opts.ocr_options = ocr_opts
conv = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})


def extract_docling(path):
    res = conv.convert(path)
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


for p in samples:
    t0 = time.time()
    try:
        out = extract_docling(p)
        nchars = sum(len(t) for _, t in out)
        print(f"\n=== {p.split(chr(92))[-1]} | {len(out)} page(s), {nchars} chars, {time.time()-t0:.1f}s ===")
        for pno, txt in out[:2]:
            print(f"  [page {pno}] {txt[:280].strip()!r}")
    except Exception:
        import traceback
        traceback.print_exc()
print("\n[test complete]")
