r"""PHASE 1 — READ-ONLY recursive inventory of the source roots (spec §4, §63, §64).

Walks every source root, hashes content, classifies files (knowledge /
legacy / CAD / garbage / unsupported), infers conservative metadata from
paths and filenames (project, discipline, revision — 'UNKNOWN' when evidence
is insufficient, never invented), detects revision families, and tags exact
duplicates.  Writes only to the manifest DB inside the workspace; originals
are opened read-only for hashing and never written.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import Config
from .manifest import Manifest, UNKNOWN
from .safety import SafetyGuard

# Revision tokens on drawings/documents: R0, R00, R01A, Rev.2, Rev A, Rev B.
#
# Alphabetic revisions (Rev A / Rev B / Rev C) are standard on drawings but the
# earlier pattern required a digit, so those files got revision=UNKNOWN, no
# supersede chain and no disclosure when a superseded sheet was cited (§62).
REV_RE = re.compile(
    r"(?:^|[\s_\-\(\[])"
    r"(?:REV\.?\s*([A-Z]?\d{1,2}[A-Z]?|[A-Z])|R(\d{1,2}[A-Z]?))"
    r"(?:$|[\s_\-\)\].])",
    re.IGNORECASE)

# Document-type hints, matched on WORD BOUNDARIES.
#
# F-V3-04 (2026-08-17, first real E:\ run): plain substring matching produced
# 13,651 false "MEMO" hits because the folder "AI MAIN MEMORY" contains "memo",
# and short tokens like "vo"/"bq"/"lai" matched inside unrelated words ("lain"
# is a common Malay word). Metadata must never be invented (§4), so every hint
# is now a \b-anchored regex and abbreviations additionally require a
# non-letter neighbour, e.g. "LAI-003" or "VO 12" but not "lain"/"volume".
def _hint_re(token: str) -> "re.Pattern":
    esc = re.escape(token)
    if len(token) <= 3 and token.isalpha():
        # abbreviation: must stand alone or be followed by a separator+digit
        return re.compile(rf"(?<![a-z]){esc}(?![a-z])", re.IGNORECASE)
    return re.compile(rf"\b{esc}", re.IGNORECASE)


DOCTYPE_HINTS = [(_hint_re(t), v) for t, v in [
    ("tender", "TENDER"), ("bill of quantit", "BQ"), ("bq", "BQ"),
    ("contract", "CONTRACT"), ("agreement", "CONTRACT"),
    ("specification", "SPECIFICATION"), ("spec", "SPECIFICATION"),
    ("drawing", "DRAWING"), ("dwg", "DRAWING"),
    ("ncr", "NCR"), ("lai", "LAI"), ("rfi", "RFI"), ("vo", "VO"),
    ("claim", "CLAIM"), ("invoice", "INVOICE"), ("payment", "PAYMENT"),
    ("submission", "SUBMISSION"), ("submittal", "SUBMISSION"),
    ("memo", "MEMO"), ("minutes", "MINUTES"), ("mom", "MINUTES"),
    ("letter", "CORRESPONDENCE"), ("email", "EMAIL"),
    ("correspondence", "CORRESPONDENCE"),
    ("method statement", "METHOD_STATEMENT"), ("catalogue", "CATALOGUE"),
    ("cpc", "CPC"), ("checklist", "CHECKLIST"), ("report", "REPORT"),
    ("photo", "PHOTO"), ("quotation", "QUOTATION"),
    ("purchase order", "PURCHASE_ORDER"),
]]

DISCIPLINE_HINTS = [(_hint_re(t), v) for t, v in [
    ("landscape", "LANDSCAPE"), ("softscape", "LANDSCAPE"), ("hardscape", "LANDSCAPE"),
    ("irrigation", "IRRIGATION"), ("arbor", "ARBORICULTURE"),
    ("civil", "CIVIL"), ("structural", "STRUCTURAL"), ("architect", "ARCHITECTURE"),
    ("m&e", "MEP"), ("mep", "MEP"), ("electrical", "MEP"), ("plumbing", "MEP"),
    ("survey", "SURVEY"), ("interior", "INTERIOR"),
]]


def classify_ext(ext: str, cfg: Config) -> str:
    ic = cfg.ingest
    if ext in ic.text_exts:
        return "knowledge"
    if ext in ic.image_exts:
        return "image"
    if ext in ic.cad_exts:
        return "cad"
    if ext in ic.legacy_exts:
        return "legacy"
    if ext in ic.garbage_exts:
        return "garbage"
    return "unsupported"


def hash_file(path: str, guard: SafetyGuard, full_max: int) -> tuple[str, str]:
    """Content hash via read-only open. Files above `full_max` bytes get a
    'partial' hash (head 1MB + tail 1MB + size) so a terabyte of archives
    can't stall the inventory; kind is recorded so partial hashes are never
    used for exact-duplicate claims."""
    size = os.path.getsize(path)
    h = hashlib.blake2b(digest_size=20)
    with guard.source_open(path) as f:
        if size <= full_max:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
            return h.hexdigest(), "blake2b"
        h.update(f.read(1 << 20))
        f.seek(max(0, size - (1 << 20)))
        h.update(f.read(1 << 20))
        h.update(str(size).encode())
        return h.hexdigest(), "partial"


def infer_metadata(path: str, source_root: str) -> dict:
    """Conservative inference from the path only (no content reads here).
    First directory level under the source root is recorded as a *candidate*
    project (that is how this corpus is organized); everything without
    evidence stays UNKNOWN."""
    rel = os.path.relpath(path, source_root)
    parts = Path(rel).parts
    meta = {"project": UNKNOWN, "document_type": UNKNOWN,
            "discipline": UNKNOWN, "revision": UNKNOWN,
            "project_source": UNKNOWN}
    if len(parts) > 1 and not parts[0].startswith(("$", ".")):
        # Folder-derived: a strong convention in this corpus, but still an
        # INFERENCE. It is recorded with its source so downstream code (and the
        # reviewer) can tell it apart from a project confirmed from content —
        # "AI MAIN MEMORY" being listed as a project is what this guards
        # against being read as fact (§4).
        meta["project"] = parts[0]
        meta["project_source"] = "folder"
    else:
        meta["project_source"] = UNKNOWN
    # Match document type on the FILENAME first: a folder name higher up the
    # tree describes the project, not this file's type, and letting it decide
    # is how "AI MAIN MEMORY" mislabelled 13k files as MEMO (F-V3-04).
    stem = Path(path).stem
    for rx, dtype in DOCTYPE_HINTS:
        if rx.search(stem):
            meta["document_type"] = dtype
            break
    for rx, disc in DISCIPLINE_HINTS:
        if rx.search(rel):          # discipline may legitimately come from folders
            meta["discipline"] = disc
            break
    m = REV_RE.search(Path(path).stem)
    if m:
        meta["revision"] = ("R" + (m.group(1) or m.group(2))).upper().replace("RR", "R")
    return meta


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _excluded(dirname: str, cfg: Config) -> bool:
    return dirname.lower() in cfg.ingest.exclude_dirs


def reinfer_metadata(cfg: Config, mf: Manifest) -> dict:
    """Re-apply the path/filename inference rules to files ALREADY in the
    manifest, without re-hashing.

    Needed because `scan()` skips unchanged files, so a fix to the inference
    rules (F-V3-04) would otherwise never reach the rows it mislabelled — the
    bad metadata simply persists. Only inferred fields are rewritten; hashes,
    states and derived data are untouched.
    """
    t0 = time.time()
    roots = [str(r) for r in cfg.source_roots]
    changed = 0
    rows = mf.con.execute(
        "SELECT file_id, original_path, project, document_type, discipline, "
        "revision FROM files").fetchall()
    for r in rows:
        path = r["original_path"]
        root = next((x for x in roots if path.lower().startswith(x.lower())), None)
        if root is None:
            continue
        meta = infer_metadata(path, root)
        if any(meta[k] != r[k] for k in
               ("project", "document_type", "discipline", "revision")):
            # project_source MUST move with project. Round-5 reviewer F5-7:
            # omitting it here left the old provenance attached to a new value,
            # so a manually confirmed 'content' attribution survived a re-infer
            # that had just replaced the project with a folder guess — the
            # citation then asserted the guess had been confirmed from document
            # content. §4 violated by the very column added to satisfy §4.
            mf.con.execute(
                "UPDATE files SET project=?, project_source=?, document_type=?, "
                "discipline=?, revision=? WHERE file_id=?",
                (meta["project"], meta["project_source"], meta["document_type"],
                 meta["discipline"], meta["revision"], r["file_id"]))
            changed += 1
    # revision links may shift once revisions are re-read
    mf.con.execute("UPDATE files SET supersedes=NULL, superseded_by=NULL")
    links = link_revision_families(mf)
    mf.commit()
    # The sparse index denormalizes `project` at write time, so correcting the
    # manifest alone leaves the sparse leg filtering on stale labels (§60).
    fts_synced = _sync_sparse_projects(cfg, mf)
    return {"rows_examined": len(rows), "rows_updated": changed,
            "revision_links": links, "sparse_rows_synced": fts_synced,
            "elapsed_s": round(time.time() - t0, 1)}


class SyncError(RuntimeError):
    """The sparse index could not be brought in line with the manifest."""


def _sync_sparse_projects(cfg: Config, mf: Manifest) -> int:
    """Push corrected project labels into the sparse index.

    This RAISES on failure. Round-3 reviewer R3-8: it used to swallow every
    exception and return a count, so an I/O error left the operator with a
    normal-looking result dict (`sparse_rows_synced: 0`), no error and a zero
    exit — while the project-scoped sparse leg was silently dead and the F3(c)
    isolation leak was back. §60 isolation is not something that may fail
    quietly; a partial sync is worse than a refused one, because the operator
    believes the labels are correct.
    """
    from .sparse import SparseIndex
    try:
        idx = SparseIndex(cfg.sparse_db)
    except Exception as e:  # noqa: BLE001
        raise SyncError(
            f"cannot open the sparse index to sync project labels: {e}. "
            "Project-scoped sparse retrieval would silently filter on stale "
            "labels (§60).") from e
    n = 0
    try:
        for r in mf.con.execute(
                "SELECT c.chunk_id, f.project FROM chunks c "
                "JOIN files f ON f.file_id=c.file_id"):
            idx.con.execute("UPDATE fts SET project=? WHERE chunk_id=?",
                            (r[1], r[0]))
            n += 1
        idx.con.commit()
    except Exception as e:  # noqa: BLE001
        raise SyncError(
            f"project label sync failed after {n} row(s): {e}. The sparse "
            "index is now inconsistent with the manifest — re-run "
            "`alirag inventory --reinfer` once the cause is fixed.") from e
    finally:
        idx.close()
    return n


def scan(cfg: Config, guard: SafetyGuard, mf: Manifest,
         max_files: int | None = None, progress_every: int = 2000,
         roots: list[str] | None = None) -> dict:
    """Full read-only inventory pass. Idempotent and incremental: files whose
    (path, size, mtime) are unchanged since the last pass skip re-hashing.

    `roots` narrows the walk to specific folders. That matters in practice:
    walking a large drive alphabetically can spend the whole file budget on
    whatever sorts first (here, AI tooling directories) and never reach the
    actual project documents, so scoping to the folders that hold real work is
    how a pilot indexes something meaningful.
    """
    t0 = time.time()
    counts = {"new": 0, "unchanged": 0, "updated": 0, "errors": 0, "seen": 0}
    known = {r["original_path"]: (r["size"], r["mtime_ns"], r["content_hash"], r["hash_kind"])
             for r in mf.con.execute(
                 "SELECT original_path,size,mtime_ns,content_hash,hash_kind FROM files")}
    for root in (roots if roots else cfg.source_roots):
        root = str(root)
        if not os.path.isdir(root):
            counts.setdefault("missing_roots", []).append(root)
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not _excluded(d, cfg)]
            if guard.in_workspace(dirpath):
                dirnames[:] = []
                continue
            for fn in filenames:
                if fn.startswith("~$"):       # Office lock/temp files
                    continue
                p = os.path.join(dirpath, fn)
                counts["seen"] += 1
                if progress_every and counts["seen"] % progress_every == 0:
                    print(f"[inventory] {counts['seen']} files seen "
                          f"({time.time()-t0:.0f}s)", flush=True)
                try:
                    st = os.stat(p)
                except OSError:
                    counts["errors"] += 1
                    continue
                prior = known.get(p)
                if prior and prior[0] == st.st_size and prior[1] == st.st_mtime_ns:
                    counts["unchanged"] += 1
                    continue
                ext = os.path.splitext(fn)[1].lower()
                stype = classify_ext(ext, cfg)
                try:
                    digest, kind = hash_file(p, guard, cfg.ingest.full_hash_max_bytes)
                except OSError as e:
                    counts["errors"] += 1
                    mf.con.execute(
                        "INSERT INTO ingest_log(ts,file_id,event,detail) VALUES(?,?,?,?)",
                        (time.time(), None, "HASH_ERROR", f"{p}: {e}"[:500]))
                    continue
                meta = infer_metadata(p, root)
                rec = {
                    "content_hash": digest, "hash_kind": kind,
                    "original_path": p, "filename": fn, "extension": ext,
                    "size": st.st_size, "mtime_ns": st.st_mtime_ns,
                    "created_date": _iso(getattr(st, "st_ctime", st.st_mtime)),
                    "modified_date": _iso(st.st_mtime),
                    "source_type": stype, "pipeline_version": _pipeline_version(),
                    **meta,
                }
                fid, disp = mf.upsert_file(rec)
                counts[disp] += 1
                if disp == "new":
                    state = ("CLASSIFIED" if stype in ("knowledge", "image", "cad")
                             else "UNSUPPORTED" if stype == "unsupported"
                             else "SKIPPED")
                    mf.set_state(fid, state,
                                 "" if stype == "knowledge" else f"source_type={stype}")
                if max_files and counts["seen"] >= max_files:
                    break
            mf.commit()
            if max_files and counts["seen"] >= max_files:
                break
    dup = mf.tag_duplicates()
    fams = link_revision_families(mf)
    mf.commit()
    counts.update({"duplicates_tagged": dup, "revision_links": fams,
                   "elapsed_s": round(time.time() - t0, 1)})
    return counts


def _pipeline_version() -> str:
    from . import PIPELINE_VERSION
    return PIPELINE_VERSION


# Directory names that mean "this is where the old copy went", not "this is a
# different subject". F-V3-21: revision families were keyed on the literal
# parent directory, so the near-universal practice of moving the previous sheet
# into a SUPERSEDED\ subfolder broke the chain — R00 and R01 became unrelated
# documents, and a citation of the obsolete sheet carried no §62 disclosure.
ARCHIVE_DIR_RE = re.compile(
    r"^(superse+ded|supercede+d|old|older|archive[sd]?|arkib|previous|prev|"
    r"obsolete|void|voided|not\s*for\s*use|backup|history|lama|"
    r"\d{4}(-\d{2})?)$", re.IGNORECASE)


def _family_dir(path: str) -> str:
    """Directory a revision family belongs to, with archival subfolders folded
    back into their parent.

    'E:/Dawson/Drawings/SUPERSEDED/2024' -> 'e:/dawson/drawings'

    Only ARCHIVE-named components are stripped, so two genuinely different
    folders ('Drawings/Softscape' vs 'Drawings/Hardscape') still keep their
    same-named sheets apart.
    """
    p = Path(path).parent
    while p.name and ARCHIVE_DIR_RE.match(p.name) and p.parent != p:
        p = p.parent
    return str(p).lower().replace("\\", "/")


def _family_key(stem: str) -> str | None:
    """Filename stem with its revision token removed -> revision-family key."""
    m = REV_RE.search(stem)
    if not m:
        return None
    key = (stem[:m.start()] + stem[m.end():]).strip(" -_().").lower()
    return key or None


def _rev_sort_key(rev: str):
    """Order revisions. Numeric revisions sort by number; purely alphabetic
    ones (Rev A < Rev B < Rev C) sort by letter, after any numeric series."""
    m = re.match(r"R?([A-Z]*)(\d+)([A-Z]*)$", rev, re.IGNORECASE)
    if m:
        return (0, int(m.group(2)), (m.group(3) or m.group(1) or "").upper())
    alpha = re.match(r"R?([A-Z]+)$", rev, re.IGNORECASE)
    if alpha:
        return (1, 0, alpha.group(1).upper())
    return (2, 0, rev.upper())


def link_revision_families(mf: Manifest) -> int:
    """Detect R00 -> R01 -> ... chains among files with the same de-revisioned
    stem in the same directory — or in an archival subfolder of it, which is
    where superseded sheets are normally filed — and link
    supersedes/superseded_by (§27). Every revision is kept; nothing is removed."""
    rows = mf.con.execute(
        "SELECT file_id, original_path, filename, revision, project FROM files "
        "WHERE revision != 'UNKNOWN'").fetchall()
    families: dict[tuple, list] = {}
    for r in rows:
        key = _family_key(Path(r["filename"]).stem)
        if key is None:
            continue
        # project is part of the key: two projects may each own a 'Layout Plan
        # R01', and linking those would assert a supersede relation that does
        # not exist (§4 — never invent metadata).
        families.setdefault(
            (_family_dir(r["original_path"]), (r["project"] or "").lower(), key),
            []).append(r)
    links = 0
    for members in families.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda r: _rev_sort_key(r["revision"]))
        for older, newer in zip(members, members[1:]):
            mf.con.execute("UPDATE files SET superseded_by=? WHERE file_id=?",
                           (newer["file_id"], older["file_id"]))
            mf.con.execute("UPDATE files SET supersedes=?, duplicate_tag='REVISION' "
                           "WHERE file_id=?", (older["file_id"], newer["file_id"]))
            links += 1
    return links


def organization_report(mf: Manifest) -> dict:
    """§63 organization report: recognized projects, unknowns, duplicates,
    revision families, unsupported files — a catalog, no file is touched."""
    con = mf.con
    return {
        "projects": {r[0]: r[1] for r in con.execute(
            "SELECT project, COUNT(*) FROM files GROUP BY project ORDER BY 2 DESC")},
        "document_types": {r[0]: r[1] for r in con.execute(
            "SELECT document_type, COUNT(*) FROM files GROUP BY document_type ORDER BY 2 DESC")},
        "source_types": {r[0]: r[1] for r in con.execute(
            "SELECT source_type, COUNT(*) FROM files GROUP BY source_type")},
        "exact_duplicates": con.execute(
            "SELECT COUNT(*) FROM files WHERE duplicate_tag='EXACT_DUPLICATE'").fetchone()[0],
        "revision_families": con.execute(
            "SELECT COUNT(*) FROM files WHERE duplicate_tag='REVISION'").fetchone()[0],
        "unsupported": con.execute(
            "SELECT COUNT(*) FROM files WHERE source_type='unsupported'").fetchone()[0],
        "unknown_project": con.execute(
            "SELECT COUNT(*) FROM files WHERE project='UNKNOWN'").fetchone()[0],
        # What the unsupported/garbage buckets actually contain, so exclusion
        # rules can be reviewed against reality before production indexing (§64).
        "unsupported_extensions": {r[0] or "(none)": r[1] for r in con.execute(
            "SELECT extension, COUNT(*) FROM files WHERE source_type='unsupported' "
            "GROUP BY extension ORDER BY 2 DESC LIMIT 25")},
        "knowledge_extensions": {r[0]: r[1] for r in con.execute(
            "SELECT extension, COUNT(*) FROM files WHERE source_type='knowledge' "
            "GROUP BY extension ORDER BY 2 DESC")},
        "cad_extensions": {r[0]: r[1] for r in con.execute(
            "SELECT extension, COUNT(*) FROM files WHERE source_type='cad' "
            "GROUP BY extension ORDER BY 2 DESC")},
    }
