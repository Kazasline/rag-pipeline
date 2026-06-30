# RAG Pipeline — Local Document Search over WhatsApp

A fully-local Retrieval-Augmented Generation (RAG) system for a Windows PC with an NVIDIA GPU.
Ingests PDFs (and other documents) from a network/cloud drive, builds a vector index with
[bge-m3](https://huggingface.co/BAAI/bge-m3) embeddings, and answers queries via a
[FastMCP](https://github.com/jlowin/fastmcp) tool that sends matching page images directly to
WhatsApp through [OpenClaw](https://openclaw.ai).

## Architecture

```
P:\ drive (PDFs, drawings, plans)
        │
        ▼
  ingest.py  ──── Docling (GPU OCR + layout) ──▶  chunks + page text
        │
        ▼
   rag.py (engine)
   ├── SQLite  → metadata, chunk text, source paths
   └── vectors.f32  → flat float32 memmap  (cosine via NumPy dot-product)
        │
        ├── query.py  → semantic search + PyMuPDF page rendering
        │
        └── rag_mcp.py (FastMCP server)
                └── file_rag tool ──▶ OpenClaw ──▶ WhatsApp image delivery
```

## Requirements

- Windows 10/11 (uses `msvcrt` lock + `SetThreadExecutionState`)
- Python 3.11+ (tested on 3.14)
- NVIDIA GPU with CUDA 12.8 (optional — Docling falls back to CPU)
- [Ollama](https://ollama.com) running locally with `bge-m3` pulled
- [OpenClaw](https://openclaw.ai) for WhatsApp delivery (optional)

## Setup

### 1. Clone and create venv

```bat
git clone https://github.com/YOUR_USER/rag-pipeline.git
cd rag-pipeline
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Install PyTorch with CUDA (skip for CPU-only)

```bat
pip install torch==2.11.0+cu128 torchvision==0.26.0+cu128 ^
    --index-url https://download.pytorch.org/whl/cu128
pip install accelerate==1.14.0
```

### 3. Pull the embedding model

```bat
ollama pull bge-m3
```

### 4. Configure environment

```bat
copy .env.example .env
# edit .env and fill in your API keys
```

### 5. Configure index location

In `rag.py`, set `LOCAL_BASE` to where the index should live (default: `C:\RAGData`):

```python
LOCAL_BASE = r"C:\RAGData"   # must NOT be on a cloud-synced drive
```

### 6. Ingest your documents

```bat
# Index one or more source folders:
set PYTHONUNBUFFERED=1
.venv\Scripts\python.exe ingest.py "D:\MyProjects" "D:\ClientFiles"

# Or with Docling (full GPU-accelerated OCR + layout parsing):
set RAG_DOCLING=1
.venv\Scripts\python.exe ingest.py --redocling
```

### 7. Test search

```bat
.venv\Scripts\python.exe demo-status.py
.venv\Scripts\python.exe demo-search.py "reinforced concrete specification"
```

### 8. Start the MCP server (for OpenClaw / WhatsApp)

```bat
.venv\Scripts\python.exe rag_mcp.py
```

Add to your `openclaw.json` under `mcp.servers`:

```json
"file-rag": {
  "command": "C:\\path\\to\\.venv\\Scripts\\python.exe",
  "args":    ["C:\\path\\to\\rag_mcp.py"],
  "timeout": 90
}
```

## Nightly auto-index

`nightly-index.ps1` is a PowerShell watchdog that runs `ingest.py --refresh` nightly,
detects Docling stalls (log stops growing for 20 min), poison-lists the stalling file,
and relaunches automatically. Register it as a Windows Scheduled Task:

```powershell
schtasks /Create /TN "RAG-Nightly-Index" /TR "powershell -File C:\path\to\nightly-index.ps1" `
         /SC DAILY /ST 02:00 /RU SYSTEM
```

## File reference

| File | Purpose |
|---|---|
| `rag.py` | Core engine — storage paths, embeddings, Docling extraction, lock, keep-awake |
| `ingest.py` | Build/update index (`--refresh`, `--redocling`, `--reocr` modes) |
| `query.py` | Semantic search + PyMuPDF page rendering |
| `rag_mcp.py` | FastMCP server — `file_rag` tool with WhatsApp image delivery |
| `prerender.py` | Pre-render all chunk pages to PNG cache |
| `compact_index.py` | Remove orphaned vectors, rewrite `vectors.f32` contiguously |
| `nightly-index.ps1` | Nightly watchdog with stall detection + auto-restart |
| `demo-status.py` | Show index stats (chunk count, Docling coverage, vector count) |
| `demo-search.py` | CLI semantic search with ranked results display |
| `demo-docling.py` | Live Docling parse demo with GPU timing |

## Index files (never commit)

The index lives at `LOCAL_BASE` (default `C:\RAGData`) and consists of:

- `index/rag.sqlite` — chunk metadata, source paths, parse method tags
- `index/vectors.f32` — flat float32 array, 1024 floats per chunk (bge-m3)
- `index/docling_attempted.txt` — resume log for `--redocling` passes
- `renders/` — cached PNG page images

None of these are in the repo (covered by `.gitignore`).

## License

MIT
