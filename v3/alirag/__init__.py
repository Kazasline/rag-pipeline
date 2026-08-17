"""ALI Agentic Hybrid Multimodal Graph RAG — V3.

Local, evidence-grounded RAG over the user's working drive (default E:\\).
Three reasoning modes (FAST / DEEP / FULLSWING), hybrid retrieval
(exact + sparse + dense + metadata + graph), strict data safety
(originals are never deleted, moved, renamed or modified), and
measurement-first performance engineering.

Data-safety posture (see safety.py for the full account):
  * source roots are opened read-only for reading;
  * `guarded_open()` / `guarded_write_path()` refuse any write inside a source
    root and journal what they do — but they are helpers, NOT a proven
    chokepoint: not every writer in the package routes through them, so the
    §84 verdict does not rest on that assumption;
  * safety is established by EVIDENCE instead — snapshot with content hashes,
    then re-scan and account for every difference, failing on anything
    unexplained (an earlier version asked "can we prove we did it?" and so
    passed whenever it had no records);
  * benchmark/reviewer reports can only be produced from real runs.
"""

__version__ = "3.0.0"
PIPELINE_VERSION = "v3.0.0"
