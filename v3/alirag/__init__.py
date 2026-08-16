"""ALI Agentic Hybrid Multimodal Graph RAG — V3.

Local, evidence-grounded RAG over the user's working drive (default E:\\).
Three reasoning modes (FAST / DEEP / FULLSWING), hybrid retrieval
(exact + sparse + dense + metadata + graph), strict data safety
(originals are never deleted, moved, renamed or modified), and
measurement-first performance engineering.

Hard rules enforced in code, not just documented:
  * every filesystem write goes through safety.guarded_write_path();
  * source roots are opened read-only;
  * benchmark/reviewer reports can only be produced from real runs.
"""

__version__ = "3.0.0"
PIPELINE_VERSION = "v3.0.0"
