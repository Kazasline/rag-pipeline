r"""PHASE 0 — READ-ONLY machine inspection (spec §3).

Collects the facts every later configuration decision depends on: OS, CPU,
RAM, GPU/VRAM, CUDA/driver, disks + free space, Python, and which AI runtimes
and models are ACTUALLY installed (Ollama, LM Studio, llama.cpp, vLLM,
SGLang, Docker, WSL2, Qdrant, existing indexes).  Nothing is installed,
started or modified — every probe is a read.

Output: JSON report to 17_REPORTS/phase0_machine.json plus a human summary.
The report is the required input for choosing the Qwen quantization,
inference backend and VRAM budget — decisions are made from this file, not
from assumptions (§88).
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path


def _run(cmd: list[str], timeout: int = 20) -> str | None:
    """Run a read-only probe command; None if unavailable."""
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return out.stdout.strip() if out.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _which(*names: str) -> dict:
    return {n: shutil.which(n) for n in names}


def inspect_gpu() -> dict:
    info: dict = {"detected": False}
    smi = _run(["nvidia-smi", "--query-gpu=name,memory.total,memory.free,driver_version",
                "--format=csv,noheader"])
    if smi:
        parts = [p.strip() for p in smi.splitlines()[0].split(",")]
        info = {"detected": True, "name": parts[0],
                "vram_total": parts[1] if len(parts) > 1 else None,
                "vram_free": parts[2] if len(parts) > 2 else None,
                "driver": parts[3] if len(parts) > 3 else None}
    cuda = _run(["nvcc", "--version"])
    info["nvcc"] = cuda.splitlines()[-1].strip() if cuda else None
    return info


def inspect_disks() -> list[dict]:
    disks = []
    if sys.platform == "win32":
        import string
        for letter in string.ascii_uppercase:
            root = f"{letter}:\\"
            if os.path.exists(root):
                try:
                    u = shutil.disk_usage(root)
                    disks.append({"drive": root, "total_gb": round(u.total / 1e9, 1),
                                  "free_gb": round(u.free / 1e9, 1)})
                except OSError:
                    pass
    else:
        for mount in ("/", os.path.expanduser("~")):
            try:
                u = shutil.disk_usage(mount)
                disks.append({"drive": mount, "total_gb": round(u.total / 1e9, 1),
                              "free_gb": round(u.free / 1e9, 1)})
            except OSError:
                pass
    return disks


def inspect_ram() -> dict:
    try:
        import psutil  # optional
        vm = psutil.virtual_memory()
        return {"total_gb": round(vm.total / 1e9, 1), "available_gb": round(vm.available / 1e9, 1)}
    except Exception:
        pass
    if sys.platform == "linux":
        try:
            with open("/proc/meminfo") as f:
                kb = int(f.readline().split()[1])
            return {"total_gb": round(kb / 1e6, 1)}
        except OSError:
            pass
    if sys.platform == "win32":
        out = _run(["wmic", "computersystem", "get", "TotalPhysicalMemory"])
        if out:
            for ln in out.splitlines():
                if ln.strip().isdigit():
                    return {"total_gb": round(int(ln.strip()) / 1e9, 1)}
    return {}


def _http_get_json(url: str, timeout: float = 3.0):
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def inspect_ai_stack() -> dict:
    """What model-serving infrastructure actually exists and responds."""
    stack: dict = {"binaries": _which(
        "ollama", "docker", "wsl", "git", "node", "python", "lms",
        "llama-server", "llama-cli", "vllm", "sglang", "qdrant",
        "unsloth", "unsloth-studio", "text-generation-webui", "koboldcpp")}
    # Ollama: list installed models via its local API (read-only)
    tags = _http_get_json("http://127.0.0.1:11434/api/tags")
    stack["ollama_models"] = ([m.get("name") for m in tags.get("models", [])]
                              if isinstance(tags, dict) else None)
    # LM Studio / llama.cpp / vLLM style OpenAI endpoints commonly on these ports
    for name, url in (("lmstudio_1234", "http://127.0.0.1:1234/v1/models"),
                      ("openai_8000", "http://127.0.0.1:8000/v1/models"),
                      ("openai_8080", "http://127.0.0.1:8080/v1/models")):
        got = _http_get_json(url)
        stack[name] = ([m.get("id") for m in got.get("data", [])]
                       if isinstance(got, dict) else None)
    # Qdrant
    q = _http_get_json("http://127.0.0.1:6333/collections")
    stack["qdrant_collections"] = (
        [c.get("name") for c in q.get("result", {}).get("collections", [])]
        if isinstance(q, dict) else None)
    # Weights ON DISK — a model pulled with Unsloth / HuggingFace / LM Studio
    # never appears in `ollama list`, so asking the servers alone under-reports
    # what is installed (F-V3-05). §3 explicitly requires inspecting Unsloth.
    stack["python_ai_packages"] = _python_ai_packages()
    stack["local_model_files"] = find_local_models()
    return stack


def _python_ai_packages() -> dict:
    """Training/inference libraries installed in the *system* Python (which may
    differ from this venv) — unsloth in particular is a library, not a server."""
    out = {}
    for mod in ("unsloth", "transformers", "peft", "trl", "bitsandbytes",
                "llama_cpp", "vllm", "sglang", "accelerate"):
        code = f"import {mod},sys;print(getattr({mod},'__version__','?'))"
        for exe in ("python", sys.executable):
            got = _run([exe, "-c", code], timeout=25)
            if got:
                out[mod] = got.strip().splitlines()[-1]
                break
        else:
            out[mod] = None
    return out


# model weight extensions worth reporting, and dirs never worth descending into
_WEIGHT_EXTS = (".gguf", ".safetensors", ".bin", ".pt", ".pth", ".onnx")
_SKIP_DIRS = {"windows", "$recycle.bin", "system volume information",
              "node_modules", ".git", "__pycache__", "appdata\\local\\temp"}


def model_search_roots() -> list[str]:
    """Where model weights realistically live on a Windows workstation."""
    home = os.path.expanduser("~")
    env = os.environ
    cands = [
        env.get("HF_HOME"), env.get("HUGGINGFACE_HUB_CACHE"),
        env.get("TRANSFORMERS_CACHE"), env.get("UNSLOTH_CACHE"),
        os.path.join(home, ".cache", "huggingface", "hub"),
        os.path.join(home, ".cache", "huggingface"),
        os.path.join(home, ".cache", "unsloth"),
        os.path.join(home, "unsloth"), os.path.join(home, "unsloth_compiled_cache"),
        os.path.join(home, ".lmstudio", "models"),
        os.path.join(home, ".cache", "lm-studio", "models"),
        os.path.join(home, ".ollama", "models"),
        os.path.join(home, "models"), os.path.join(home, "Downloads"),
        os.path.join(home, "Documents"),
        r"C:\models", r"C:\AI", r"C:\llm", r"C:\gguf",
        r"D:\models", r"D:\AI", r"D:\llm",
        r"E:\models", r"E:\AI", r"E:\llm", r"E:\SCI_AI_LIBRARY",
    ]
    seen, out = set(), []
    for c in cands:
        if c and os.path.isdir(c) and c.lower() not in seen:
            seen.add(c.lower())
            out.append(c)
    return out


def find_local_models(roots: list[str] | None = None, max_depth: int = 6,
                      limit: int = 400) -> list[dict]:
    """READ-ONLY scan for model weight files. Bounded in depth and count so a
    workstation-wide search stays quick; returns path, size and a guessed
    quantization so Phase 0 can report what is ACTUALLY installed."""
    roots = roots if roots is not None else model_search_roots()
    found: list[dict] = []
    for root in roots:
        base_depth = root.rstrip("\\/").count(os.sep)
        for dirpath, dirnames, filenames in os.walk(root):
            if dirpath.count(os.sep) - base_depth >= max_depth:
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames if d.lower() not in _SKIP_DIRS]
            for fn in filenames:
                if not fn.lower().endswith(_WEIGHT_EXTS):
                    continue
                p = os.path.join(dirpath, fn)
                try:
                    size = os.path.getsize(p)
                except OSError:
                    continue
                if size < 100 * 1024 * 1024:      # skip adapters/small shards
                    continue
                found.append({"path": p, "size_gb": round(size / 1e9, 2),
                              "quant": _guess_quant(fn), "root": root})
                if len(found) >= limit:
                    return sorted(found, key=lambda x: -x["size_gb"])
    return sorted(found, key=lambda x: -x["size_gb"])


def _guess_quant(name: str) -> str | None:
    import re as _re
    m = _re.search(r"(Q\d(?:_[A-Z0-9]+)*|IQ\d[A-Z_]*|F16|BF16|FP8|8bit|4bit)",
                   name, _re.IGNORECASE)
    return m.group(1).upper() if m else None


def inspect_python() -> dict:
    mods = {}
    for m in ("numpy", "torch", "docling", "fitz", "qdrant_client", "fastapi",
              "yaml", "PIL", "openpyxl", "docx", "pptx", "pytesseract"):
        try:
            __import__(m)
            mods[m] = True
        except Exception:
            mods[m] = False
    cuda_available = None
    if mods.get("torch"):
        try:
            import torch
            cuda_available = bool(torch.cuda.is_available())
        except Exception:
            cuda_available = None
    return {"version": sys.version, "modules": mods, "torch_cuda": cuda_available}


def run_phase0() -> dict:
    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "read_only": True,
        "os": {"platform": platform.platform(), "machine": platform.machine(),
               "system": platform.system(), "release": platform.release()},
        "cpu": {"count": os.cpu_count(), "name": platform.processor() or None},
        "ram": inspect_ram(),
        "gpu": inspect_gpu(),
        "disks": inspect_disks(),
        "python": inspect_python(),
        "ai_stack": inspect_ai_stack(),
    }
    # honesty flags the rest of the pipeline reads before claiming anything
    stack = report["ai_stack"]
    weights = stack.get("local_model_files") or []
    report["flags"] = {
        "gpu_present": report["gpu"]["detected"],
        "llm_endpoint_up": any(stack.get(k) for k in
                               ("lmstudio_1234", "openai_8000", "openai_8080")),
        "ollama_up": stack["ollama_models"] is not None,
        "qdrant_up": stack["qdrant_collections"] is not None,
        "unsloth_installed": bool(stack.get("python_ai_packages", {}).get("unsloth")),
        "local_weights_found": len(weights),
        # weights on disk with nothing serving them is a real state: the model
        # exists but the RAG cannot call it until a server is running (§33)
        "weights_without_server": bool(weights) and not any(
            stack.get(k) for k in ("lmstudio_1234", "openai_8000", "openai_8080")),
        "is_target_machine": sys.platform == "win32" and os.path.exists("E:\\"),
    }
    return report


def main(out_path: str | None = None):
    rep = run_phase0()
    text = json.dumps(rep, indent=2, default=str)
    if out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(text, encoding="utf-8")
        print(f"[phase0] report written to {out_path}")
    print(text)
    if not rep["flags"]["is_target_machine"]:
        print("\n[phase0] NOTE: this is NOT the target Windows machine with E:\\ — "
              "configuration decisions must wait for a run on the real hardware.")
    return rep


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
