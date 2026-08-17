r"""Evidence verifier (spec §38–§39, §60–§62).

Deterministic checks before DEEP/FULLSWING answers (and before any answer
claims support):

  * project consistency — evidence mixing multiple projects when the query
    targets one is flagged and filtered (§60 wrong-project control);
  * revision currency — superseded documents are disclosed, and the latest
    revision is preferred for current-status questions (§27, §62);
  * conflict surfacing — differing monetary amounts across documents for the
    same question are reported, not silently averaged (§38);
  * sufficiency — too little or too-weak evidence yields INSUFFICIENT, which
    the answer layer must translate into the §39 honest reply, never a guess.

evidence_status: SUPPORTED | PARTIAL | INSUFFICIENT (§54).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MONEY_RE = re.compile(r"(?:RM|MYR|\$)\s?([\d,]+(?:\.\d{2})?)", re.IGNORECASE)

# Distinct content terms a chunk must share with the query before dense-only
# evidence counts as relevant. 2 rather than 1: one incidental word in common
# is coincidence at corpus scale.
MIN_CONTENT_OVERLAP = 2

# The stopword list and tokenizer are SHARED with the lexical index (terms.py).
# They disagreed before, and the disagreement was the whole defect.
from .sparse import harvest_ids, normalize_id  # noqa: E402
from .terms import STOPWORDS, content_terms  # noqa: E402,F401

_content_terms = content_terms   # retained: referenced by existing tests


def _carries_code(evidence: dict, query: str) -> bool:
    """True when a document code present in the QUERY is also present in the
    EVIDENCE (filename or text), compared on normalized form.

    This is what makes an exact-leg hit self-justifying. Trusting the leg's
    label instead would mean the verifier certifies whatever the retriever
    claims — and the retriever is one of the things it exists to check.
    """
    qcodes = {normalize_id(c) for c in harvest_ids(query, limit=8)}
    if not qcodes:
        return False
    hay = f"{evidence.get('filename', '')} {evidence.get('text', '')}"
    return bool(qcodes & {normalize_id(c) for c in harvest_ids(hay, limit=60)})


@dataclass
class Verdict:
    status: str      # SUPPORTED | PARTIAL | INSUFFICIENT | AMBIGUOUS_PROJECT
    kept: list = field(default_factory=list)
    flags: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)
    # populated for AMBIGUOUS_PROJECT: {project: [evidence, ...]}
    by_project: dict = field(default_factory=dict)


def verify(evidence: list[dict], project_hint: str | None = None,
           min_evidence: int = 1, query: str = "",
           cross_project: bool = False) -> Verdict:
    flags: list[str] = []
    conflicts: list[str] = []
    kept = list(evidence)

    if not kept:
        return Verdict("INSUFFICIENT", [], ["no evidence retrieved"], [])

    # ---- relevance floor (§39)
    #
    # Dense retrieval always returns nearest neighbours, so "we got results" is
    # not evidence of anything. An earlier version accepted any shared 3+ char
    # token, which the reviewer showed meant the word "the" was enough: a
    # question about pump warranties returned chunks about rain trees and was
    # marked SUPPORTED. Stopwords are now removed and a single incidental word
    # is not sufficient — a lexical-leg hit (the term was actually matched in
    # the index) or at least MIN_CONTENT_OVERLAP distinct content terms in one
    # chunk is required.
    #
    # Deliberate trade-off: a cross-language paraphrase sharing no content term
    # and matched only by embedding will be refused rather than answered. §39
    # prefers a refusal to a fabrication, and DEEP is the sanctioned escalation
    # (§16). Retune against the benchmark, not by intuition.
    if query:
        qterms = content_terms(query)
        best_overlap = 0
        if qterms:
            for e in kept:
                overlap = len(qterms & content_terms(e.get("text", "")))
                best_overlap = max(best_overlap, overlap)
        # A query with NO content terms ("the", "apa yang ada dalam ini?") has
        # nothing to match on, so dense neighbours cannot be evidence for it
        # either — that must not count as passing the floor.
        enough_overlap = bool(qterms) and best_overlap >= MIN_CONTENT_OVERLAP

        # An exact-ID hit is the ONE standalone pass, and only when verified
        # here rather than taken on trust: the query must contain a document
        # code and the evidence must actually carry that same normalized code.
        # "Find LAI-003" is legitimately answered by one term.
        #
        # A SPARSE hit grants nothing on its own. It used to: `lexical_hit`
        # meant "the sparse leg returned rows", and since the FTS expression
        # ORs every token, a document matching only `the` satisfied the floor
        # and an off-corpus question came back SUPPORTED. Stopwords are now
        # stripped from the FTS query too, but the verifier must not depend on
        # the retriever's tokenizer being right — it checks the terms itself.
        exact_hit = any("exact" in (e.get("sources") or []) and _carries_code(e, query)
                        for e in kept)

        if not exact_hit and not enough_overlap:
            return Verdict("INSUFFICIENT", kept,
                           [f"no verified exact-code match and only {best_overlap} "
                            f"content term(s) shared with the query (need "
                            f"{MIN_CONTENT_OVERLAP}) — the retrieved items are "
                            "nearest neighbours, not evidence"], [])

    # ---- project isolation (§60)
    projects = {e.get("project") for e in kept if e.get("project")
                and e.get("project") != "UNKNOWN"}
    if project_hint:
        on_target = [e for e in kept
                     if (e.get("project") or "").lower() == project_hint.lower()]
        off = len(kept) - len(on_target)
        if on_target:
            if off:
                flags.append(f"dropped {off} evidence item(s) from other projects "
                             f"(query targets '{project_hint}')")
            kept = on_target
        else:
            flags.append(f"query targets '{project_hint}' but no evidence matches "
                         "that project")
            return Verdict("INSUFFICIENT", [], flags, [])
    # Ambiguity is DECIDED here but REPORTED at the end, so that revision
    # currency and conflict surfacing still run over the evidence — the
    # clarification lists the same ordered, disclosed items the answer would
    # have used.
    ambiguous = len(projects) > 1 and not project_hint and not cross_project
    if len(projects) > 1 and not project_hint:
        if cross_project:
            flags.append("cross-project question: evidence merged from "
                         f"{sorted(projects)} as asked")
        else:
            flags.append(f"evidence spans multiple projects: {sorted(projects)}"
                         " — answering would mix them; asking which one instead")

    # ---- unattributed evidence (§4, §60)
    #
    # A file whose project could not be inferred is not "no objection" — it is
    # a file that MIGHT belong to any project, including one the reader would
    # not accept an answer from. Round-2 reviewer N3: this set was built with
    # `!= "UNKNOWN"`, so unattributed evidence merged silently with a named
    # project, and an all-UNKNOWN evidence set was reported SUPPORTED with no
    # flags at all. On this corpus that is the common case, not the edge case
    # (43,897 of ~45,000 inventoried files are UNKNOWN).
    #
    # Deliberate trade-off, stated so it can be overruled: unattributed
    # evidence is DISCLOSED and caps the status at PARTIAL, rather than
    # triggering the clarification question. Treating it as ambiguity would
    # make almost every query on this corpus unanswerable, which would push
    # the operator to disable the check — a guard nobody can live with is a
    # guard that gets removed. Once project inference covers most of the
    # corpus, revisit and consider promoting this to AMBIGUOUS_PROJECT.
    unattributed = [e for e in kept
                    if not e.get("project") or e.get("project") == "UNKNOWN"]
    if unattributed:
        names = sorted({e.get("filename", "?") for e in unattributed})
        flags.append(
            f"{len(unattributed)} of {len(kept)} evidence item(s) have NO known "
            f"project and cannot be attributed: {', '.join(names[:5])}"
            + (" …" if len(names) > 5 else "")
            + (" — they may belong to a different project than the one asked "
               "about" if projects else
               " — nothing in this answer is attributable to a project"))

    # ---- revision currency (§62)
    superseded = [e for e in kept if e.get("superseded_by")]
    if superseded:
        names = sorted({e["filename"] for e in superseded})
        flags.append("superseded revision(s) in evidence: "
                     + ", ".join(names[:5])
                     + " — latest revision preferred; older kept for disclosure")
        current = [e for e in kept if not e.get("superseded_by")]
        if current:
            kept = current + superseded  # current first, older disclosed after

    # ---- monetary conflict surfacing (§38)
    amounts: dict[str, set[str]] = {}
    for e in kept:
        for m in MONEY_RE.finditer(e.get("text", "")):
            amounts.setdefault(m.group(1), set()).add(e["filename"])
    if len(amounts) > 1:
        listing = "; ".join(f"RM{a} ({', '.join(sorted(fs)[:2])})"
                            for a, fs in sorted(amounts.items())[:6])
        conflicts.append(f"multiple monetary amounts in evidence: {listing}")

    # ---- sufficiency
    if len(kept) < min_evidence:
        return Verdict("INSUFFICIENT", kept, flags + ["insufficient evidence"],
                       conflicts)

    # ---- multi-project ambiguity (§60)
    #
    # Do NOT merge and warn. The previous behaviour built one answer out of
    # documents belonging to different clients and appended a note asking the
    # reader to check it. For "what is the final claim amount?" that yields a
    # single figure with no way to tell whose project it came from — a
    # wrong-project answer dressed as a caveat, and a confidentiality problem
    # besides (§2). The question is genuinely ambiguous, so say so and let the
    # asker choose. An explicit cross-project question still gets the merged
    # treatment it asked for.
    if ambiguous:
        grouped: dict[str, list] = {}
        for e in kept:
            p = e.get("project")
            if p and p != "UNKNOWN":
                grouped.setdefault(p, []).append(e)
        return Verdict("AMBIGUOUS_PROJECT", kept, flags, conflicts, grouped)
    status = "SUPPORTED"
    if (conflicts
            or any("cross-project question" in f for f in flags)
            # unattributed evidence must not read as fully supported (§4)
            or any(e for e in kept
                   if not e.get("project") or e.get("project") == "UNKNOWN")):
        status = "PARTIAL"
    return Verdict(status, kept, flags, conflicts)
