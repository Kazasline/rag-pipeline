r"""Configuration for ALI RAG V3.

Everything path- or model-specific lives here and is overridable via a YAML
file (config.yaml in the workspace) and/or environment variables — nothing in
the rest of the package hard-codes a drive letter, a model name or a port.

Precedence: environment variable > config.yaml > built-in default.

The defaults target the intended production layout on the user's Windows PC
(sources on E:\, workspace at E:\ALI_RAG) but the whole system runs anywhere,
which is how the test suite exercises it on Linux.
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:  # PyYAML is in requirements; degrade gracefully to JSON-only config
    import yaml
except Exception:  # pragma: no cover
    yaml = None

# Numbered workspace sub-folders (spec §5). Key -> folder name.
WORKSPACE_LAYOUT = {
    "system": "00_SYSTEM",
    "config": "01_CONFIG",
    "manifest": "02_MANIFEST",
    "metadata": "03_METADATA",
    "extracted": "04_EXTRACTED",
    "page_images": "05_PAGE_IMAGES",
    "tables": "06_TABLES",
    "drawing_derivatives": "07_DRAWING_DERIVATIVES",
    "vector_index": "08_VECTOR_INDEX",
    "sparse_index": "09_SPARSE_INDEX",
    "visual_index": "10_VISUAL_INDEX",
    "graph": "11_GRAPH",
    "cache": "12_CACHE",
    "query_history": "13_QUERY_HISTORY",
    "evaluation": "14_EVALUATION",
    "benchmark": "15_BENCHMARK",
    "logs": "16_LOGS",
    "reports": "17_REPORTS",
    "backup_config": "18_BACKUP_CONFIG",
    "temp": "19_TEMP",
    "skills": "20_SKILLS",
}


def _default_workspace() -> str:
    if os.environ.get("ALIRAG_WORKSPACE"):
        return os.environ["ALIRAG_WORKSPACE"]
    if sys.platform == "win32":
        return r"E:\ALI_RAG"
    return os.path.expanduser("~/ALI_RAG")


def _default_sources() -> list[str]:
    env = os.environ.get("ALIRAG_SOURCES")
    if env:
        return [p for p in env.split(os.pathsep) if p]
    if sys.platform == "win32":
        return ["E:\\"]
    return []  # must be configured explicitly off-Windows


@dataclass
class LLMConfig:
    """Primary answer model. Endpoint is OpenAI-compatible (/v1/chat/completions),
    which LM Studio, llama.cpp server, vLLM, SGLang and Ollama all expose.

    `model` is a *placeholder until Phase 0 inspection*: the actually-installed
    Qwen model, quantization and runtime must be read from the machine
    (inspect_machine.py reports them) before this is finalized.  Reasoning-mode
    mapping differs per backend (§34) — `reasoning_param_style` selects how
    FAST/DEEP/FULLSWING map onto request parameters and must be verified
    against the chosen backend's docs.
    """
    base_url: str = "http://127.0.0.1:1234/v1"
    model: str = "UNVERIFIED-set-after-phase0-inspection"
    api_key: str = ""                    # local servers usually ignore this; never commit real keys
    timeout_s: float = 300.0
    # Budgets must cover chain-of-thought AND the answer. Measured on Qwen3.5:
    # a 400-token FAST budget was consumed entirely by reasoning, so the model
    # streamed 22s and produced an empty answer (F-V3-09). If your backend can
    # genuinely disable thinking, these can come back down.
    max_answer_tokens_fast: int = 1024
    max_answer_tokens_deep: int = 3000
    max_answer_tokens_fullswing: int = 8000
    # one of: "none" (no reasoning knob), "openai_effort" (reasoning_effort),
    # "qwen_enable_thinking" (chat_template_kwargs.enable_thinking),
    # "ollama_think" (think: bool)
    reasoning_param_style: str = "none"
    # Optional per-mode model override. Justified by measured hardware, not
    # preference: on a 16GB card a 30B+ model cannot stay resident alongside
    # anything else, so FAST may need a smaller resident model while DEEP /
    # FULLSWING accept slower CPU-offloaded generation for better reasoning
    # (§34/§35/§88). Empty = use `model` for every mode. Note that alternating
    # between two models makes the server swap weights, which costs seconds —
    # only split after benchmarking both ways.
    model_fast: str = ""
    model_deep: str = ""
    model_fullswing: str = ""
    # Vision model for page-image reasoning (§21); empty = vision disabled.
    model_vision: str = ""

    def model_for(self, mode: str) -> str:
        return {"FAST": self.model_fast, "DEEP": self.model_deep,
                "FULLSWING": self.model_fullswing}.get(mode) or self.model


@dataclass
class EmbedConfig:
    """Query/document embedding. Default reuses the proven V1 stack
    (bge-m3 @ 1024 dims via local Ollama) — multilingual EN+MS, already
    validated on this corpus. Candidate alternatives are benchmarked with
    `alirag bench embed`, not swapped on reputation (§36/§97)."""
    provider: str = "ollama"             # ollama | hash (deterministic, tests only)
    base_url: str = "http://127.0.0.1:11434"
    model: str = "bge-m3"
    dim: int = 1024
    timeout_s: float = 600.0


@dataclass
class DenseConfig:
    backend: str = "auto"                # auto | qdrant | memmap
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_collection: str = "alirag_chunks"


@dataclass
class RetrievalPolicy:
    """Candidate-count policy per mode. Spec §9/§13 starting values — these are
    explicitly benchmark targets, not sacred numbers."""
    sparse_k: int = 20
    dense_k: int = 20
    fused_k: int = 10
    evidence_k: int = 4
    rerank: bool = False
    graph_hops: int = 0
    verify: bool = False


DEFAULT_POLICIES = {
    "FAST": RetrievalPolicy(sparse_k=20, dense_k=20, fused_k=10, evidence_k=4,
                            rerank=False, graph_hops=0, verify=False),
    "DEEP": RetrievalPolicy(sparse_k=60, dense_k=60, fused_k=30, evidence_k=8,
                            rerank=True, graph_hops=1, verify=True),
    "FULLSWING": RetrievalPolicy(sparse_k=120, dense_k=120, fused_k=40, evidence_k=12,
                                 rerank=True, graph_hops=2, verify=True),
}


@dataclass
class IngestConfig:
    # extensions ingested as knowledge (content parsed + indexed)
    text_exts: tuple = (".pdf", ".docx", ".xlsx", ".pptx", ".txt", ".md",
                        ".csv", ".json", ".eml")
    image_exts: tuple = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp")
    # CAD sources are never parsed directly; only derivatives (PDF/DXF exports)
    cad_exts: tuple = (".dwg", ".dxf")
    # legacy formats: inventoried + tagged, not parsed in v3.0
    legacy_exts: tuple = (".doc", ".xls", ".ppt")
    # never index as knowledge (§64/§65): binaries, models, archives, caches
    garbage_exts: tuple = (".exe", ".dll", ".msi", ".sys", ".gguf", ".safetensors",
                           ".bin", ".pt", ".pth", ".onnx", ".iso", ".zip", ".rar",
                           ".7z", ".tar", ".gz", ".bak", ".tmp", ".lnk", ".db")
    exclude_dirs: tuple = (
        "$recycle.bin", "system volume information", "node_modules", ".git",
        "__pycache__", ".venv", "venv", "appdata", "windows", "program files",
        "program files (x86)", "programdata", ".cache", "site-packages",
        "ali_rag",  # never index our own workspace
    )
    hash_algo: str = "blake2b"
    # files larger than this get a partial hash (head+tail+size) to keep the
    # inventory pass fast; full hash on demand
    full_hash_max_bytes: int = 256 * 1024 * 1024
    chunk_chars: int = 1800              # V1-proven values
    chunk_overlap: int = 360
    ocr_lang: str = "eng+msa"
    page_image_max_px: int = 1600        # V1 lesson: cap renders for A0/A1 sheets


@dataclass
class Config:
    workspace: str = field(default_factory=_default_workspace)
    source_roots: list = field(default_factory=_default_sources)
    llm: LLMConfig = field(default_factory=LLMConfig)
    embed: EmbedConfig = field(default_factory=EmbedConfig)
    dense: DenseConfig = field(default_factory=DenseConfig)
    ingest: IngestConfig = field(default_factory=IngestConfig)
    policies: dict = field(default_factory=lambda: {k: dataclasses.replace(v)
                                                    for k, v in DEFAULT_POLICIES.items()})
    api_host: str = "127.0.0.1"          # localhost-only by default (§52)
    api_port: int = 8642

    # ---------------------------------------------------------------- paths
    def dir(self, key: str) -> Path:
        """Absolute path of a workspace sub-folder (created on demand)."""
        p = Path(self.workspace) / WORKSPACE_LAYOUT[key]
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def manifest_db(self) -> Path:
        return self.dir("manifest") / "manifest.sqlite"

    @property
    def sparse_db(self) -> Path:
        return self.dir("sparse_index") / "sparse.sqlite"

    @property
    def graph_db(self) -> Path:
        return self.dir("graph") / "graph.sqlite"

    @property
    def dense_dir(self) -> Path:
        return self.dir("vector_index")

    # ---------------------------------------------------------------- io
    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        return d

    def save(self, path: Path | None = None) -> Path:
        path = path or (self.dir("config") / "config.yaml")
        d = self.to_dict()
        d.pop("policies", None)  # policies saved separately once tuned
        text = yaml.safe_dump(d, sort_keys=False) if yaml else json.dumps(d, indent=2)
        Path(path).write_text(text, encoding="utf-8")
        return Path(path)


def load_config(path: str | os.PathLike | None = None) -> Config:
    """Load config.yaml if present; env vars override file values."""
    cfg = Config()
    candidates = []
    if path:
        candidates.append(Path(path))
    candidates.append(Path(cfg.workspace) / WORKSPACE_LAYOUT["config"] / "config.yaml")
    for cand in candidates:
        if cand.is_file() and yaml:
            data = yaml.safe_load(cand.read_text(encoding="utf-8")) or {}
            for key in ("workspace", "source_roots", "api_host", "api_port"):
                if key in data:
                    setattr(cfg, key, data[key])
            for section, cls in (("llm", LLMConfig), ("embed", EmbedConfig),
                                 ("dense", DenseConfig), ("ingest", IngestConfig)):
                if isinstance(data.get(section), dict):
                    cur = getattr(cfg, section)
                    for k, v in data[section].items():
                        if hasattr(cur, k):
                            setattr(cur, k, tuple(v) if isinstance(getattr(cur, k), tuple) else v)
            break
    # env overrides (highest precedence)
    if os.environ.get("ALIRAG_WORKSPACE"):
        cfg.workspace = os.environ["ALIRAG_WORKSPACE"]
    if os.environ.get("ALIRAG_SOURCES"):
        cfg.source_roots = [p for p in os.environ["ALIRAG_SOURCES"].split(os.pathsep) if p]
    if os.environ.get("ALIRAG_LLM_URL"):
        cfg.llm.base_url = os.environ["ALIRAG_LLM_URL"]
    if os.environ.get("ALIRAG_LLM_MODEL"):
        cfg.llm.model = os.environ["ALIRAG_LLM_MODEL"]
    if os.environ.get("ALIRAG_EMBED_PROVIDER"):
        cfg.embed.provider = os.environ["ALIRAG_EMBED_PROVIDER"]
    return cfg
