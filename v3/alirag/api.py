r"""Local HTTP API (spec §52–§54).

FastAPI app bound to localhost by default; endpoints per §53. The UI layer
talks to these endpoints only — no internal database details leak. When
configured, a bearer token protects every endpoint and sensitive mutations
require authentication. Import is optional: the CLI works without fastapi
installed.

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

import hmac

from .answer import Engine
from .config import Config, is_loopback_host, load_config
from .instrument import percentiles


def create_app(cfg: Config | None = None):
    from fastapi import FastAPI
    from fastapi import Depends, HTTPException
    from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
    from pydantic import BaseModel, Field
    from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

    cfg = cfg or load_config()
    bearer = HTTPBearer(auto_error=False)

    def _check_token(authorization: str | None):
        if not cfg.api_token:
            raise HTTPException(403, "endpoint disabled: set api_token in config to enable")
        expected = f"Bearer {cfg.api_token}".encode()
        if authorization is None or not hmac.compare_digest(
                authorization.encode("utf-8", errors="replace"), expected):
            raise HTTPException(401, "invalid or missing bearer token")

    def require_token(
            credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        authorization = (f"{credentials.scheme} {credentials.credentials}"
                         if credentials is not None else None)
        _check_token(authorization)

    def require_token_if_configured(
            credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if cfg.api_token:
            authorization = (f"{credentials.scheme} {credentials.credentials}"
                             if credentials is not None else None)
            _check_token(authorization)

    app = FastAPI(title="ALI RAG V3", version="3.0.0",
                  docs_url=None if cfg.api_token else "/docs",
                  redoc_url=None if cfg.api_token else "/redoc",
                  openapi_url=None if cfg.api_token else "/openapi.json",
                  dependencies=[Depends(require_token_if_configured)])
    engine = Engine(cfg)

    if cfg.api_token:
        @app.get("/openapi.json", include_in_schema=False)
        def openapi():
            return app.openapi()

        @app.get("/docs", include_in_schema=False)
        def swagger_ui():
            return get_swagger_ui_html(openapi_url="/openapi.json",
                                       title="ALI RAG V3 - Swagger UI")

        @app.get("/redoc", include_in_schema=False)
        def redoc():
            return get_redoc_html(openapi_url="/openapi.json",
                                  title="ALI RAG V3 - ReDoc")

    class QueryIn(BaseModel):
        query: str = Field(min_length=1, max_length=4000)
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

    @app.get("/source/{file_id}", dependencies=[Depends(require_token)])
    def source(file_id: int):
        row = engine.mf.get(file_id)
        if not row:
            return {"error": "unknown file_id"}
        fields = ("file_id", "filename", "original_path", "extension", "project",
                  "project_source", "document_type", "discipline", "revision",
                  "page_count", "index_status", "supersedes", "superseded_by",
                  "duplicate_tag", "duplicate_of")
        return {k: row[k] for k in fields}

    @app.post("/ingest", dependencies=[Depends(require_token)])
    def ingest(limit: int | None = None):
        """Incremental: inventory rescan + ingest changed/new files."""
        from .ingest import Ingestor
        from .inventory import scan
        from .safety import SafetyGuard
        guard = SafetyGuard(cfg.source_roots, cfg.workspace,
                            excluded_dirs=cfg.ingest.exclude_dirs)
        inv = scan(cfg, guard, engine.mf, max_files=None)
        ing = Ingestor(cfg, mf=engine.mf)
        counts = ing.run(limit=limit)
        return {"inventory": inv, "ingest": counts}

    @app.post("/reindex", dependencies=[Depends(require_token)])
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
    if bool(cfg.api_ssl_certfile) != bool(cfg.api_ssl_keyfile):
        raise RuntimeError("api_ssl_certfile and api_ssl_keyfile must be set together")
    if not is_loopback_host(cfg.api_host) and not cfg.api_token:
        raise RuntimeError(
            "api_host is not loopback; refusing to expose the API without api_token "
            "(set api_token in config or ALIRAG_API_TOKEN)")
    uvicorn_kwargs = {}
    if not is_loopback_host(cfg.api_host) and cfg.api_ssl_certfile:
        uvicorn_kwargs["ssl_certfile"] = cfg.api_ssl_certfile
        uvicorn_kwargs["ssl_keyfile"] = cfg.api_ssl_keyfile
    elif not is_loopback_host(cfg.api_host) and cfg.api_token:
        print("Warning: API is exposed without TLS; terminate TLS at a reverse proxy.")
    uvicorn.run(create_app(cfg), host=cfg.api_host, port=cfg.api_port, **uvicorn_kwargs)
