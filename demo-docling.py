# demo-docling.py — LIVE proof for the camera that Docling is parsing the firm's own PDFs,
# locally, on the GPU. Run: .\.venv\Scripts\python.exe demo-docling.py  [optional\path.pdf]
import sys, time
try:                                            # Windows console is cp1252; force UTF-8 so output never crashes
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
sys.path.insert(0, r"P:\RAG Database\pipeline")
import rag

try:
    import torch
    gpu = torch.cuda.is_available()
    dev = torch.cuda.get_device_name(0) if gpu else "CPU"
except Exception:
    gpu, dev = False, "CPU"
print(f"\n  Docling (IBM) — running locally | GPU: {gpu}  [{dev}]\n  {'='*64}")

# Pick a real, already-Docling-parsed project PDF (small one, so it's quick on camera).
con = rag.connect()
row = con.execute(
    "SELECT path FROM files WHERE ext='.pdf' AND note LIKE 'docling%' "
    "AND size BETWEEN 50000 AND 3000000 ORDER BY size LIMIT 1").fetchone()
pdf = sys.argv[1] if len(sys.argv) > 1 else (row[0] if row else None)
if not pdf:
    print("  No sample PDF found."); sys.exit(0)

print(f"  Parsing: {pdf}\n  (Docling loads its layout + OCR models, then reads the document...)\n")
t = time.time()
pages = rag.extract_docling(pdf)
dt = time.time() - t
print(f"  >> Docling parsed {len(pages)} page(s) in {dt:.1f}s -- structured text (with OCR):\n")
for p, txt in pages[:3]:
    body = "   " + txt.strip().replace("\n", "\n   ")[:380]
    print(f"  -- page {p} " + "-" * 40 + f"\n{body}\n")
print("  This is the exact function (rag.extract_docling) that built the live index.\n")
