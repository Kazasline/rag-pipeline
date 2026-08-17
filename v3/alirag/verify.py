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

# Questions where an answer from an unattributable document is a §60
# wrong-project answer, not a caveat. Round-3 reviewer, N3 condition 1: a
# PARTIAL on "what locations are shown" is a different risk from a PARTIAL on
# "what is the final claim amount" — nobody reads a footnote as disqualifying
# a figure. For these, UNKNOWN-project evidence alongside a named project
# escalates to the clarification question.
SENSITIVE_INTENT = re.compile(
    r"\b(amount|amounts|sum|total|cost|price|rate|rates|value|quantum|claim|"
    r"claims|payment|invoice|certified|certificate|vo\b|variation|"
    r"date|dated|deadline|due|completion|extension|eot\b|"
    r"status|approved|rejected|outstanding|liability|penalty|lad\b|"
    r"clause|obligation|entitled|entitlement|warranty|defects|retention|"
    r"jumlah|harga|kos|nilai|tuntutan|bayaran|tarikh|tempoh|status)\b",
    re.IGNORECASE)

# Distinct content terms a chunk must share with the query before dense-only
# evidence counts as relevant. 2 rather than 1: one incidental word in common
# is coincidence at corpus scale.
MIN_CONTENT_OVERLAP = 2

# The stopword list and tokenizer are SHARED with the lexical index (terms.py).
# They disagreed before, and the disagreement was the whole defect.
from .sparse import harvest_ids, is_document_code, normalize_id  # noqa: E402
from .terms import (  # noqa: E402,F401
    STOPWORDS, content_terms, df_is_meaningful, discriminative_terms,
)

_content_terms = content_terms   # retained: referenced by existing tests


def _query_doc_codes(query: str) -> set:
    """Normalized codes in the query that could IDENTIFY a document."""
    return {normalize_id(c) for c in harvest_ids(query, limit=8)
            if is_document_code(c)}


def _names_the_document(evidence: dict, qcodes: set) -> bool:
    """True when a document code from the query appears in the evidence's
    FILENAME — i.e. the user named this document.

    Filename-anchored, not body-anchored. Round-3 reviewer R3-2: matching a
    code mentioned anywhere in the body let "what is the pump warranty period
    on drawing L-201?" be answered from a tender spec that merely cross-refers
    to L-201 and says nothing about pumps. A body mention is a REFERENCE to a
    document; only the filename is the document's IDENTITY, and identity is
    what justifies returning a document the user asked for by name.
    """
    if not qcodes:
        return False
    fn = evidence.get("filename", "") or ""
    return bool(qcodes & {normalize_id(c) for c in harvest_ids(fn, limit=20)})


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
           cross_project: bool = False,
           doc_freq: dict | None = None, total_docs: int = 0) -> Verdict:
    """`doc_freq`/`total_docs` carry MEASURED corpus statistics (how many
    indexed chunks contain each query term). With them the relevance floor
    drops terms that are common in this corpus rather than terms someone
    guessed would be common; without them it falls back to a hand-written
    boilerplate list and says so in the flag."""
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
        qcodes = _query_doc_codes(query)
        # Terms that only restate the SCOPE of the question, not its subject.
        scope_terms = content_terms(project_hint or "")

        # The floor FILTERS; it does not merely gate.
        #
        # Round-3 reviewer R3-3: `best_overlap` was a max over all evidence and
        # the exact-code check was an `any(...)`, so ONE qualifying item let the
        # whole set through. Unrelated chunks were then hydrated into `sources`
        # with full §40 provenance and packed into the prompt having met no
        # floor of their own — the citation list said they were evidence.
        passed, dropped, best = [], 0, 0
        for e in kept:
            shared = qterms & content_terms(e.get("text", ""))
            good = discriminative_terms(shared, doc_freq, total_docs,
                                        exclude=scope_terms)
            best = max(best, len(good))
            if _names_the_document(e, qcodes) or len(good) >= MIN_CONTENT_OVERLAP:
                passed.append(e)
            else:
                dropped += 1

        if not passed:
            # A query with NO content terms ("the", "apa yang ada dalam ini?")
            # lands here too: nothing to match on means nothing can be evidence.
            return Verdict("INSUFFICIENT", kept,
                           [f"no evidence met the relevance floor: best item "
                            f"shared {best} discriminating term(s) with the "
                            f"query (need {MIN_CONTENT_OVERLAP}) and none is a "
                            f"document the query named"
                            + ("" if df_is_meaningful(total_docs) else
                               " [term weighting used the fallback boilerplate "
                               "list — the index is too small for document "
                               "frequency to mean anything]")], [])
        if dropped:
            flags.append(f"dropped {dropped} retrieved item(s) that did not meet "
                         "the relevance floor (nearest neighbours, not evidence)")
        kept = passed

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
    # Deliberate trade-off, recorded as DECISIONS.md D-20 and ACCEPTED by the
    # round-3 reviewer with conditions: unattributed evidence is DISCLOSED and
    # caps the status at PARTIAL, rather than triggering the clarification
    # question for every query. Treating it as ambiguity outright would make
    # almost every query on this corpus unanswerable, which would push the
    # operator to disable the check — a guard nobody can live with is a guard
    # that gets removed.
    #
    # Reviewer condition 1: that leniency does NOT extend to the questions
    # where an unattributable source is itself the harm. For a monetary
    # amount, a date, a status or a clause obligation, mixing an unattributed
    # document in with a named project is a §60 wrong-project answer, and no
    # reader treats a footnote as disqualifying a figure. Those escalate.
    unattributed = [e for e in kept
                    if not e.get("project") or e.get("project") == "UNKNOWN"]
    if unattributed:
        names = sorted({e.get("filename", "?") for e in unattributed})
        sensitive = bool(query and SENSITIVE_INTENT.search(query))
        mixed = bool(projects) and not project_hint
        flags.append(
            f"{len(unattributed)} of {len(kept)} evidence item(s) have NO known "
            f"project and cannot be attributed: {', '.join(names[:5])}"
            + (" …" if len(names) > 5 else "")
            + (" — they may belong to a different project than the one asked "
               "about" if projects else
               " — nothing in this answer is attributable to a project"))
        if sensitive and mixed and not cross_project:
            flags.append(
                "the question asks for a figure, date, status or obligation, "
                "so unattributable evidence cannot simply be disclosed — "
                "asking which project instead")
            ambiguous = True

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
            # Unattributed items get their own group rather than being hidden:
            # "which of these do you mean" is not answerable if the option that
            # cannot be attributed is left off the list.
            grouped.setdefault(p if p and p != "UNKNOWN" else "UNKNOWN",
                               []).append(e)
        return Verdict("AMBIGUOUS_PROJECT", kept, flags, conflicts, grouped)
    status = "SUPPORTED"
    if (conflicts
            or any("cross-project question" in f for f in flags)
            # unattributed evidence must not read as fully supported (§4)
            or any(e for e in kept
                   if not e.get("project") or e.get("project") == "UNKNOWN")):
        status = "PARTIAL"
    return Verdict(status, kept, flags, conflicts)
