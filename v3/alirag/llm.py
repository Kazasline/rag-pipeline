r"""Local LLM client (spec §11, §33–§34).

Talks to any OpenAI-compatible /v1/chat/completions endpoint — LM Studio,
llama.cpp server, vLLM, SGLang and Ollama all expose one — so the backend can
be swapped by config after the Phase 0/§33 benchmark, without code changes.

Streaming is always used so TTFT is measured for real: ttft = first content
token wall-clock, tokens/sec from the stream tail (§10, §42).

Mode -> reasoning mapping (§34) is backend-dependent; `reasoning_param_style`
in config selects the wire format and MUST be verified against the actual
backend's docs on the target machine (cloud parameter names do not transfer
to local servers automatically).
"""

from __future__ import annotations

import json
import time
import urllib.request


class LLMError(RuntimeError):
    pass


MODE_EFFORT = {"FAST": "low", "DEEP": "medium", "FULLSWING": "high"}


def _mode_params(mode: str, style: str) -> dict:
    """Extra request params implementing FAST=no/min thinking,
    DEEP=medium, FULLSWING=high (§34)."""
    if style == "openai_effort":
        return {"reasoning_effort": MODE_EFFORT[mode]}
    if style == "qwen_enable_thinking":
        return {"chat_template_kwargs": {"enable_thinking": mode != "FAST"}}
    if style == "ollama_think":
        return {"think": mode != "FAST"}
    return {}


class LLMClient:
    def __init__(self, cfg):
        self.cfg = cfg.llm
        self.max_tokens = {"FAST": self.cfg.max_answer_tokens_fast,
                           "DEEP": self.cfg.max_answer_tokens_deep,
                           "FULLSWING": self.cfg.max_answer_tokens_fullswing}

    def chat(self, system: str, user: str, mode: str = "FAST") -> dict:
        """Returns {text, ttft_ms, gen_ms, tokens, tokens_per_s}."""
        body = {
            "model": self.cfg.model_for(mode),
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "max_tokens": self.max_tokens.get(mode, 800),
            "temperature": 0.2,
            "stream": True,
            **_mode_params(mode, self.cfg.reasoning_param_style),
        }
        req = urllib.request.Request(
            self.cfg.base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {self.cfg.api_key}"}
                        if self.cfg.api_key else {})})
        t0 = time.perf_counter()
        ttft = None
        parts: list[str] = []
        ntok = 0
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout_s) as r:
                for raw in r:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        delta = (json.loads(payload)["choices"][0]
                                 .get("delta", {}).get("content"))
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
                    if delta:
                        if ttft is None:
                            ttft = time.perf_counter() - t0
                        parts.append(delta)
                        ntok += 1
        except OSError as e:
            raise LLMError(f"LLM endpoint unreachable at {self.cfg.base_url}: {e}") from e
        total = time.perf_counter() - t0
        gen = total - (ttft or 0)
        return {"text": "".join(parts).strip(),
                "ttft_ms": round((ttft or 0) * 1000, 1),
                "gen_ms": round(gen * 1000, 1),
                "tokens": ntok,
                "tokens_per_s": round(ntok / gen, 1) if gen > 0.05 and ntok else None}

    def health(self) -> bool:
        try:
            req = urllib.request.Request(self.cfg.base_url.rstrip("/") + "/models")
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status == 200
        except OSError:
            return False
