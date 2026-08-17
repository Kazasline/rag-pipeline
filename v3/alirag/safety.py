r"""Data-safety enforcement (spec §1, §84).

The rule: original files are NEVER deleted, moved, renamed, overwritten or
modified by this system.

HOW THIS IS ACTUALLY ESTABLISHED — read this before trusting anything here.

An earlier version claimed the guarantee was structurally enforced because
"every write goes through guarded_write_path()". The independent reviewer
disproved that: the guard was called exactly once in the whole package, by
snapshot() itself, so attribution ("was this path in our write journal?")
compared changes against an empty set and could never fail. Overwriting,
deleting and renaming an original all reported pass:True.

So the guarantee now rests on EVIDENCE, not on a claim about call sites:

  * `snapshot()` records path + size + mtime + CONTENT HASH for every file
    under the source roots. Hashing is what catches an in-place edit that
    restores size and mtime.
  * `verify_snapshot()` re-scans and classifies every difference:
      - modified / deleted / moved (moves reconciled by content hash)
      - each change is then labelled: rag_attributable (present in our write
        journal), allowlisted (matches a path pattern the operator explicitly
        declared volatile, e.g. a live service's own logs), or UNEXPLAINED.
  * `pass` fails on ANY unexplained or RAG-attributable change to a source
    file. Nothing is excused by default. Excusing a change requires the
    operator to declare that pattern in advance, which is an auditable act.

That inversion is the point: the previous design asked "can we prove we did
it?" and therefore passed whenever it had no records. This design asks "can
this change be accounted for?" and fails when it cannot.

`guarded_open()` / `guarded_write_path()` remain the sanctioned write helpers
and refuse source paths outright, but the safety verdict no longer depends on
every writer remembering to use them.

ONE HONEST LIMITATION, so nobody reads more into the report than it says
(round-3 reviewer R3-11): `guarded_write_path()` is currently called from
exactly one place in the package — snapshot() — so `written_paths()` never
contains a real content path and `rag_modified` / `rag_deleted` are in practice
always empty. A genuine breach by this system would therefore be reported under
`unexplained_*`, not under `rag_*`. The VERDICT is unaffected (both fail
`pass`), but the two-way attribution in the report is decorative today rather
than load-bearing, and should not be cited as evidence that this system was
shown not to be the writer.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

# files larger than this are hashed head+tail+size rather than in full, so a
# snapshot over a terabyte of archives stays practical
_FULL_HASH_MAX = 64 * 1024 * 1024


class SourceWriteViolation(RuntimeError):
    """Raised when code attempts to write inside a protected source root."""


def hash_path(path: str | os.PathLike, full_max: int = _FULL_HASH_MAX) -> str:
    """Content digest used for change and move detection."""
    size = os.path.getsize(path)
    h = hashlib.blake2b(digest_size=16)
    with open(path, "rb") as f:
        if size <= full_max:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
            return h.hexdigest()
        h.update(f.read(1 << 20))
        f.seek(max(0, size - (1 << 20)))
        h.update(f.read(1 << 20))
        h.update(str(size).encode())
        return "p" + h.hexdigest()      # 'p' marks a partial digest


class VolatilePatternRejected(ValueError):
    """A declared volatile pattern was broad enough to excuse a real document."""


# Extensions that carry the user's actual work. A volatile declaration that can
# match one of these is not describing a live service — it is describing the
# corpus, and it would let §84 pass while a tender was rewritten.
_DOCUMENT_EXTS = (
    ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".txt", ".md",
    ".csv", ".rtf", ".odt", ".eml", ".msg", ".dwg", ".dxf", ".jpg", ".jpeg",
    ".png", ".tif", ".tiff",
)


class SafetyGuard:
    def __init__(self, source_roots: list[str], workspace: str,
                 excluded_dirs=()):
        self.source_roots = [Path(r).resolve() for r in source_roots if r]
        self.workspace = Path(workspace).resolve()
        # Directories the operator has declared non-knowledge. A volatile
        # declaration may only point inside one of these (see
        # _reject_overbroad_pattern).
        self.excluded_dirs = tuple(excluded_dirs)
        self._audit_path: Path | None = None
        # No per-path allowlist exists any more (see allow_volatile). Kept as
        # an always-empty attribute so older callers reading it see the truth
        # rather than an AttributeError.
        self.volatile_patterns: tuple = ()

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

    def allow_volatile(self, patterns: list[str]) -> None:
        """REMOVED. Use `ingest.exclude_dirs` instead.

        The per-path allowlist was introduced in round 2 so a live service's
        own logs would not fail §84, and the independent reviewer then broke it
        three times: it accepted `*` (N4-6), then `*/Dawson/*` naming the corpus
        (N4-6 again), then its own guard turned out to be masked by a later
        branch (F5-4). Every fix bounded the examples and left the class open.

        It is now redundant as well as dangerous. The walk covers every file,
        and changes inside a directory the operator excluded from indexing are
        classified as `excluded_dir_*` — reported and counted, not failing
        `pass`. That is one declaration, made in config where it is visible,
        doing the job this method did by per-path exception.

        A mechanism whose only remaining purpose is to weaken a guarantee is
        better deleted than bounded.
        """
        raise VolatilePatternRejected(
            "volatile_patterns is removed. Declare the directory in "
            "ingest.exclude_dirs instead: changes inside it are then reported "
            "under excluded_dir_* without failing §84, and the declaration is "
            f"visible in config. Rejected: {list(patterns)[:4]}")

    # ------------------------------------------------------------ enforced API
    def source_open(self, path: str | os.PathLike):
        """Open an original document read-only. The only sanctioned accessor."""
        return open(path, "rb")

    def guarded_open(self, path: str | os.PathLike, mode: str = "w",
                     purpose: str = "", **kwargs):
        """Sanctioned write: validates the destination, journals it, opens it.

        Prefer this over bare open() for anything the system writes, so the
        journal reflects reality — attribution is only as good as its records.
        """
        if "r" in mode and "+" not in mode:
            raise ValueError("guarded_open is for writing; use source_open to read")
        target = self.guarded_write_path(path, purpose)
        kwargs.setdefault("encoding", None if "b" in mode else "utf-8")
        return open(target, mode, **kwargs)

    def guarded_write_path(self, path: str | os.PathLike, purpose: str = "") -> Path:
        """Validate that *path* is a legal write target and journal it.

        Legal = inside the workspace (which may itself sit on a source drive,
        e.g. E:\\ALI_RAG on E:\\ — an explicit, audited carve-out). Anything
        else under a source root is refused.
        """
        p = Path(path)
        if self.in_workspace(p):
            self._audit("write", str(p), purpose)
            return p
        if self.in_source(p):
            # journaled with a distinct action; must NOT count as a write by us
            self._audit("refused", str(p), purpose)
            raise SourceWriteViolation(
                f"refusing to write inside source root: {p} ({purpose or 'no purpose given'})")
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

    def written_paths(self) -> set:
        """Paths this system actually wrote, from the audit journal.

        Only genuine writes count. A REFUSED attempt is journaled too, and an
        earlier version matched it with `action.endswith("write")` — so a
        blocked write was attributed to us and could fail an innocent run.
        Actions are matched exactly.
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
                    if rec.get("action") in ("write", "write_outside"):
                        out.add(rec.get("path", ""))
        except OSError:
            pass
        return out

    # ------------------------------------------------------------ §84 acceptance
    def _is_excluded_dir(self, path: Path) -> bool:
        """Is this directory one the operator declared non-knowledge?"""
        low = {d.lower() for d in self.excluded_dirs}
        return any(part.lower() in low for part in path.parts)

    def _walk_sources(self):
        for root in self.source_roots:
            for dirpath, dirnames, filenames in os.walk(root):
                dp = Path(dirpath)
                if self._under(dp, self.workspace):
                    dirnames[:] = []
                    continue
                # The walk covers EVERY file under the source roots.
                #
                # Round-6 reviewer R6-1: an earlier version skipped
                # `exclude_dirs` here, and that was a regression in the
                # headline guarantee. Excluded directories were not excused —
                # they were never examined — so §84 reported `pass: True`
                # while a source document inside one was modified, deleted and
                # renamed. The shipped exclusion list contains generic names
                # (`models`, `build`, `dist`, `checkpoints`) that plausibly
                # match real document folders, and `_is_excluded_dir` matches
                # ANY path component, so no operator mistake was required.
                #
                # The reasoning in the removed comment was a non-sequitur:
                # exclusion from indexing asserts "not worth indexing", while
                # §1 protects the user's FILES. Those two sets were identical
                # before that change and were not after it.
                #
                # Changes inside excluded directories are now CLASSIFIED
                # (below) rather than omitted: reported, counted, and not
                # failing `pass` — visible instead of absent.
                for fn in filenames:
                    yield dp / fn

    def snapshot(self, out_path: str | os.PathLike, max_files: int | None = None,
                 hash_files: bool = True) -> int:
        """Record path, size, mtime and content hash for every source file.

        The hash is what makes the check able to see an edit that restores
        size and mtime, and what lets a rename be reconciled as a move rather
        than reported as an unexplained deletion.
        """
        out = self.guarded_write_path(out_path, "safety snapshot")
        n = 0
        with open(out, "w", encoding="utf-8") as f:
            for p in self._walk_sources():
                try:
                    st = p.stat()
                except OSError:
                    continue
                rec = {"p": str(p), "s": st.st_size, "m": st.st_mtime_ns}
                if hash_files:
                    try:
                        rec["h"] = hash_path(p)
                    except OSError:
                        rec["h"] = None
                f.write(json.dumps(rec) + "\n")
                n += 1
                if max_files and n >= max_files:
                    return n
        return n

    def verify_snapshot(self, snap_path: str | os.PathLike) -> dict:
        """Re-scan, diff against the snapshot, and account for every change.

        `pass` is the §84 verdict and fails on any change to a source file
        that is either attributable to this system or unexplained. Only
        changes matching an operator-declared volatile pattern are excused,
        and they are listed so the excuse itself can be reviewed.
        """
        before: dict[str, dict] = {}
        with open(snap_path, encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                before[rec["p"]] = rec

        modified: list[str] = []
        seen: set[str] = set()
        now_hashes: dict[str, str] = {}
        for p in self._walk_sources():
            sp = str(p)
            seen.add(sp)
            prior = before.get(sp)
            try:
                st = p.stat()
            except OSError:
                continue
            if prior is None:
                try:
                    now_hashes[sp] = hash_path(p)
                except OSError:
                    pass
                continue
            changed = (st.st_size != prior.get("s")
                       or st.st_mtime_ns != prior.get("m"))
            if prior.get("h"):
                try:
                    changed = hash_path(p) != prior["h"]
                except OSError:
                    pass
            if changed:
                modified.append(sp)

        deleted = [p for p in before if p not in seen]
        added = [p for p in seen if p not in before]

        # reconcile deletions against additions by content hash -> moves (§84
        # claims 0 moved; a rename is otherwise invisible)
        moved = []
        added_by_hash: dict[str, list[str]] = {}
        for a in added:
            h = now_hashes.get(a)
            if h:
                added_by_hash.setdefault(h, []).append(a)
        still_deleted = []
        for d in deleted:
            h = before[d].get("h")
            cands = added_by_hash.get(h) if h else None
            if cands:
                moved.append({"from": d, "to": cands.pop(0)})
            else:
                still_deleted.append(d)

        ours = self.written_paths()

        def classify(paths):
            rag, excluded, unexplained = [], [], []
            for p in paths:
                if p in ours:
                    rag.append(p)
                elif self._is_excluded_dir(Path(p)):
                    excluded.append(p)
                else:
                    unexplained.append(p)
            return rag, excluded, unexplained

        rag_mod, excl_mod, unexplained_mod = classify(modified)
        rag_del, excl_del, unexplained_del = classify(still_deleted)

        # An excluded directory holding DOCUMENTS is a configuration error, not
        # a live service: the exclusion list and the corpus overlap, and the
        # operator is losing both indexing and safety coverage without being
        # told. These fail (round-6 reviewer, condition 2).
        def _documents(paths):
            return [p for p in paths
                    if Path(p).suffix.lower() in _DOCUMENT_EXTS]

        excl_doc_mod = _documents(excl_mod)
        excl_doc_del = _documents(excl_del)

        passed = not (rag_mod or unexplained_mod or rag_del
                      or unexplained_del or moved
                      or excl_doc_mod or excl_doc_del)
        # A pass earned by an allowlist is not a clean run, and the machine-
        # readable verdict must say so rather than leaving it to a detail
        # string nobody parses (round-3 reviewer R3-7).
        excluded_changed = bool(excl_mod or excl_del)
        verdict = ("FAIL" if not passed else
                   "PASS_WITH_EXCLUSIONS" if excluded_changed else
                   "PASS")

        return {
            "files_before": len(before),
            "files_after": len(seen),
            "pass": passed,
            "verdict": verdict,          # PASS | PASS_WITH_EXCUSES | FAIL
            # RAG-attributable — a genuine breach by this system
            "rag_modified": rag_mod,
            "rag_deleted": rag_del,
            # changed by something else and NOT declared — must be reviewed
            "unexplained_modified": unexplained_mod,
            "unexplained_deleted": unexplained_del,
            # excused only because the operator declared the pattern volatile
            # changed inside a directory the operator excluded from indexing:
            # not a breach, but NEVER silently absent from the report (R6-1)
            "excluded_dir_modified": excl_mod,
            "excluded_dir_deleted": excl_del,
            # ...unless they are documents, which means the exclusion list and
            # the corpus overlap — a configuration error that fails
            "excluded_dir_documents_modified": excl_doc_mod,
            "excluded_dir_documents_deleted": excl_doc_del,
            "excluded_dirs": list(self.excluded_dirs),
            "moved": moved,
            # raw diffs
            "modified": modified,
            "deleted": still_deleted,
            "added_count": len(added) - len(moved),
            "hashed": any(r.get("h") for r in before.values()),
            "note": ("EVERY file under the source roots is walked; nothing is "
                     "skipped. 'pass' fails on any change to a source file that this "
                     "system caused or that nothing accounts for. "
                     "Changes inside directories excluded from "
                     "indexing are listed under excluded_dir_* and do not fail "
                     "'pass' — but a DOCUMENT changing inside one does fail, "
                     "because that means the exclusion list overlaps the "
                     "corpus. Renames are reconciled by content hash and "
                     "reported under 'moved'."),
        }
