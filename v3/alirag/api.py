r"""Local HTTP API (spec §52–§54).

FastAPI app bound to localhost by default; endpoints per §53. The UI layer
talks to these endpoints only — no internal database details leak. Import is
optional: the CLI works without fastapi installed.

Run:  alirag serve      (uvicorn, 127.0.0.1:8642)
"""

# NOTE: deliberately NO `from __future__ import annotations` here.
#
# F-V3-22: with deferred annotations every parameter annotation becomes a
# string, and FastAPI resolves them with get_type_hints() against the MODULE
# globals. `QueryIn` is defined inside create_app() (pydantic can only be
# imported lazily — §52 keeps fastapi optional), so the name was unresolvable,
# FastAPI fell back to treating the request body as a query parameter, and
# every POST /query and /explain answered 422 Unprocessable Entity. The whole
# HTTP API was dead and nothing caught it because api.py had no tests.
#
# `X | None` is valid at runtime on the target Python (3.11), so nothing here
# needs the future import.

from .answer import Engine
from .config import Config, load_config
from .instrument import percentiles


def create_app(cfg: Config | None = None):
    from fastapi import FastAPI
    from pydantic import BaseModel

    cfg = cfg or load_config()
    app = FastAPI(title="ALI RAG V3", version="3.0.0")
    engine = Engine(cfg)

    class QueryIn(BaseModel):
        query: str
        mode: str | None = None      # override; normally the router decides
        use_llm: bool = True

    @app.get("/health")
    def health():
        return {"ok": True, "llm_up": engine.llm.health(),
                "dense_backend": type(engine.dense).__name__}

    @app.get("/status")
    def status():
        return {"manifest": engine.mf.stats(),
                "sparse": engine.sparse.stats(),
                "graph": engine.graph.stats(),
                "dense_count": engine.dense.count(),
                "dense_backend": type(engine.dense).__name__,
                "llm_up": engine.llm.health(),
                "workspace": cfg.workspace}

    @app.post("/query")
    def query(q: QueryIn):
        resp = engine.query(q.query, mode_override=q.mode, use_llm=q.use_llm)
        resp.pop("_fingerprint", None)
        return resp

    @app.post("/explain")
    def explain(q: QueryIn):
        """§41: same as /query but returns the full internal trace."""
        resp = engine.query(q.query, mode_override=q.mode, use_llm=q.use_llm,
                            use_cache=False)
        resp.pop("_fingerprint", None)   # internal cache key, never published (§53)
        return resp

    @app.get("/metrics")
    def metrics(mode: str | None = None):
        log = cfg.dir("query_history") / "query_traces.jsonl"
        return percentiles(log, mode=mode)

    @app.get("/source/{file_id}")
    def source(file_id: int):
        row = engine.mf.get(file_id)
        if not row:
            return {"error": "unknown file_id"}
        return {k: row[k] for k in row.keys()}

    @app.post("/ingest")
    def ingest(limit: int | None = None):
        """Incremental: inventory rescan + ingest changed/new files."""
        from .ingest import Ingestor
        from .inventory import scan
        from .safety import SafetyGuard
        guard = SafetyGuard(cfg.source_roots, cfg.workspace)
        inv = scan(cfg, guard, engine.mf, max_files=None)
        ing = Ingestor(cfg, mf=engine.mf)
        counts = ing.run(limit=limit)
        return {"inventory": inv, "ingest": counts}

    @app.post("/reindex")
    def reindex(file_id: int):
        from .ingest import Ingestor
        row = engine.mf.get(file_id)
        if not row:
            return {"error": "unknown file_id"}
        ing = Ingestor(cfg, mf=engine.mf)
        return {"result": ing.ingest_file(row)}

    return app


def serve(cfg: Config | None = None):
    import uvicorn
    cfg = cfg or load_config()
    uvicorn.run(create_app(cfg), host=cfg.api_host, port=cfg.api_port)
