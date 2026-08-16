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


@dataclass
class Verdict:
    status: str                      # SUPPORTED | PARTIAL | INSUFFICIENT
    kept: list = field(default_factory=list)
    flags: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)


def verify(evidence: list[dict], project_hint: str | None = None,
           min_evidence: int = 1, query: str = "") -> Verdict:
    flags: list[str] = []
    conflicts: list[str] = []
    kept = list(evidence)

    if not kept:
        return Verdict("INSUFFICIENT", [], ["no evidence retrieved"], [])

    # ---- relevance floor (§39): dense retrieval ALWAYS returns nearest
    # neighbors, even for a query about nothing in the corpus. If no evidence
    # item came from a lexical leg (exact/sparse = actual term match) AND no
    # item shares a single meaningful query term, the evidence is neighbors,
    # not answers -> INSUFFICIENT. (A cross-language paraphrase normally still
    # shares codes/names/project tokens; if this floor ever misfires, DEEP
    # retrieval with real embeddings is the sanctioned escalation, §16.)
    if query:
        qterms = {w.lower() for w in re.findall(r"[A-Za-z0-9][\w\-]{2,}", query)}
        lexical_hit = any(set(e.get("sources", [])) & {"exact", "sparse"}
                          for e in kept)
        overlap_hit = any(qterms & set(re.findall(r"[a-z0-9][\w\-]{2,}",
                                                  e.get("text", "").lower()))
                          for e in kept) if qterms else True
        if not lexical_hit and not overlap_hit:
            return Verdict("INSUFFICIENT", kept,
                           ["retrieved items are nearest neighbors only — no "
                            "lexical or term-level connection to the query"], [])

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
    elif len(projects) > 1:
        flags.append(f"evidence spans multiple projects: {sorted(projects)} — "
                     "verify the answer does not mix them")

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
    status = "SUPPORTED"
    if conflicts or any("multiple projects" in f for f in flags):
        status = "PARTIAL"
    return Verdict(status, kept, flags, conflicts)
