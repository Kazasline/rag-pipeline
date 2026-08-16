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
        "llama-server", "llama-cli", "vllm", "sglang", "qdrant")}
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
    return stack


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
    report["flags"] = {
        "gpu_present": report["gpu"]["detected"],
        "llm_endpoint_up": any(report["ai_stack"].get(k) for k in
                               ("lmstudio_1234", "openai_8000", "openai_8080")),
        "ollama_up": report["ai_stack"]["ollama_models"] is not None,
        "qdrant_up": report["ai_stack"]["qdrant_collections"] is not None,
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
