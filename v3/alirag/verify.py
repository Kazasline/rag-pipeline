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

# Function words carry no topical signal, in either language this corpus uses.
# Without this list the floor was satisfied by "the", "for", "what", "yang".
STOPWORDS = {
    # English
    "the", "and", "for", "are", "was", "were", "with", "this", "that", "from",
    "have", "has", "had", "not", "but", "you", "your", "our", "their", "its",
    "what", "which", "who", "whom", "when", "where", "why", "how", "all", "any",
    "can", "will", "would", "should", "could", "there", "here", "than", "then",
    "into", "onto", "out", "off", "over", "under", "about", "been", "being",
    "does", "did", "done", "get", "got", "per", "via", "such", "some", "each",
    "more", "most", "other", "only", "own", "same", "too", "very", "just",
    # Malay
    "yang", "dan", "untuk", "adalah", "ialah", "dengan", "ini", "itu", "dari",
    "daripada", "pada", "ada", "tidak", "tak", "atau", "juga", "akan", "boleh",
    "apa", "mana", "siapa", "bila", "kenapa", "mengapa", "bagaimana", "semua",
    "saya", "anda", "kita", "kami", "mereka", "dalam", "oleh", "kepada",
    "sebagai", "telah", "sudah", "masih", "lagi", "sahaja", "cari", "berapa",
    "jumlah", "senarai",
}


def _content_terms(text: str) -> set:
    """Topical terms only: alphanumeric tokens of 3+ chars, minus stopwords.

    Codes like `LAI-003` survive because the pattern keeps internal hyphens.
    """
    toks = re.findall(r"[A-Za-z0-9][\w\-]{2,}", text.lower())
    return {t for t in toks if t not in STOPWORDS}


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
        qterms = _content_terms(query)
        lexical_hit = any(set(e.get("sources", [])) & {"exact", "sparse"}
                          for e in kept)
        best_overlap = 0
        if qterms:
            for e in kept:
                overlap = len(qterms & _content_terms(e.get("text", "")))
                best_overlap = max(best_overlap, overlap)
        # A query with NO content terms ("the", "apa yang ada dalam ini?") has
        # nothing to match on, so dense neighbours cannot be evidence for it
        # either — that must not count as passing the floor.
        enough_overlap = bool(qterms) and best_overlap >= MIN_CONTENT_OVERLAP
        if not lexical_hit and not enough_overlap:
            return Verdict("INSUFFICIENT", kept,
                           [f"no lexical match and only {best_overlap} content "
                            f"term(s) shared with the query (need "
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
    if conflicts or any("cross-project question" in f for f in flags):
        status = "PARTIAL"
    return Verdict(status, kept, flags, conflicts)
