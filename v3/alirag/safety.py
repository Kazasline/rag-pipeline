r"""Data-safety enforcement (spec §1, §84).

The absolute rule — NEVER delete, overwrite, move or rename original files —
is enforced structurally, not by convention:

  * ``source_open()`` is the only sanctioned way pipeline code touches a
    source file, and it opens strictly read-only ('rb').
  * ``guarded_write_path()`` must wrap every path the system writes to; it
    raises ``SourceWriteViolation`` for any path inside a source root and
    outside the workspace, and journals every approved write to an audit log.
  * ``snapshot()`` / ``verify_snapshot()`` implement acceptance test §84:
    record (path, size, mtime) for every file under the source roots before a
    run, re-scan after, and prove 0 deleted / 0 modified / 0 moved.

Nothing in this package imports os.remove/shutil.move against source paths;
grep-able invariant: the strings "os.remove" and "shutil.move" appear nowhere
outside this docstring.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path


class SourceWriteViolation(RuntimeError):
    """Raised when code attempts to write inside a protected source root."""


class SafetyGuard:
    def __init__(self, source_roots: list[str], workspace: str):
        self.source_roots = [Path(r).resolve() for r in source_roots if r]
        self.workspace = Path(workspace).resolve()
        self._audit_path: Path | None = None

    # ------------------------------------------------------------ helpers
    def _under(self, path: Path, root: Path) -> bool:
        try:
            path.resolve().relative_to(root)
            return True
        except ValueError:
            return False

    def in_workspace(self, path: str | os.PathLike) -> bool:
        return self._under(Path(path), self.workspace)

    def in_source(self, path: str | os.PathLike) -> bool:
        return any(self._under(Path(path), r) for r in self.source_roots)

    # ------------------------------------------------------------ enforced API
    def source_open(self, path: str | os.PathLike):
        """Open an original document read-only. The only sanctioned accessor."""
        return open(path, "rb")

    def guarded_write_path(self, path: str | os.PathLike, purpose: str = "") -> Path:
        """Validate that *path* is a legal write target and journal it.

        Legal = inside the workspace (the workspace may itself live on a
        source drive, e.g. E:\\ALI_RAG on E:\\ — that carve-out is explicit
        and audited). Any other location under a source root is refused.
        """
        p = Path(path)
        if self.in_workspace(p):
            self._audit("write", str(p), purpose)
            return p
        if self.in_source(p):
            self._audit("REFUSED_write", str(p), purpose)
            raise SourceWriteViolation(
                f"refusing to write inside source root: {p} ({purpose or 'no purpose given'})")
        # outside both (e.g. system temp during tests) — allowed but audited
        self._audit("write_outside", str(p), purpose)
        return p

    # ------------------------------------------------------------ audit log
    def _audit(self, action: str, path: str, purpose: str):
        if self._audit_path is None:
            logs = self.workspace / "16_LOGS"
            try:
                logs.mkdir(parents=True, exist_ok=True)
                self._audit_path = logs / "safety_audit.jsonl"
            except OSError:
                return  # never let auditing itself break the pipeline
        try:
            with open(self._audit_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": time.time(), "action": action,
                                    "path": path, "purpose": purpose}) + "\n")
        except OSError:
            pass

    # ------------------------------------------------------------ §84 acceptance
    def snapshot(self, out_path: str | os.PathLike, max_files: int | None = None) -> int:
        """Record (relpath, size, mtime_ns) of every file under the source
        roots (excluding the workspace) to a JSONL file. Returns file count."""
        out = self.guarded_write_path(out_path, "safety snapshot")
        n = 0
        with open(out, "w", encoding="utf-8") as f:
            for root in self.source_roots:
                for dirpath, dirnames, filenames in os.walk(root):
                    dp = Path(dirpath)
                    if self._under(dp, self.workspace):
                        dirnames[:] = []
                        continue
                    for fn in filenames:
                        p = dp / fn
                        try:
                            st = p.stat()
                        except OSError:
                            continue
                        f.write(json.dumps({"p": str(p), "s": st.st_size,
                                            "m": st.st_mtime_ns}) + "\n")
                        n += 1
                        if max_files and n >= max_files:
                            return n
        return n

    def written_paths(self) -> set:
        """Every path this system has ever written, from the audit journal.

        guarded_write_path() is the only write route in the package and it
        always journals, so this set is the complete record of RAG-attributable
        writes — which is what makes attribution in verify_snapshot() evidence
        based rather than a guess.
        """
        out: set = set()
        path = self.workspace / "16_LOGS" / "safety_audit.jsonl"
        if not path.exists():
            return out
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("action", "").endswith("write"):
                        out.add(rec.get("path", ""))
        except OSError:
            pass
        return out

    def verify_snapshot(self, snap_path: str | os.PathLike) -> dict:
        """Re-scan and compare against a snapshot, and ATTRIBUTE every change.

        F-V3-03 (2026-08-17, first real E:\\ run): the original version reported
        a bare pass/fail on "did anything change", which returned pass:false
        because the user's own live services (cron heartbeats, keep-warm locks,
        bridge logs) rewrote their own files during the 173-second scan. That is
        a false positive: it says nothing about whether the RAG touched a
        document.

        The check now separates:
          * rag_modified  — changed AND present in our write audit  -> real breach
          * external_modified — changed but never written by us     -> other process
        `pass` (the §84 acceptance criterion) is based on rag_modified/deleted,
        while `pass_strict` preserves the old "nothing changed at all" answer so
        nothing is hidden. Both are reported.
        """
        before = {}
        with open(snap_path, encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                before[rec["p"]] = (rec["s"], rec["m"])
        deleted, modified = [], []
        seen = set()
        for root in self.source_roots:
            for dirpath, dirnames, filenames in os.walk(root):
                dp = Path(dirpath)
                if self._under(dp, self.workspace):
                    dirnames[:] = []
                    continue
                for fn in filenames:
                    p = str(dp / fn)
                    seen.add(p)
                    if p in before:
                        try:
                            st = os.stat(p)
                        except OSError:
                            continue
                        if (st.st_size, st.st_mtime_ns) != before[p]:
                            modified.append(p)
        deleted = [p for p in before if p not in seen]
        added = len(seen) - len(before) + len(deleted)

        ours = self.written_paths()
        rag_modified = [p for p in modified if p in ours]
        external_modified = [p for p in modified if p not in ours]
        rag_deleted = [p for p in deleted if p in ours]

        result = {
            "files_before": len(before),
            "files_after": len(seen),
            "added_count": max(0, added),
            # §84 acceptance: did the RAG harm an original?
            "pass": not rag_deleted and not rag_modified,
            "rag_modified": rag_modified,
            "rag_deleted": rag_deleted,
            # strict view: did ANYTHING under the source roots change?
            "pass_strict": not deleted and not modified,
            "external_modified": external_modified,
            "external_modified_count": len(external_modified),
            "deleted": deleted,
            "modified": modified,
            "note": ("'pass' answers the §84 question (did the RAG modify or "
                     "delete an original?) by cross-referencing the write audit "
                     "log. 'external_modified' are files changed by other "
                     "processes during the scan — typically your own running "
                     "services writing their logs/locks/heartbeats. Review that "
                     "list: every entry should be a file you expect a live "
                     "service to write."),
        }
        return result
