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
# Dates in the forms these documents actually use.
DATE_RE = re.compile(
    r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
    r"|\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+\d{2,4}"
    r"|\d{4}-\d{2}-\d{2})\b", re.IGNORECASE)


# A number carrying a UNIT is an answer; a bare number is usually furniture.
# "24 months", "150mm", "12 nos", "5%" qualify. "SHEET 3 OF 40" and
# "SCALE 1:200" — the contents of a drawing title block — do not, which is the
# distinction R6-2 turns on.
QUANTITY_RE = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(?:months?|weeks?|days?|years?|hours?|"
    r"mm|cm|m2|m³|m3|sqm|sq\.?m|km|kg|tonnes?|nos?\b|units?|pcs?|%|"
    r"bulan|minggu|hari|tahun|unit|biji)\b", re.IGNORECASE)


def _sensitive_evidence(items: list) -> bool:
    """Does the EVIDENCE itself carry the kind of fact that must be attributable?

    Round-5 reviewer F5-5: escalation keyed only on query vocabulary, so the
    same unattributable money chunk escalated for "what is the final claim
    amount" and merely disclosed for "how much was billed" — and the reviewer
    listed nine more phrasings that missed. The regex was fixed twice against
    the examples quoted at it while the class stayed open.

    The verifier already KNOWS the evidence is monetary — MONEY_RE fires on it
    to build the conflict list. Keying on that is a property of the material,
    which no rephrasing can evade. The query regex is kept as an ADDITIONAL
    trigger for questions whose evidence has no number in it (a status, an
    obligation), not as the primary one.
    """
    for e in items:
        text = e.get("text", "") or ""
        if (MONEY_RE.search(text) or DATE_RE.search(text)
                or QUANTITY_RE.search(text)):
            return True
    return False


SENSITIVE_INTENT = re.compile(
    r"\b(amount|amounts|sum|total|cost|price|rate|rates|value|quantum|claim|"
    r"claims|payment|invoice|certified|certificate|vo\b|variation|"
    r"date|dated|deadline|due|completion|extension|eot\b|"
    r"status|approved|rejected|outstanding|liability|penalty|lad\b|"
    r"clause|obligation|entitled|entitlement|warranty|defects|retention|"
    r"charge|charged|pay|paid|payable|quoted|quote|figure|owed|owing|balance|"
    r"days|delay|delayed|late|when|deadline|finish|finished|agreed|"
    r"jumlah|harga|kos|nilai|tuntutan|bayaran|tarikh|tempoh|status|"
    r"berapa|bayar|caj|hutang|lewat|bila)\b",
    re.IGNORECASE)

# Questions whose answer must be a NUMBER OR A DATE. Narrower than
# SENSITIVE_INTENT on purpose: "what is the approval status?" is sensitive —
# an unattributable source is still a §60 problem for it — but its answer is a
# word ("rejected"), so demanding a figure in the evidence would refuse a
# correct answer. Only these require the answer's shape to be present.
QUANTITATIVE_INTENT = re.compile(
    r"\b(amount|amounts|sum|total|cost|price|rate|rates|value|quantum|"
    r"payment|invoice|claim|claims|fee|fees|charge|charged|billed|paid|"
    r"payable|owed|owing|balance|shortfall|unpaid|quantity|quantities|"
    r"how much|how many|how long|when|date|dated|deadline|due|duration|"
    r"period|completion|handover|extension|eot\b|days|weeks|months|"
    r"berapa|jumlah|harga|kos|nilai|bayaran|tarikh|tempoh|bila)\b",
    re.IGNORECASE)

# Distinct content terms a chunk must share with the query before dense-only
# evidence counts as relevant. 2 rather than 1: one incidental word in common
# is coincidence at corpus scale.
MIN_CONTENT_OVERLAP = 2

# Prefix of the flag raised when a document was admitted because the user named
# it, not because its content matched. Such an answer can never be SUPPORTED.
NAME_ONLY_FLAG = "returned because you named "

# The stopword list and tokenizer are SHARED with the lexical index (terms.py).
# They disagreed before, and the disagreement was the whole defect.
from .sparse import (  # noqa: E402
    code_variants, harvest_ids, is_document_code, normalize_id,
)
from .terms import (  # noqa: E402,F401
    STOPWORDS, content_terms, df_is_meaningful, discriminative_terms,
)

_content_terms = content_terms   # retained: referenced by existing tests


def _query_doc_codes(query: str) -> set:
    """Normalized codes in the query that could IDENTIFY a document.

    Expanded through `code_variants` as well (round-6 reviewer R6-3b): F5-2
    fixed only the index side, so pasting a full sheet number from an email —
    "…on DWG-L-201-R03?" — failed to match the short `L-201.pdf` on disk, while
    the reverse direction worked. The §9 exact path has to work whichever form
    the user happens to have.
    """
    out = {normalize_id(c) for c in harvest_ids(query, limit=8)
           if is_document_code(c)}
    for c in harvest_ids(query, limit=8):
        if is_document_code(c):
            out |= code_variants(c, limit=8)
    return out


def _names_the_document(evidence: dict, qcodes: set) -> bool:
    """True when a document code from the query appears in the evidence's
    FILENAME — i.e. the user named this document.

    Filename-anchored, not body-anchored. Round-3 reviewer R3-2: matching a
    code mentioned anywhere in the body let "what is the pump warranty period
    on drawing L-201?" be answered from a tender spec that merely cross-refers
    to L-201 and says nothing about pumps. A body mention is a REFERENCE to a
    document; only the filename is the document's IDENTITY, and identity is
    what justifies returning a document the user asked for by name.

    Matching is on code COMPONENTS (see `code_variants`), so `L-201-RevB.pdf`
    and `DWG-L-201-R03.pdf` are recognised as L-201.
    """
    if not qcodes:
        return False
    return bool(qcodes & code_variants(evidence.get("filename", "") or "", limit=20))


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
           doc_freq: dict | None = None, total_docs: int = 0,
           known_projects: list | None = None) -> Verdict:
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
        # Strip the project label as a PHRASE, not term by term.
        #
        # Round-4 reviewer N4-5: subtracting every word of the project name
        # destroyed real evidence whenever the name was descriptive — for
        # project "Pump Station Upgrade", the question "what is the pump
        # warranty period" lost "pump" and a chunk literally containing the
        # answer was refused. Real project names in this domain are noun
        # phrases, so this fired often. Removing the phrase occurrence leaves
        # any term that also stands on its own elsewhere in the question.
        scoped_query = query
        if project_hint:
            scoped_query = re.sub(re.escape(project_hint), " ", query,
                                  flags=re.IGNORECASE)
        qterms = content_terms(scoped_query)
        qcodes = _query_doc_codes(query)

        # Terms belonging to ANY project name, not just the hinted one.
        #
        # Round-5 reviewer F5-1: two independently conservative mechanisms
        # composed into a hole. `_project_hint` only fires on a verbatim match,
        # and the phrase strip only ran when a hint was set — so reordering the
        # project name ("...for Meridian Towers Dawson?") left the hint None,
        # nothing stripped, and the project name itself counted as
        # discriminating evidence. A drawing title block reading only "DAWSON
        # MERIDIAN TOWERS / Sheet 12 of 40" came back SUPPORTED for "what is
        # the final claim amount". Whether the floor held depended on the word
        # order of the question.
        #
        # Project-name terms are not subtracted outright — that was N4-5, and
        # it deleted real evidence when the name was descriptive. They simply
        # cannot be the ONLY thing an item is admitted on.
        project_terms = set()
        for name in (known_projects or []):
            project_terms |= content_terms(name)
        if project_hint:
            project_terms |= content_terms(project_hint)

        # The floor FILTERS; it does not merely gate.
        #
        # Round-3 reviewer R3-3: `best_overlap` was a max over all evidence and
        # the exact-code check was an `any(...)`, so ONE qualifying item let the
        # whole set through. Unrelated chunks were then hydrated into `sources`
        # with full §40 provenance and packed into the prompt having met no
        # floor of their own — the citation list said they were evidence.
        passed, dropped, best = [], 0, 0
        named_only = []          # admitted by filename, not by content
        for e in kept:
            shared = qterms & content_terms(e.get("text", ""))
            good = discriminative_terms(shared, doc_freq, total_docs)
            best = max(best, len(good))
            on_topic = len(good) >= MIN_CONTENT_OVERLAP
            # ...and at least one of those terms must be about the SUBJECT,
            # not about which project we are in (§60 handles scope separately).
            if not good - project_terms:
                on_topic = False
            if on_topic:
                passed.append(e)
            elif _names_the_document(e, qcodes):
                # Round-4 reviewer N4-4: a filename match used to bypass the
                # content floor for EVERY chunk of that file, unflagged — a
                # scaffolding invoice inside "L-201.pdf" was returned SUPPORTED
                # as evidence for a pump warranty. Naming a document justifies
                # RETURNING it; it does not certify an arbitrary chunk of it as
                # an answer. Kept, but marked and capped below.
                passed.append(e)
                named_only.append(e)
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
        if named_only:
            names = sorted({e.get("filename", "?") for e in named_only})
            flags.append(
                NAME_ONLY_FLAG + ", ".join(names[:3])
                + ", not because its content answers the question — "
                "no passage in it met the relevance floor")
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
    # D-20 (lenient disclosure) is WITHDRAWN — see DECISIONS.md.
    #
    # It rested on "43,897 of ~45,000 files carry project=UNKNOWN". The
    # round-4 reviewer showed that figure is the DOCUMENT_TYPE unknown count;
    # the project columns sum to the same 45,645 total, so essentially every
    # file does carry a project label and the true unattributed share is ~0%.
    # The argument for leniency — that the strict rule would make the system
    # unusable — was therefore false, and the round-2 reviewer's original
    # instruction stands: unattributed evidence must not be merged with a
    # named project. It costs almost nothing here.
    #
    # For SENSITIVE questions (amounts, dates, statuses, obligations) the
    # escalation applies even when NO project is known, because "this figure
    # comes from a document we cannot attribute" is the whole problem.
    unattributed = [e for e in kept
                    if not e.get("project") or e.get("project") == "UNKNOWN"]
    if unattributed:
        names = sorted({e.get("filename", "?") for e in unattributed})
        sensitive = _sensitive_evidence(kept) or bool(
            query and SENSITIVE_INTENT.search(query))
        # NOT gated on a known project also being present (round-4 N4-7): an
        # all-UNKNOWN evidence set answering "what is the final claim amount?"
        # was the case that never escalated, and it is the worst one.
        mixed = not project_hint
        flags.append(
            f"{len(unattributed)} of {len(kept)} evidence item(s) have NO known "
            f"project and cannot be attributed: {', '.join(names[:5])}"
            + (" …" if len(names) > 5 else "")
            + (" — they may belong to a different project than the one asked "
               "about" if projects else
               " — nothing in this answer is attributable to a project"))
        if mixed and not cross_project and (sensitive or projects):
            flags.append(
                "unattributable evidence cannot be merged into an answer"
                + (" for a figure, date, status or obligation" if sensitive else "")
                + " — asking which project instead")
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

    # ---- the answer's SHAPE must be present in the evidence (§39)
    #
    # Round-6 reviewer R6-2. A drawing title block — "SKYPARK TOWERS — PODIUM
    # LANDSCAPE GA — SHEET 3 OF 40" — was returned SUPPORTED, with no flags,
    # for "what is the final claim amount for the Skypark Towers podium?".
    # The F5-1 fix subtracted terms belonging to MANIFEST PROJECT LABELS, and
    # a title block is full of words that are not project labels (the
    # development name, the drawing title, the client, the consultant), so two
    # of them cleared the floor.
    #
    # The machinery to close this already existed and was wired only to the
    # unattributed-evidence path: if the question asks for a figure, a date or
    # a status, then evidence containing none of those cannot support an
    # answer — whoever the project belongs to. This is the same "key on the
    # material" move as F5-5, applied to the other half.
    if query and QUANTITATIVE_INTENT.search(query) and not _sensitive_evidence(kept):
        flags.append(
            "the question asks for a figure or a date, but no retrieved "
            "passage contains one — the evidence identifies documents rather "
            "than answering the question")
        return Verdict("INSUFFICIENT", kept, flags, conflicts)

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
            # admitted by filename alone — the user named the document, but
            # nothing in it met the content floor (round-4 reviewer N4-4)
            or any(f.startswith(NAME_ONLY_FLAG) for f in flags)
            # unattributed evidence must not read as fully supported (§4)
            or any(e for e in kept
                   if not e.get("project") or e.get("project") == "UNKNOWN")):
        status = "PARTIAL"
    return Verdict(status, kept, flags, conflicts)
