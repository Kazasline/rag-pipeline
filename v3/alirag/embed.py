r"""Embedding clients (spec §35–§36).

Default: bge-m3 (1024-dim, multilingual EN+MS) via local Ollama — the V1
production choice, precomputed at ingestion so query-time embedding is one
small call. Alternatives are selected only by running `alirag bench embed`
against the real corpus, never by leaderboard reputation.

HashEmbedder is a deterministic, dependency-light embedder used by the test
suite and for offline development: NOT semantically meaningful, but it makes
every pipeline stage executable and measurable without a model server.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import urllib.request


class EmbedError(RuntimeError):
    pass


class OllamaEmbedder:
    def __init__(self, base_url: str = "http://127.0.0.1:11434",
                 model: str = "bge-m3", dim: int = 1024, timeout_s: float = 600.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.dim = dim
        self.timeout_s = timeout_s

    def embed(self, texts: list[str]) -> list[list[float]]:
        if isinstance(texts, str):
            texts = [texts]
        data = json.dumps({"model": self.model, "input": texts}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/api/embed", data=data,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                out = json.loads(r.read().decode("utf-8"))
        except OSError as e:
            raise EmbedError(f"embedding endpoint unreachable: {e}") from e
        vecs = out.get("embeddings")
        if not vecs:
            raise EmbedError(f"no embeddings returned: {out}")
        return [_l2(v) for v in vecs]


class HashEmbedder:
    """Deterministic bag-of-hashed-ngrams embedding. Test/offline use only."""

    def __init__(self, dim: int = 256):
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        if isinstance(texts, str):
            texts = [texts]
        out = []
        for t in texts:
            v = [0.0] * self.dim
            for tok in re.findall(r"[a-z0-9]+", t.lower()):
                for gram in (tok, tok[:4]):
                    h = int.from_bytes(hashlib.blake2b(
                        gram.encode(), digest_size=4).digest(), "little")
                    v[h % self.dim] += 1.0
            out.append(_l2(v))
        return out


def _l2(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def make_embedder(cfg) -> object:
    if cfg.embed.provider == "hash":
        return HashEmbedder(dim=cfg.embed.dim)
    return OllamaEmbedder(base_url=cfg.embed.base_url, model=cfg.embed.model,
                          dim=cfg.embed.dim, timeout_s=cfg.embed.timeout_s)
